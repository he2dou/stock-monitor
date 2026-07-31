from __future__ import annotations

import time
from datetime import datetime, timezone
from src.models import Quote
from src.trading_models import (
    BreakoutPullbackSetup,
    LeveragedBreakoutPullbackSetup,
    OrderExecution,
    StrategyConstraints,
    StrategySignal,
    StrategySizing,
    StrategyTrigger,
    TradingStrategy,
)

VALID_ACTIONS = {"buy", "sell"}
VALID_FIELDS = {"price", "change_pct"}
VALID_OPS = {"above", "below"}
VALID_TYPES = {"threshold", "breakout_pullback", "leveraged_breakout_pullback"}
VALID_SIZING_TYPES = {"fixed_amount", "risk_percent"}


class StrategyConfigError(Exception):
    pass


def parse_strategy(raw: dict) -> TradingStrategy:
    if "id" not in raw or not raw["id"]:
        raise StrategyConfigError("Strategy missing id")
    strategy_type = raw.get("type", "threshold")
    if strategy_type not in VALID_TYPES:
        raise StrategyConfigError(f"Invalid strategy type '{strategy_type}'")

    action = raw.get("action")
    if action not in VALID_ACTIONS:
        raise StrategyConfigError(f"Invalid strategy action '{action}'")
    if strategy_type in {"breakout_pullback", "leveraged_breakout_pullback"} and action != "buy":
        raise StrategyConfigError(f"{strategy_type} strategy only supports buy action")
    if "symbol" not in raw or not raw["symbol"]:
        raise StrategyConfigError(f"Strategy {raw.get('id')} missing symbol")

    trigger = _parse_trigger(raw) if strategy_type == "threshold" else None
    setup = _parse_breakout_pullback(raw) if strategy_type == "breakout_pullback" else None
    leveraged_setup = (
        _parse_leveraged_breakout_pullback(raw)
        if strategy_type == "leveraged_breakout_pullback" else None
    )

    return TradingStrategy(
        id=str(raw["id"]),
        enabled=bool(raw.get("enabled", True)),
        symbol=str(raw["symbol"]),
        action=action,
        trigger=trigger,
        sizing=_parse_sizing(raw),
        constraints=_parse_constraints(raw),
        type=strategy_type,
        breakout_pullback=setup,
        leveraged_breakout_pullback=leveraged_setup,
    )


def _parse_trigger(raw: dict) -> StrategyTrigger:
    trigger_raw = raw.get("trigger") or {}
    field = trigger_raw.get("field")
    op = trigger_raw.get("op")
    if field not in VALID_FIELDS:
        raise StrategyConfigError(f"Invalid trigger field '{field}'")
    if op not in VALID_OPS:
        raise StrategyConfigError(f"Invalid trigger op '{op}'")
    if "value" not in trigger_raw:
        raise StrategyConfigError("Trigger missing value")
    return StrategyTrigger(field=field, op=op, value=float(trigger_raw["value"]))


def _parse_breakout_pullback(raw: dict) -> BreakoutPullbackSetup:
    setup_raw = raw.get("breakout_pullback") or raw.get("setup") or {}
    if "resistance" not in setup_raw:
        raise StrategyConfigError("breakout_pullback missing resistance")
    resistance = float(setup_raw["resistance"])
    if resistance <= 0:
        raise StrategyConfigError("breakout_pullback resistance must be positive")
    max_pullback_bars = int(setup_raw.get("max_pullback_bars", 8) or 8)
    if max_pullback_bars <= 0:
        raise StrategyConfigError("max_pullback_bars must be positive")
    invalidation_pct = float(setup_raw.get("invalidation_pct", 2.0) or 0)
    if invalidation_pct < 0:
        raise StrategyConfigError("invalidation_pct must be non-negative")
    return BreakoutPullbackSetup(
        resistance=resistance,
        breakout_buffer_pct=float(setup_raw.get("breakout_buffer_pct", 0.0) or 0.0),
        pullback_tolerance_pct=float(setup_raw.get("pullback_tolerance_pct", 1.0) or 1.0),
        confirmation_pct=float(setup_raw.get("confirmation_pct", 0.0) or 0.0),
        max_pullback_bars=max_pullback_bars,
        invalidation_pct=invalidation_pct,
    )


