from dataclasses import dataclass
from typing import Literal


@dataclass
class TradeMessage:
    """Notifier-compatible message wrapper for paper-trading events."""

    message: str


@dataclass
class StrategyTrigger:
    field: Literal["price", "change_pct"]
    op: Literal["above", "below"]
    value: float

    def matches(self, current_value: float) -> bool:
        if self.op == "above":
            return current_value >= self.value
        return current_value <= self.value


@dataclass
class BreakoutPullbackSetup:
    resistance: float
    breakout_buffer_pct: float = 0.0
    pullback_tolerance_pct: float = 1.0
    confirmation_pct: float = 0.0
    max_pullback_bars: int = 8
    invalidation_pct: float = 2.0


@dataclass
class StrategySizing:
    type: Literal["fixed_amount"]
    amount: float
    currency: str | None = None
    lot_size: int | None = None


@dataclass
class StrategyConstraints:
    cooldown_minutes: int = 0
    max_position_amount: float | None = None


@dataclass
class TradingStrategy:
    id: str
    enabled: bool
    symbol: str
    action: Literal["buy", "sell"]
    trigger: StrategyTrigger | None
    sizing: StrategySizing
    constraints: StrategyConstraints
    type: Literal["threshold", "breakout_pullback"] = "threshold"
    breakout_pullback: BreakoutPullbackSetup | None = None


@dataclass
class StrategySignal:
    strategy: TradingStrategy
    symbol: str
    market: str
    name: str
    action: Literal["buy", "sell"]
    trigger_field: str
    trigger_op: str
    trigger_value: float
    current_value: float
    quote_price: float
    quote_timestamp: str
    cooldown_remaining_seconds: float = 0.0


@dataclass
class OrderExecution:
    strategy_id: str
    symbol: str
    market: str
    side: Literal["buy", "sell"]
    status: Literal["FILLED", "REJECTED"]
    quantity: int
    price: float
    amount: float
    currency: str
    reason: str = ""
    remaining_cash: float | None = None