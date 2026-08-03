from dataclasses import dataclass, field
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
class LeveragedBreakoutPullbackSetup:
    lookback_bars: int = 20
    breakout_buffer_pct: float = 0.5
    pullback_tolerance_pct: float = 4.0
    confirmation_pct: float = 0.0
    max_pullback_bars: int = 12
    invalidation_pct: float = 8.0
    trend_short_bars: int = 20
    trend_long_bars: int = 60
    partial_take_profit_r: float = 3.0
    partial_sell_fraction: float = 0.5
    trailing_stop_pct: float = 12.0
    # --- 时间尺度标注(P0-2)。仅用于校验回测数据频率是否与参数语义匹配，不参与信号逻辑 ---
    bar_timeframe: str = "unknown"
    # --- 更鲁棒的压力位(P1-8)。100.0 = 取 max(旧行为)；< 100 则用百分位忽略单根尖峰 ---
    resistance_percentile: float = 100.0
    # --- 连续收盘确认(P1-9)。1 = 旧行为(站上即触发)；>1 需连续 N 根收盘站上压力位 ---
    require_close_above_resistance_bars: int = 1
    # --- 分级移动止损(P1-5)。部分止盈后用更紧的止损；None = 保持原 trailing_stop_pct ---
    trailing_stop_pct_after_partial: float | None = None
    # --- 时间止损(P1-5)。持仓 N 根 bar 未到 1R 浮盈则平仓；None = 关闭 ---
    time_stop_bars: int | None = None
    # --- 止损离场后冷却(P1-10)。分钟，0 = 不冷却 ---
    cooldown_after_stop_minutes: int = 0
    # --- 成交量确认(P1-7)。突破 bar 量需 > 近 N 日均量 × mult ---
    require_volume_confirm: bool = False
    volume_confirm_mult: float = 1.5
    volume_avg_period: int = 20
    # --- RSI 过滤(P1-7)。入场前 RSI 高于此值则不进场(防追高)；None = 不过滤 ---
    rsi_period: int | None = None
    rsi_max: float | None = None


@dataclass
class StrategySizing:
    type: Literal["fixed_amount", "risk_percent"]
    amount: float
    currency: str | None = None
    lot_size: int | None = None
    # --- 仓位基数(P0-1)。"cash" = 旧行为(仅现金)；"equity" = 现金 + 持仓市值，可复利 ---
    equity_basis: Literal["cash", "equity"] = "cash"
    # --- 最小仓位下限(P0-1)。仓位占基数最低百分比；None = 无下限 ---
    min_position_value_pct: float | None = None


@dataclass
class StrategyConstraints:
    cooldown_minutes: int = 0
    max_position_amount: float | None = None


@dataclass
class TradingCosts:
    """交易成本(手续费/滑点)。用于回测更贴近真实成交。"""
    commission_bps: float = 0.0    # 单边佣金(bps)
    slippage_bps: float = 0.0      # 单边滑点(bps)


@dataclass
class TradingStrategy:
    id: str
    enabled: bool
    symbol: str
    action: Literal["buy", "sell"]
    trigger: StrategyTrigger | None
    sizing: StrategySizing
    constraints: StrategyConstraints
    type: Literal["threshold", "breakout_pullback", "leveraged_breakout_pullback"] = "threshold"
    breakout_pullback: BreakoutPullbackSetup | None = None
    leveraged_breakout_pullback: LeveragedBreakoutPullbackSetup | None = None
    costs: TradingCosts | None = None


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
    metadata: dict = field(default_factory=dict)


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
    commission: float = 0.0       # 单边佣金(成本应用后)
    slippage: float = 0.0         # 成交价相对信号价的偏移(滑点)