def _parse_leveraged_breakout_pullback(raw: dict) -> LeveragedBreakoutPullbackSetup:
    setup_raw = raw.get("leveraged_breakout_pullback") or raw.get("setup") or {}
    lookback_bars = int(setup_raw.get("lookback_bars", 20) or 20)
    trend_short_bars = int(setup_raw.get("trend_short_bars", 20) or 20)
    trend_long_bars = int(setup_raw.get("trend_long_bars", 60) or 60)
    max_pullback_bars = int(setup_raw.get("max_pullback_bars", 12) or 12)
    if lookback_bars <= 1:
        raise StrategyConfigError("lookback_bars must be greater than 1")
    if trend_short_bars <= 0 or trend_long_bars <= 0 or trend_short_bars >= trend_long_bars:
        raise StrategyConfigError("trend_short_bars must be positive and less than trend_long_bars")
    if max_pullback_bars <= 0:
        raise StrategyConfigError("max_pullback_bars must be positive")
    partial_sell_fraction = float(setup_raw.get("partial_sell_fraction", 0.5) or 0.5)
    if not 0 < partial_sell_fraction <= 1:
        raise StrategyConfigError("partial_sell_fraction must be between 0 and 1")
    return LeveragedBreakoutPullbackSetup(
        lookback_bars=lookback_bars,
        breakout_buffer_pct=float(setup_raw.get("breakout_buffer_pct", 0.5) or 0.0),
        pullback_tolerance_pct=float(setup_raw.get("pullback_tolerance_pct", 4.0) or 0.0),
        confirmation_pct=float(setup_raw.get("confirmation_pct", 0.0) or 0.0),
        max_pullback_bars=max_pullback_bars,
        invalidation_pct=float(setup_raw.get("invalidation_pct", 8.0) or 0.0),
        trend_short_bars=trend_short_bars,
        trend_long_bars=trend_long_bars,
        partial_take_profit_r=float(setup_raw.get("partial_take_profit_r", 3.0) or 3.0),
        partial_sell_fraction=partial_sell_fraction,
        trailing_stop_pct=float(setup_raw.get("trailing_stop_pct", 12.0) or 12.0),
    )


def _parse_sizing(raw: dict) -> StrategySizing:
    sizing_raw = raw.get("sizing") or {}
    sizing_type = sizing_raw.get("type", "fixed_amount")
    if sizing_type not in VALID_SIZING_TYPES:
        raise StrategyConfigError(f"Unsupported sizing type '{sizing_type}'")
    amount = float(sizing_raw.get("amount", 0) or 0)
    if amount <= 0:
        raise StrategyConfigError("Sizing amount must be positive")
    lot_size = sizing_raw.get("lot_size")
    if lot_size is not None and int(lot_size) <= 0:
        raise StrategyConfigError("lot_size must be positive")
    return StrategySizing(
        type=sizing_type,
        amount=amount,
        currency=sizing_raw.get("currency"),
        lot_size=int(lot_size) if lot_size is not None else None,
    )


def _parse_constraints(raw: dict) -> StrategyConstraints:
    constraints_raw = raw.get("constraints") or {}
    max_position_amount = constraints_raw.get("max_position_amount")
    return StrategyConstraints(
        cooldown_minutes=int(constraints_raw.get("cooldown_minutes", 0) or 0),
        max_position_amount=(
            float(max_position_amount) if max_position_amount is not None else None
        ),
    )


def parse_strategies(raw: list[dict] | None) -> list[TradingStrategy]:
    return [parse_strategy(item) for item in (raw or [])]


