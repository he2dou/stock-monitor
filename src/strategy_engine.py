from __future__ import annotations

import time
from src.models import Quote
from src.trading_models import (
    BreakoutPullbackSetup,
    StrategyConstraints,
    StrategySignal,
    StrategySizing,
    StrategyTrigger,
    TradingStrategy,
)

VALID_ACTIONS = {"buy", "sell"}
VALID_FIELDS = {"price", "change_pct"}
VALID_OPS = {"above", "below"}
VALID_TYPES = {"threshold", "breakout_pullback"}


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
    if strategy_type == "breakout_pullback" and action != "buy":
        raise StrategyConfigError("breakout_pullback strategy only supports buy action")
    if "symbol" not in raw or not raw["symbol"]:
        raise StrategyConfigError(f"Strategy {raw.get('id')} missing symbol")

    trigger = _parse_trigger(raw) if strategy_type == "threshold" else None
    setup = _parse_breakout_pullback(raw) if strategy_type == "breakout_pullback" else None
    sizing = _parse_sizing(raw)
    constraints = _parse_constraints(raw)

    return TradingStrategy(
        id=str(raw["id"]),
        enabled=bool(raw.get("enabled", True)),
        symbol=str(raw["symbol"]),
        action=action,
        trigger=trigger,
        sizing=sizing,
        constraints=constraints,
        type=strategy_type,
        breakout_pullback=setup,
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


def _parse_sizing(raw: dict) -> StrategySizing:
    sizing_raw = raw.get("sizing") or {}
    sizing_type = sizing_raw.get("type", "fixed_amount")
    if sizing_type != "fixed_amount":
        raise StrategyConfigError(f"Unsupported sizing type '{sizing_type}'")
    amount = float(sizing_raw.get("amount", 0) or 0)
    if amount <= 0:
        raise StrategyConfigError("Sizing amount must be positive")
    lot_size = sizing_raw.get("lot_size")
    if lot_size is not None and int(lot_size) <= 0:
        raise StrategyConfigError("lot_size must be positive")
    return StrategySizing(
        type="fixed_amount",
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

    def generate_signals(self, quotes: list[Quote]) -> list[StrategySignal]:
        quotes_by_symbol = {q.symbol: q for q in quotes}
        now = time.time()
        signals: list[StrategySignal] = []
        for strategy in self.strategies:
            if not strategy.enabled:
                continue
            quote = quotes_by_symbol.get(strategy.symbol)
            if quote is None:
                continue
            signal = self._generate_signal(strategy, quote, now)
            if signal is not None:
                signals.append(signal)
        return signals

    def _generate_signal(self, strategy: TradingStrategy, quote: Quote,
                         now: float) -> StrategySignal | None:
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
                    trigger_field="price",
                    trigger_op="breakout_pullback_confirmed",
                    trigger_value=resistance,
                    current_value=price,
                )
            return None

        state.update({"phase": "waiting_breakout", "bars": 0})
        return None

    def _build_signal(self, strategy: TradingStrategy, quote: Quote, now: float,
                      trigger_field: str, trigger_op: str, trigger_value: float,
                      current_value: float) -> StrategySignal:
        cooldown_seconds = strategy.constraints.cooldown_minutes * 60
        elapsed = now - self._last_filled_at.get(strategy.id, 0)
        remaining = max(0.0, cooldown_seconds - elapsed)
        return StrategySignal(
            strategy=strategy,
            symbol=quote.symbol,
            market=quote.market,
            name=quote.name,
            action=strategy.action,
            trigger_field=trigger_field,
            trigger_op=trigger_op,
            trigger_value=trigger_value,
            current_value=current_value,
            quote_price=quote.price,
            quote_timestamp=quote.timestamp,
            cooldown_remaining_seconds=remaining,
        )

    def mark_filled(self, strategy_id: str) -> None:
        self._last_filled_at[strategy_id] = time.time()