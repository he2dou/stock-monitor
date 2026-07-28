from __future__ import annotations

import math
from src.models import Quote
from src.strategy_engine import StrategyEngine
from src.trading_models import OrderExecution, StrategySignal, TradeMessage, TradingStrategy
from src.trading_store import TradingStore

MARKET_CURRENCY = {"A股": "CNY", "港股": "HKD", "美股": "USD"}
DEFAULT_LOT_SIZE = {"A股": 100, "港股": 100, "美股": 1}


class PaperBroker:
    def __init__(self, store: TradingStore):
        self.store = store

    def execute(self, signal: StrategySignal) -> OrderExecution:
        strategy = signal.strategy
        signal_id = self.store.record_signal(signal)
        currency = strategy.sizing.currency or MARKET_CURRENCY.get(signal.market, "CNY")
        lot_size = strategy.sizing.lot_size or DEFAULT_LOT_SIZE.get(signal.market, 1)

        if signal.cooldown_remaining_seconds > 0:
            reason = f"strategy cooldown: {int(signal.cooldown_remaining_seconds)}s remaining"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, signal.action,
                0, signal.quote_price, currency, "REJECTED", reason,
            )
            return self._result(signal, "REJECTED", 0, 0.0, currency, reason)

        quantity = self._quantity_from_fixed_amount(strategy, signal.quote_price, lot_size)
        if quantity <= 0:
            reason = "quantity below minimum lot size"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, signal.action,
                0, signal.quote_price, currency, "REJECTED", reason,
            )
            return self._result(signal, "REJECTED", 0, 0.0, currency, reason)

        if signal.action == "buy":
            return self._buy(signal_id, signal, quantity, currency)
        return self._sell(signal_id, signal, quantity, currency)

    @staticmethod
    def _quantity_from_fixed_amount(strategy: TradingStrategy, price: float, lot_size: int) -> int:
        if price <= 0 or lot_size <= 0:
            return 0
        raw_quantity = int(strategy.sizing.amount // price)
        return int(math.floor(raw_quantity / lot_size) * lot_size)

    def _buy(self, signal_id: str, signal: StrategySignal, quantity: int, currency: str) -> OrderExecution:
        strategy = signal.strategy
        amount = quantity * signal.quote_price
        cash = self.store.get_balance(currency)
        position = self.store.get_position(signal.market, signal.symbol)
        current_position_value = int(position["quantity"]) * signal.quote_price
        max_amount = strategy.constraints.max_position_amount
        if max_amount is not None and current_position_value + amount > max_amount:
            reason = "max position amount exceeded"
            self.store.record_order(signal_id, strategy.id, signal.symbol, signal.market, "buy",
                                    quantity, signal.quote_price, currency, "REJECTED", reason)
            return self._result(signal, "REJECTED", quantity, amount, currency, reason, cash)
        if cash < amount:
            reason = "insufficient cash"
            self.store.record_order(signal_id, strategy.id, signal.symbol, signal.market, "buy",
                                    quantity, signal.quote_price, currency, "REJECTED", reason)
            return self._result(signal, "REJECTED", quantity, amount, currency, reason, cash)

        order_id = self.store.record_order(signal_id, strategy.id, signal.symbol, signal.market, "buy",
                                           quantity, signal.quote_price, currency, "FILLED")
        self.store.record_fill(order_id, signal.quote_price, quantity)
        new_cash = cash - amount
        self.store.update_balance(currency, new_cash)

        old_qty = int(position["quantity"])
        old_cost = float(position["avg_cost"])
        new_qty = old_qty + quantity
        new_avg_cost = ((old_qty * old_cost) + amount) / new_qty if new_qty else 0.0
        self.store.upsert_position(
            signal.market, signal.symbol, signal.name, currency, new_qty,
            new_avg_cost, float(position["realized_pnl"]),
        )
        return self._result(signal, "FILLED", quantity, amount, currency, "", new_cash)

    def _sell(self, signal_id: str, signal: StrategySignal, quantity: int, currency: str) -> OrderExecution:
        strategy = signal.strategy
        amount = quantity * signal.quote_price
        position = self.store.get_position(signal.market, signal.symbol)
        held_qty = int(position["quantity"])
        cash = self.store.get_balance(currency)
        if held_qty < quantity:
            reason = "insufficient position quantity"
            self.store.record_order(signal_id, strategy.id, signal.symbol, signal.market, "sell",
                                    quantity, signal.quote_price, currency, "REJECTED", reason)
            return self._result(signal, "REJECTED", quantity, amount, currency, reason, cash)

        order_id = self.store.record_order(signal_id, strategy.id, signal.symbol, signal.market, "sell",
                                           quantity, signal.quote_price, currency, "FILLED")
        self.store.record_fill(order_id, signal.quote_price, quantity)
        new_cash = cash + amount
        self.store.update_balance(currency, new_cash)

        avg_cost = float(position["avg_cost"])
        realized = float(position["realized_pnl"]) + (signal.quote_price - avg_cost) * quantity
        new_qty = held_qty - quantity
        new_avg_cost = avg_cost if new_qty else 0.0
        self.store.upsert_position(
            signal.market, signal.symbol, signal.name, currency, new_qty, new_avg_cost, realized,
        )
        return self._result(signal, "FILLED", quantity, amount, currency, "", new_cash)

    @staticmethod
    def _result(signal: StrategySignal, status: str, quantity: int, amount: float,
                currency: str, reason: str = "", remaining_cash: float | None = None) -> OrderExecution:
        return OrderExecution(
            strategy_id=signal.strategy.id,
            symbol=signal.symbol,
            market=signal.market,
            side=signal.action,
            status=status,
            quantity=quantity,
            price=signal.quote_price,
            amount=amount,
            currency=currency,
            reason=reason,
            remaining_cash=remaining_cash,
        )


class PaperTradingService:
    def __init__(self, store: TradingStore, strategy_engine: StrategyEngine,
                 enabled: bool = True, quote_history_enabled: bool = True):
        self.store = store
        self.strategy_engine = strategy_engine
        self.enabled = enabled
        self.quote_history_enabled = quote_history_enabled
        self.broker = PaperBroker(store)

    def set_strategies(self, strategies: list[dict]) -> None:
        self.strategy_engine.set_strategies(strategies)

    def apply_config(self, config: dict) -> None:
        paper = config.get("paper_trading", {}) if config else {}
        self.enabled = bool(paper.get("enabled", self.enabled))
        self.quote_history_enabled = bool(
            paper.get("quote_history_enabled", self.quote_history_enabled)
        )
        accounts = paper.get("accounts") or {}
        if accounts:
            self.store.ensure_accounts(accounts)

    def process(self, quotes: list[Quote]) -> list[TradeMessage]:
        if self.quote_history_enabled:
            self.store.save_quote_snapshots(quotes)
        if not self.enabled:
            return []
        messages: list[TradeMessage] = []
        for signal in self.strategy_engine.generate_signals(quotes):
            execution = self.broker.execute(signal)
            if execution.status == "FILLED":
                self.strategy_engine.mark_filled(signal.strategy.id)
            messages.append(TradeMessage(self._format_message(signal, execution)))
        return messages

    @staticmethod
    def _format_message(signal: StrategySignal, execution: OrderExecution) -> str:
        trigger = (
            f"{signal.trigger_field} {signal.strategy.trigger.op} {signal.trigger_value} "
            f"(当前 {signal.current_value:.4f})"
        )
        base = (
            f"策略 {execution.strategy_id} | {execution.side.upper()} {signal.name}({signal.symbol}) | "
            f"触发: {trigger} | 状态: {execution.status}"
        )
        if execution.status == "FILLED":
            cash = "" if execution.remaining_cash is None else f" | 剩余现金: {execution.remaining_cash:.2f} {execution.currency}"
            return (
                f"{base} | 成交价: {execution.price:.4f} | 数量: {execution.quantity} | "
                f"金额: {execution.amount:.2f} {execution.currency}{cash}"
            )
        return f"{base} | 拒单原因: {execution.reason}"
