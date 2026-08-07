from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

@dataclass
class Quote:
    symbol: str
    name: str
    market: str  # "A股" | "港股" | "美股"
    price: float
    change_pct: float
    volume: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # --- P0: OHLC fields for intrabar backtesting (daily-bars only) ---
    open: float | None = None
    high: float | None = None
    low: float | None = None

    def to_dict(self) -> dict:
        d = {
            "symbol": self.symbol, "name": self.name, "market": self.market,
            "price": self.price, "change_pct": self.change_pct,
            "volume": self.volume, "timestamp": self.timestamp,
        }
        if self.open is not None:
            d["open"] = self.open
            d["high"] = self.high
            d["low"] = self.low
        return d

@dataclass
class AlertRule:
    field: str          # "price" | "change_pct"
    op: Literal["above", "below"]
    value: float

    def matches(self, current_value: float) -> bool:
        if self.op == "above":
            return current_value >= self.value
        return current_value <= self.value

@dataclass
class Alert:
    symbol: str
    name: str
    rule: AlertRule
    current_value: float
    message: str