class StrategyEngine:
    def __init__(self, strategies: list[dict] | list[TradingStrategy] | None = None):
        self._last_filled_at: dict[str, float] = {}
        self._breakout_states: dict[str, dict] = {}
        self._leveraged_states: dict[str, dict] = {}
        self.set_strategies(strategies or [])

    def set_strategies(self, strategies: list[dict] | list[TradingStrategy]) -> None:
        parsed: list[TradingStrategy] = []
        for item in strategies:
            parsed.append(item if isinstance(item, TradingStrategy) else parse_strategy(item))
        self.strategies = parsed
        active_breakout_ids = {s.id for s in parsed if s.type == "breakout_pullback"}
        self._breakout_states = {
            strategy_id: state
            for strategy_id, state in self._breakout_states.items()
            if strategy_id in active_breakout_ids
        }
        active_leveraged_ids = {s.id for s in parsed if s.type == "leveraged_breakout_pullback"}
        self._leveraged_states = {
            strategy_id: state
            for strategy_id, state in self._leveraged_states.items()
            if strategy_id in active_leveraged_ids
        }

    def generate_signals(self, quotes: list[Quote]) -> list[StrategySignal]:
        quotes_by_symbol = {q.symbol: q for q in quotes}
        signals: list[StrategySignal] = []
        for strategy in self.strategies:
            if not strategy.enabled:
                continue
            quote = quotes_by_symbol.get(strategy.symbol)
            if quote is None:
                continue
            signal = self._generate_signal(strategy, quote, self._quote_epoch(quote))
            if signal is not None:
                signals.append(signal)
        return signals

    @staticmethod
    def _quote_epoch(quote: Quote) -> float:
        return StrategyEngine._timestamp_epoch(quote.timestamp)

    @staticmethod
    def _signal_epoch(signal: StrategySignal | None) -> float:
        return StrategyEngine._timestamp_epoch(signal.quote_timestamp if signal else "")

    @staticmethod
    def _timestamp_epoch(raw: str) -> float:
        try:
            normalized = (raw or "").replace("Z", "+00:00")
            when = datetime.fromisoformat(normalized)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when.timestamp()
        except ValueError:
            return time.time()
    def _generate_signal(self, strategy: TradingStrategy, quote: Quote,
                         now: float) -> StrategySignal | None:
        if strategy.type == "leveraged_breakout_pullback":
            return self._generate_leveraged_breakout_pullback_signal(strategy, quote, now)
        if strategy.type == "breakout_pullback":
            return self._generate_breakout_pullback_signal(strategy, quote, now)
        return self._generate_threshold_signal(strategy, quote, now)

    def _generate_threshold_signal(self, strategy: TradingStrategy, quote: Quote,
                                   now: float) -> StrategySignal | None:
        if strategy.trigger is None:
            return None
        current_value = getattr(quote, strategy.trigger.field, None)
        if current_value is None or not strategy.trigger.matches(float(current_value)):
            return None
        return self._build_signal(
            strategy=strategy,
            quote=quote,
            now=now,
            action=strategy.action,
            trigger_field=strategy.trigger.field,
            trigger_op=strategy.trigger.op,
            trigger_value=strategy.trigger.value,
            current_value=float(current_value),
        )

    def _generate_breakout_pullback_signal(self, strategy: TradingStrategy, quote: Quote,
                                           now: float) -> StrategySignal | None:
        setup = strategy.breakout_pullback
        if setup is None:
            return None
        state = self._breakout_states.setdefault(strategy.id, {"phase": "waiting_breakout", "bars": 0})
        price = float(quote.price)
        resistance = setup.resistance
        breakout_price = resistance * (1 + setup.breakout_buffer_pct / 100.0)
        support_low = resistance * (1 - setup.pullback_tolerance_pct / 100.0)
        support_high = resistance * (1 + setup.pullback_tolerance_pct / 100.0)
        confirmation_price = resistance * (1 + setup.confirmation_pct / 100.0)
        invalidation_price = resistance * (1 - setup.invalidation_pct / 100.0)

        phase = state.get("phase", "waiting_breakout")
        if phase == "waiting_breakout":
            if price >= breakout_price:
                state.update({"phase": "waiting_pullback", "bars": 0})
            return None

        if price < invalidation_price:
            state.update({"phase": "waiting_breakout", "bars": 0})
            return None

        if phase == "waiting_pullback":
            state["bars"] = int(state.get("bars", 0)) + 1
            if state["bars"] > setup.max_pullback_bars:
                state.update({"phase": "waiting_breakout", "bars": 0})
                return None
            if support_low <= price <= support_high:
                state.update({"phase": "waiting_confirmation", "bars": 0})
            return None

        if phase == "waiting_confirmation":
            if price >= confirmation_price:
                state.update({"phase": "waiting_breakout", "bars": 0})
                return self._build_signal(
                    strategy=strategy,
                    quote=quote,
                    now=now,
                    action="buy",
                    trigger_field="price",
                    trigger_op="breakout_pullback_confirmed",
                    trigger_value=resistance,
                    current_value=price,
                )
            return None

        state.update({"phase": "waiting_breakout", "bars": 0})
        return None

    def _generate_leveraged_breakout_pullback_signal(self, strategy: TradingStrategy,
                                                     quote: Quote, now: float) -> StrategySignal | None:
        setup = strategy.leveraged_breakout_pullback
        if setup is None:
            return None
        state = self._leveraged_states.setdefault(strategy.id, {
            "phase": "waiting_breakout",
            "bars": 0,
            "prices": [],
            "position": None,
        })
        price = float(quote.price)
        prices = state.setdefault("prices", [])
        history_before = list(prices)
        prices.append(price)
        max_history = max(setup.trend_long_bars, setup.lookback_bars) + setup.max_pullback_bars + 5
        if len(prices) > max_history:
            del prices[:-max_history]

        active_position = state.get("position")
        if active_position:
            return self._generate_leveraged_exit_signal(strategy, quote, now, setup, active_position)

        if len(history_before) < max(setup.lookback_bars, setup.trend_long_bars):
            return None
        if not self._trend_allows_entry(history_before + [price], setup):
            state.update({"phase": "waiting_breakout", "bars": 0})
            return None

        resistance = max(history_before[-setup.lookback_bars:])
        breakout_price = resistance * (1 + setup.breakout_buffer_pct / 100.0)
        support_low = resistance * (1 - setup.pullback_tolerance_pct / 100.0)
        support_high = resistance * (1 + setup.pullback_tolerance_pct / 100.0)
        confirmation_price = resistance * (1 + setup.confirmation_pct / 100.0)
        invalidation_price = resistance * (1 - setup.invalidation_pct / 100.0)
        phase = state.get("phase", "waiting_breakout")

        if phase == "waiting_breakout":
            if price >= breakout_price:
                state.update({
                    "phase": "waiting_pullback",
                    "bars": 0,
                    "resistance": resistance,
                    "breakout_price": price,
                })
            return None

        resistance = float(state.get("resistance", resistance))
        invalidation_price = resistance * (1 - setup.invalidation_pct / 100.0)
        support_low = resistance * (1 - setup.pullback_tolerance_pct / 100.0)
        support_high = resistance * (1 + setup.pullback_tolerance_pct / 100.0)
        confirmation_price = resistance * (1 + setup.confirmation_pct / 100.0)

        if price < invalidation_price:
            state.update({"phase": "waiting_breakout", "bars": 0})
            return None

        if phase == "waiting_pullback":
            state["bars"] = int(state.get("bars", 0)) + 1
            if state["bars"] > setup.max_pullback_bars:
                state.update({"phase": "waiting_breakout", "bars": 0})
                return None
            if support_low <= price <= support_high:
                state.update({"phase": "waiting_confirmation", "pullback_low": price})
            return None

        if phase == "waiting_confirmation" and price >= confirmation_price:
            pullback_low = float(state.get("pullback_low", price))
            stop_price = min(pullback_low, invalidation_price)
            risk_per_share = price - stop_price
            if risk_per_share <= 0:
                state.update({"phase": "waiting_breakout", "bars": 0})
                return None
            state.update({"phase": "waiting_breakout", "bars": 0})
            return self._build_signal(
                strategy=strategy,
                quote=quote,
                now=now,
                action="buy",
                trigger_field="price",
                trigger_op="leveraged_breakout_pullback_confirmed",
                trigger_value=resistance,
                current_value=price,
                metadata={
                    "stop_price": stop_price,
                    "risk_per_share": risk_per_share,
                    "resistance": resistance,
                    "setup_type": "leveraged_breakout_pullback",
                },
            )
        return None

    def _trend_allows_entry(self, prices: list[float], setup: LeveragedBreakoutPullbackSetup) -> bool:
        if len(prices) < setup.trend_long_bars:
            return False
        short_ma = sum(prices[-setup.trend_short_bars:]) / setup.trend_short_bars
        long_ma = sum(prices[-setup.trend_long_bars:]) / setup.trend_long_bars
        return short_ma > long_ma

    def _generate_leveraged_exit_signal(self, strategy: TradingStrategy, quote: Quote, now: float,
                                        setup: LeveragedBreakoutPullbackSetup,
                                        active_position: dict) -> StrategySignal | None:
        price = float(quote.price)
        active_position["highest_price"] = max(float(active_position.get("highest_price", price)), price)
        entry_price = float(active_position["entry_price"])
        risk_per_share = float(active_position["risk_per_share"])
        initial_stop = float(active_position["stop_price"])
        partial_target = entry_price + setup.partial_take_profit_r * risk_per_share
        trailing_stop = max(
            initial_stop,
            float(active_position["highest_price"]) * (1 - setup.trailing_stop_pct / 100.0),
        )

        if not active_position.get("partial_taken") and price >= partial_target:
            return self._build_signal(
                strategy=strategy,
                quote=quote,
                now=now,
                action="sell",
                trigger_field="price",
                trigger_op="partial_take_profit",
                trigger_value=partial_target,
                current_value=price,
                metadata={"position_fraction": setup.partial_sell_fraction, "exit_kind": "partial"},
            )
        if price <= trailing_stop:
            return self._build_signal(
                strategy=strategy,
                quote=quote,
                now=now,
                action="sell",
                trigger_field="price",
                trigger_op="trailing_or_initial_stop",
                trigger_value=trailing_stop,
                current_value=price,
                metadata={"position_fraction": 1.0, "exit_kind": "final"},
            )
        return None

    def _build_signal(self, strategy: TradingStrategy, quote: Quote, now: float,
                      action: str, trigger_field: str, trigger_op: str,
                      trigger_value: float, current_value: float,
                      metadata: dict | None = None) -> StrategySignal:
        cooldown_seconds = strategy.constraints.cooldown_minutes * 60
        elapsed = now - self._last_filled_at.get(strategy.id, 0)
        remaining = max(0.0, cooldown_seconds - elapsed) if action == strategy.action else 0.0
        return StrategySignal(
            strategy=strategy,
            symbol=quote.symbol,
            market=quote.market,
            name=quote.name,
            action=action,
            trigger_field=trigger_field,
            trigger_op=trigger_op,
            trigger_value=trigger_value,
            current_value=current_value,
            quote_price=quote.price,
            quote_timestamp=quote.timestamp,
            cooldown_remaining_seconds=remaining,
            metadata=metadata or {},
        )

    def mark_filled(self, strategy_id: str, signal: StrategySignal | None = None,
                    execution: OrderExecution | None = None) -> None:
        self._last_filled_at[strategy_id] = self._signal_epoch(signal) if signal else time.time()
        if signal is None or execution is None or execution.status != "FILLED":
            return
        if signal.strategy.type != "leveraged_breakout_pullback":
            return
        state = self._leveraged_states.setdefault(strategy_id, {"phase": "waiting_breakout", "bars": 0, "prices": []})
        if signal.action == "buy":
            risk_per_share = float(signal.metadata.get("risk_per_share", 0) or 0)
            stop_price = float(signal.metadata.get("stop_price", signal.quote_price) or signal.quote_price)
            state["position"] = {
                "entry_price": signal.quote_price,
                "stop_price": stop_price,
                "risk_per_share": risk_per_share,
                "highest_price": signal.quote_price,
                "partial_taken": False,
            }
        elif signal.action == "sell":
            position = state.get("position")
            if not position:
                return
            if signal.metadata.get("exit_kind") == "partial":
                position["partial_taken"] = True
            else:
                state["position"] = None