from __future__ import annotations

import time
from src.models import Quote
from src.trading_models import (
    StrategyConstraints,
    StrategySignal,
    StrategySizing,
    StrategyTrigger,
    TradingStrategy,
)

VALID_ACTIONS = {"buy", "sell"}
VALID_FIELDS = {"price", "change_pct"}
VALID_OPS = {"above", "below"}


class StrategyConfigError(Exception):
    pass


def parse_strategy(raw: dict) -> TradingStrategy:
    if "id" not in raw or not raw["id"]:
        raise StrategyConfigError("Strategy missing id")
    action = raw.get("action")
    if action not in VALID_ACTIONS:
        raise StrategyConfigError(f"Invalid strategy action '{action}'")
    if "symbol" not in raw or not raw["symbol"]:
        raise StrategyConfigError(f"Strategy {raw.get('id')} missing symbol")

    trigger_raw = raw.get("trigger") or {}
    field = trigger_raw.get("field")
    op = trigger_raw.get("op")
    if field not in VALID_FIELDS:
        raise StrategyConfigError(f"Invalid trigger field '{field}'")
    if op not in VALID_OPS:
        raise StrategyConfigError(f"Invalid trigger op '{op}'")
    if "value" not in trigger_raw:
        raise StrategyConfigError("Trigger missing value")

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

    constraints_raw = raw.get("constraints") or {}
    max_position_amount = constraints_raw.get("max_position_amount")
    return TradingStrategy(
        id=str(raw["id"]),
        enabled=bool(raw.get("enabled", True)),
        symbol=str(raw["symbol"]),
        action=action,
        trigger=StrategyTrigger(
            field=field,
            op=op,
            value=float(trigger_raw["value"]),
        ),
        sizing=StrategySizing(
            type="fixed_amount",
            amount=amount,
            currency=sizing_raw.get("currency"),
            lot_size=int(lot_size) if lot_size is not None else None,
        ),
        constraints=StrategyConstraints(
            cooldown_minutes=int(constraints_raw.get("cooldown_minutes", 0) or 0),
            max_position_amount=(
                float(max_position_amount) if max_position_amount is not None else None
            ),
        ),
    )


def parse_strategies(raw: list[dict] | None) -> list[TradingStrategy]:
    return [parse_strategy(item) for item in (raw or [])]


class StrategyEngine:
    def __init__(self, strategies: list[dict] | list[TradingStrategy] | None = None):
        self._last_filled_at: dict[str, float] = {}
        self.set_strategies(strategies or [])

    def set_strategies(self, strategies: list[dict] | list[TradingStrategy]) -> None:
        parsed: list[TradingStrategy] = []
        for item in strategies:
            parsed.append(item if isinstance(item, TradingStrategy) else parse_strategy(item))
        self.strategies = parsed

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
            current_value = getattr(quote, strategy.trigger.field, None)
            if current_value is None or not strategy.trigger.matches(float(current_value)):
                continue
            cooldown_seconds = strategy.constraints.cooldown_minutes * 60
            elapsed = now - self._last_filled_at.get(strategy.id, 0)
            remaining = max(0.0, cooldown_seconds - elapsed)
            signals.append(StrategySignal(
                strategy=strategy,
                symbol=quote.symbol,
                market=quote.market,
                name=quote.name,
                action=strategy.action,
                trigger_field=strategy.trigger.field,
                trigger_value=strategy.trigger.value,
                current_value=float(current_value),
                quote_price=quote.price,
                quote_timestamp=quote.timestamp,
                cooldown_remaining_seconds=remaining,
            ))
        return signals

    def mark_filled(self, strategy_id: str) -> None:
        self._last_filled_at[strategy_id] = time.time()
