from __future__ import annotations

import math
from datetime import datetime, timezone

from src.index_snapshots import market_snapshot_date
from src.models import Quote
from src.strategy_engine import StrategyEngine
from src.trading_models import OrderExecution, StrategySignal, TradeMessage, TradingStrategy, TradingCosts
from src.trading_store import TradingStore

MARKET_CURRENCY = {"A股": "CNY", "港股": "HKD", "美股": "USD"}
DEFAULT_LOT_SIZE = {"A股": 100, "港股": 100, "美股": 1}


class PaperBroker:
    def __init__(self, store: TradingStore, costs: TradingCosts | None = None):
        self.store = store
        self.costs = costs

    def execute(self, signal: StrategySignal) -> OrderExecution:
        strategy = signal.strategy
        trading_day = signal_trading_day(signal)
        currency = strategy.sizing.currency or MARKET_CURRENCY.get(signal.market, "CNY")
        lot_size = strategy.sizing.lot_size or DEFAULT_LOT_SIZE.get(signal.market, 1)
        signal_key = signal_daily_key(signal)

        if self.store.has_order_for_signal_on_day(
            strategy.id, signal.symbol, signal.action, trading_day, signal_key
        ):
            reason = f"daily signal already ordered for {trading_day}"
            return self._result(signal, "REJECTED", 0, 0.0, currency, reason)

        signal_id = self.store.record_signal(signal)

        if signal.cooldown_remaining_seconds > 0:
            reason = f"strategy cooldown: {int(signal.cooldown_remaining_seconds)}s remaining"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, signal.action,
                0, signal.quote_price, currency, "REJECTED", reason, trading_day, signal_key,
            )
            return self._result(signal, "REJECTED", 0, 0.0, currency, reason)

        quantity = self._quantity_from_signal(signal, currency, lot_size)
        if quantity <= 0:
            reason = "quantity below minimum lot size"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, signal.action,
                0, signal.quote_price, currency, "REJECTED", reason, trading_day, signal_key,
            )
            return self._result(signal, "REJECTED", 0, 0.0, currency, reason)

        if signal.action == "buy":
            return self._buy(signal_id, signal, quantity, currency, trading_day, signal_key)
        return self._sell(signal_id, signal, quantity, currency, trading_day, signal_key)

    def _quantity_from_signal(self, signal: StrategySignal, currency: str, lot_size: int) -> int:
        strategy = signal.strategy
        price = signal.quote_price
        if price <= 0 or lot_size <= 0:
            return 0
        if signal.action == "sell" and "position_fraction" in signal.metadata:
            held_qty = int(self.store.get_position(signal.market, signal.symbol)["quantity"])
            fraction = float(signal.metadata.get("position_fraction", 1.0) or 1.0)
            raw_quantity = int(math.floor(held_qty * min(max(fraction, 0.0), 1.0)))
            return int(math.floor(raw_quantity / lot_size) * lot_size)
        if strategy.sizing.type == "risk_percent" and signal.action == "buy":
            stop_price = float(signal.metadata.get("stop_price", price) or price)
            risk_per_share = price - stop_price
            if risk_per_share <= 0:
                return 0
            cash = self.store.get_balance(currency)
            # 风险基数: equity = 现金 + 持仓市值; cash = 仅现金(旧行为)
            if strategy.sizing.equity_basis == "equity":
                position = self.store.get_position(signal.market, signal.symbol)
                position_value = int(position["quantity"]) * price
                base = cash + position_value
            else:
                base = cash
            risk_cash = base * float(strategy.sizing.amount) / 100.0
            risk_quantity = int(risk_cash // risk_per_share)
            # 最小仓位下限: 占基数最低比例,避免宽止损时仓位缩到接近0
            min_pct = strategy.sizing.min_position_value_pct
            if min_pct is not None and min_pct > 0:
                min_value = base * min_pct / 100.0
                min_quantity = int(min_value // price)
                risk_quantity = max(risk_quantity, min_quantity)
            cash_quantity = int(cash // price)
            raw_quantity = min(risk_quantity, cash_quantity)
            return int(math.floor(raw_quantity / lot_size) * lot_size)
        return self._quantity_from_fixed_amount(strategy, price, lot_size)

    @staticmethod
    def _quantity_from_fixed_amount(strategy: TradingStrategy, price: float, lot_size: int) -> int:
        if price <= 0 or lot_size <= 0:
            return 0
        raw_quantity = int(strategy.sizing.amount // price)
        return int(math.floor(raw_quantity / lot_size) * lot_size)

    def _fill_price(self, signal: StrategySignal) -> float:
        """实际成交价 = 信号价 + 滑点(买入上浮,卖出下浮)。无 costs 时回退为信号价。"""
        price = signal.quote_price
        if self.costs is None or self.costs.slippage_bps <= 0:
            return price
        if signal.action == "buy":
            return price * (1 + self.costs.slippage_bps / 10000.0)
        return price * (1 - self.costs.slippage_bps / 10000.0)

    def _commission(self, amount: float) -> float:
        """单边佣金金额。"""
        if self.costs is None or self.costs.commission_bps <= 0:
            return 0.0
        return amount * self.costs.commission_bps / 10000.0

    def _buy(self, signal_id: str, signal: StrategySignal, quantity: int,
             currency: str, trading_day: str, signal_key: str) -> OrderExecution:
        strategy = signal.strategy
        fill_price = self._fill_price(signal)
        amount = quantity * fill_price
        commission = self._commission(amount)
        cash = self.store.get_balance(currency)
        position = self.store.get_position(signal.market, signal.symbol)
        current_position_value = int(position["quantity"]) * fill_price
        max_amount = strategy.constraints.max_position_amount
        if max_amount is not None and current_position_value + amount > max_amount:
            reason = "max position amount exceeded"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, "buy",
                quantity, fill_price, currency, "REJECTED", reason, trading_day, signal_key,
            )
            return self._result(signal, "REJECTED", quantity, amount, currency, reason, cash)
        if cash < amount + commission:
            reason = "insufficient cash"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, "buy",
                quantity, fill_price, currency, "REJECTED", reason, trading_day, signal_key,
            )
            return self._result(signal, "REJECTED", quantity, amount, currency, reason, cash)

        order_id = self.store.record_order(
            signal_id, strategy.id, signal.symbol, signal.market, "buy",
            quantity, fill_price, currency, "FILLED", "", trading_day, signal_key,
        )
        self.store.record_fill(order_id, fill_price, quantity)
        new_cash = cash - amount - commission
        self.store.update_balance(currency, new_cash)

        old_qty = int(position["quantity"])
        old_cost = float(position["avg_cost"])
        new_qty = old_qty + quantity
        new_avg_cost = ((old_qty * old_cost) + amount) / new_qty if new_qty else 0.0
        self.store.upsert_position(
            signal.market, signal.symbol, signal.name, currency, new_qty,
            new_avg_cost, float(position["realized_pnl"]),
        )
        return self._buy_result(signal, quantity, fill_price, amount, commission, currency, new_cash)

    def _sell(self, signal_id: str, signal: StrategySignal, quantity: int,
              currency: str, trading_day: str, signal_key: str) -> OrderExecution:
        strategy = signal.strategy
        fill_price = self._fill_price(signal)
        amount = quantity * fill_price
        commission = self._commission(amount)
        position = self.store.get_position(signal.market, signal.symbol)
        held_qty = int(position["quantity"])
        cash = self.store.get_balance(currency)
        if held_qty < quantity:
            reason = "insufficient position quantity"
            self.store.record_order(
                signal_id, strategy.id, signal.symbol, signal.market, "sell",
                quantity, fill_price, currency, "REJECTED", reason, trading_day, signal_key,
            )
            return self._result(signal, "REJECTED", quantity, amount, currency, reason, cash)

        order_id = self.store.record_order(
            signal_id, strategy.id, signal.symbol, signal.market, "sell",
            quantity, fill_price, currency, "FILLED", "", trading_day, signal_key,
        )
        self.store.record_fill(order_id, fill_price, quantity)
        new_cash = cash + amount - commission
        self.store.update_balance(currency, new_cash)

        avg_cost = float(position["avg_cost"])
        realized = float(position["realized_pnl"]) + (fill_price - avg_cost) * quantity
        new_qty = held_qty - quantity
        new_avg_cost = avg_cost if new_qty else 0.0
        self.store.upsert_position(
            signal.market, signal.symbol, signal.name, currency, new_qty, new_avg_cost, realized,
        )
        return self._sell_result(signal, quantity, fill_price, amount, commission, currency, new_cash)

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

    def _buy_result(self, signal: StrategySignal, quantity: int, fill_price: float,
                    amount: float, commission: float, currency: str,
                    remaining_cash: float) -> OrderExecution:
        return self._filled_result(signal, quantity, fill_price, amount, commission,
                                   currency, remaining_cash)

    def _sell_result(self, signal: StrategySignal, quantity: int, fill_price: float,
                     amount: float, commission: float, currency: str,
                     remaining_cash: float) -> OrderExecution:
        return self._filled_result(signal, quantity, fill_price, amount, commission,
                                   currency, remaining_cash)

    @staticmethod
    def _filled_result(signal: StrategySignal, quantity: int, fill_price: float,
                       amount: float, commission: float, currency: str,
                       remaining_cash: float) -> OrderExecution:
        return OrderExecution(
            strategy_id=signal.strategy.id,
            symbol=signal.symbol,
            market=signal.market,
            side=signal.action,
            status="FILLED",
            quantity=quantity,
            price=fill_price,
            amount=amount,
            currency=currency,
            reason="",
            remaining_cash=remaining_cash,
            commission=commission,
            slippage=fill_price - signal.quote_price,
        )


def signal_trading_day(signal: StrategySignal) -> str:
    raw = signal.quote_timestamp or ""
    try:
        normalized = raw.replace("Z", "+00:00")
        when = datetime.fromisoformat(normalized)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        when = datetime.now(timezone.utc)
    return market_snapshot_date(signal.market, when)


def signal_daily_key(signal: StrategySignal) -> str:
    exit_kind = str(signal.metadata.get("exit_kind", ""))
    return "|".join([
        signal.trigger_field,
        signal.trigger_op,
        f"{float(signal.trigger_value):.8f}",
        exit_kind,
    ])

def execute_manual_order(
    store: TradingStore,
    *,
    side: str,
    market: str,
    symbol: str,
    name: str,
    quantity: int,
    price: float,
    currency: str,
) -> OrderExecution:
    """Execute an immediate manual paper-trading order."""
    side = side.strip().lower()
    symbol = symbol.strip()
    name = name.strip() or symbol
    currency = currency.strip().upper() or MARKET_CURRENCY.get(market, "CNY")
    quantity = int(quantity)
    price = float(price)
    amount = quantity * price

    position = store.get_position(market, symbol)
    position_currency = str(position.get("currency") or "").strip().upper()
    if position_currency:
        currency = position_currency

    if side == "buy":
        cash = store.get_balance(currency)
        if cash < amount:
            reason = "insufficient cash"
            store.record_order(
                "", "manual", symbol, market, "buy", quantity, price,
                currency, "REJECTED", reason,
            )
            return OrderExecution(
                "manual", symbol, market, "buy", "REJECTED", quantity, price,
                amount, currency, reason=reason, remaining_cash=cash,
            )

        order_id = store.record_order(
            "", "manual", symbol, market, "buy", quantity, price,
            currency, "FILLED",
        )
        store.record_fill(order_id, price, quantity)
        new_cash = cash - amount
        store.update_balance(currency, new_cash)

        old_qty = int(position["quantity"])
        old_cost = float(position["avg_cost"])
        new_qty = old_qty + quantity
        new_avg_cost = ((old_qty * old_cost) + amount) / new_qty if new_qty else 0.0
        store.upsert_position(
            market, symbol, name, currency, new_qty,
            new_avg_cost, float(position["realized_pnl"]),
        )
        return OrderExecution(
            "manual", symbol, market, "buy", "FILLED", quantity, price,
            amount, currency, remaining_cash=new_cash,
        )

    cash = store.get_balance(currency)
    held_qty = int(position["quantity"])
    if held_qty < quantity:
        reason = "insufficient position quantity"
        store.record_order(
            "", "manual", symbol, market, "sell", quantity, price,
            currency, "REJECTED", reason,
        )
        return OrderExecution(
            "manual", symbol, market, "sell", "REJECTED", quantity, price,
            amount, currency, reason=reason, remaining_cash=cash,
        )

    order_id = store.record_order(
        "", "manual", symbol, market, "sell", quantity, price,
        currency, "FILLED",
    )
    store.record_fill(order_id, price, quantity)
    new_cash = cash + amount
    store.update_balance(currency, new_cash)

    avg_cost = float(position["avg_cost"])
    realized = float(position["realized_pnl"]) + (price - avg_cost) * quantity
    new_qty = held_qty - quantity
    store.upsert_position(
        market, symbol, name or str(position["name"]), currency, new_qty,
        avg_cost if new_qty else 0.0, realized,
    )
    return OrderExecution(
        "manual", symbol, market, "sell", "FILLED", quantity, price,
        amount, currency, remaining_cash=new_cash,
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
                self.strategy_engine.mark_filled(signal.strategy.id, signal, execution)
            messages.append(TradeMessage(self._format_message(signal, execution)))
        return messages

    @staticmethod
    def _format_message(signal: StrategySignal, execution: OrderExecution) -> str:
        trigger = (
            f"{signal.trigger_field} {signal.trigger_op} {signal.trigger_value} "
            f"(当前 {signal.current_value:.4f})"
        )
        lines = [
            "**PAPER TRADE**",
            f"- 策略: `{execution.strategy_id}`",
            f"- 方向: {execution.side.upper()}",
            f"- 股票: {signal.name}({signal.symbol})",
            f"- 触发: `{trigger}`",
            f"- 状态: **{execution.status}**",
        ]
        if execution.status == "FILLED":
            lines.extend([
                f"- 成交价: {execution.price:.4f}",
                f"- 数量: {execution.quantity}",
                f"- 金额: {execution.amount:.2f} {execution.currency}",
            ])
            if execution.remaining_cash is not None:
                lines.append(f"- 剩余现金: {execution.remaining_cash:.2f} {execution.currency}")
        else:
            lines.append(f"- 拒单原因: {execution.reason}")
        return "\n".join(lines)
