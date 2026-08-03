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
    TradingCosts,
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
        costs=_parse_costs(raw),
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

    # --- 新增字段解析与校验 ---
    bar_timeframe = str(setup_raw.get("bar_timeframe", "unknown") or "unknown")
    resistance_percentile = float(setup_raw.get("resistance_percentile", 100.0) or 100.0)
    if not 0 < resistance_percentile <= 100:
        raise StrategyConfigError("resistance_percentile must be in (0, 100]")
    require_close_above = int(setup_raw.get("require_close_above_resistance_bars", 1) or 1)
    if require_close_above < 1:
        raise StrategyConfigError("require_close_above_resistance_bars must be >= 1")
    trailing_after_partial_raw = setup_raw.get("trailing_stop_pct_after_partial")
    trailing_stop_pct_after_partial = (
        float(trailing_after_partial_raw) if trailing_after_partial_raw is not None else None
    )
    if (trailing_stop_pct_after_partial is not None
            and trailing_stop_pct_after_partial <= 0):
        raise StrategyConfigError("trailing_stop_pct_after_partial must be positive")
    time_stop_bars_raw = setup_raw.get("time_stop_bars")
    time_stop_bars = int(time_stop_bars_raw) if time_stop_bars_raw is not None else None
    if time_stop_bars is not None and time_stop_bars <= 0:
        raise StrategyConfigError("time_stop_bars must be positive")
    cooldown_after_stop_minutes = int(setup_raw.get("cooldown_after_stop_minutes", 0) or 0)
    if cooldown_after_stop_minutes < 0:
        raise StrategyConfigError("cooldown_after_stop_minutes must be non-negative")

    require_volume_confirm = bool(setup_raw.get("require_volume_confirm", False) or False)
    volume_confirm_mult = float(setup_raw.get("volume_confirm_mult", 1.5) or 1.5)
    if volume_confirm_mult <= 0:
        raise StrategyConfigError("volume_confirm_mult must be positive")
    volume_avg_period = int(setup_raw.get("volume_avg_period", 20) or 20)
    if volume_avg_period <= 0:
        raise StrategyConfigError("volume_avg_period must be positive")

    rsi_period_raw = setup_raw.get("rsi_period")
    rsi_period = int(rsi_period_raw) if rsi_period_raw is not None else None
    if rsi_period is not None and rsi_period <= 0:
        raise StrategyConfigError("rsi_period must be positive")
    rsi_max_raw = setup_raw.get("rsi_max")
    rsi_max = float(rsi_max_raw) if rsi_max_raw is not None else None
    if rsi_max is not None and not 0 < rsi_max < 100:
        raise StrategyConfigError("rsi_max must be in (0, 100)")

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
        bar_timeframe=bar_timeframe,
        resistance_percentile=resistance_percentile,
        require_close_above_resistance_bars=require_close_above,
        trailing_stop_pct_after_partial=trailing_stop_pct_after_partial,
        time_stop_bars=time_stop_bars,
        cooldown_after_stop_minutes=cooldown_after_stop_minutes,
        require_volume_confirm=require_volume_confirm,
        volume_confirm_mult=volume_confirm_mult,
        volume_avg_period=volume_avg_period,
        rsi_period=rsi_period,
        rsi_max=rsi_max,
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
    equity_basis = str(sizing_raw.get("equity_basis", "cash") or "cash")
    if equity_basis not in {"cash", "equity"}:
        raise StrategyConfigError(f"Invalid equity_basis '{equity_basis}'")
    min_position_value_pct_raw = sizing_raw.get("min_position_value_pct")
    min_position_value_pct = (
        float(min_position_value_pct_raw) if min_position_value_pct_raw is not None else None
    )
    if min_position_value_pct is not None and min_position_value_pct <= 0:
        raise StrategyConfigError("min_position_value_pct must be positive")
    return StrategySizing(
        type=sizing_type,
        amount=amount,
        currency=sizing_raw.get("currency"),
        lot_size=int(lot_size) if lot_size is not None else None,
        equity_basis=equity_basis,
        min_position_value_pct=min_position_value_pct,
    )


def _parse_costs(raw: dict) -> TradingCosts | None:
    costs_raw = raw.get("costs")
    if not costs_raw:
        return None
    commission_bps = float(costs_raw.get("commission_bps", 0.0) or 0.0)
    slippage_bps = float(costs_raw.get("slippage_bps", 0.0) or 0.0)
    if commission_bps < 0 or slippage_bps < 0:
        raise StrategyConfigError("commission_bps and slippage_bps must be non-negative")
    return TradingCosts(commission_bps=commission_bps, slippage_bps=slippage_bps)


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
            "volumes": [],
            "position": None,
        })
        price = float(quote.price)
        volume = float(getattr(quote, "volume", 0) or 0)
        prices = state.setdefault("prices", [])
        volumes = state.setdefault("volumes", [])
        history_before = list(prices)
        volumes_before = list(volumes)
        prices.append(price)
        volumes.append(volume)
        max_history = max(setup.trend_long_bars, setup.lookback_bars) + setup.max_pullback_bars + 5
        if len(prices) > max_history:
            del prices[:-max_history]
            del volumes[:-max_history]

        active_position = state.get("position")
        if active_position:
            return self._generate_leveraged_exit_signal(strategy, quote, now, setup, active_position)

        # pending_entry: 买入信号已发出但尚未成交(用于次根成交模式,防止重复发单)
        if state.get("phase") == "entry_pending":
            return None

        if len(history_before) < max(setup.lookback_bars, setup.trend_long_bars):
            return None
        if not self._trend_allows_entry(history_before + [price], setup):
            state.update({"phase": "waiting_breakout", "bars": 0})
            return None

        # 止损离场后冷却: 若在冷却窗口内则不允许新进场(P1-10)
        if setup.cooldown_after_stop_minutes > 0:
            last_stop = float(state.get("last_stop_time", 0) or 0)
            if last_stop and now - last_stop < setup.cooldown_after_stop_minutes * 60:
                state.update({"phase": "waiting_breakout", "bars": 0})
                return None

        full_prices = history_before + [price]
        # 阻力位基于突破前的历史价格(不含当根),避免当根价格成为自身压力位
        resistance = self._percentile_resistance(
            history_before[-setup.lookback_bars:], setup.resistance_percentile)
        breakout_price = resistance * (1 + setup.breakout_buffer_pct / 100.0)
        support_low = resistance * (1 - setup.pullback_tolerance_pct / 100.0)
        support_high = resistance * (1 + setup.pullback_tolerance_pct / 100.0)
        confirmation_price = resistance * (1 + setup.confirmation_pct / 100.0)
        invalidation_price = resistance * (1 - setup.invalidation_pct / 100.0)
        phase = state.get("phase", "waiting_breakout")

        if phase == "waiting_breakout":
            if price >= breakout_price:
                # 成交量确认(P1-7): 突破 bar 量需 > 近 N 日均量 × mult
                if setup.require_volume_confirm and not self._volume_confirms(
                        volumes_before, volume, setup):
                    return None
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
                state.update({"phase": "waiting_confirmation", "pullback_low": price,
                              "confirm_bars": 0})
            return None

        if phase == "waiting_confirmation" and price >= confirmation_price:
            # 连续收盘确认(P1-9): 需连续 N 根站上压力位才进场
            required = setup.require_close_above_resistance_bars
            confirm_bars = int(state.get("confirm_bars", 0)) + 1
            state["confirm_bars"] = confirm_bars
            if confirm_bars < required:
                return None
            # RSI 过滤(P1-7): 超买则不进场
            if setup.rsi_period is not None and setup.rsi_max is not None:
                rsi = self._rsi(full_prices, setup.rsi_period)
                if rsi is not None and rsi > setup.rsi_max:
                    state.update({"phase": "waiting_breakout", "bars": 0})
                    return None
            pullback_low = float(state.get("pullback_low", price))
            stop_price = min(pullback_low, invalidation_price)
            risk_per_share = price - stop_price
            if risk_per_share <= 0:
                state.update({"phase": "waiting_breakout", "bars": 0})
                return None
            # 标记为 entry_pending: 等待成交(mark_filled)后再进入持仓/重置
            state.update({"phase": "entry_pending", "bars": 0})
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

    @staticmethod
    def _percentile_resistance(prices: list[float], percentile: float) -> float:
        """动态压力位。percentile=100 取 max(旧行为);<100 用百分位忽略单根尖峰。"""
        if not prices:
            return 0.0
        if percentile >= 100.0:
            return max(prices)
        sorted_prices = sorted(prices)
        # 线性插值百分位
        n = len(sorted_prices)
        if n == 1:
            return sorted_prices[0]
        rank = (percentile / 100.0) * (n - 1)
        lo = int(rank)
        hi = min(lo + 1, n - 1)
        frac = rank - lo
        return sorted_prices[lo] * (1 - frac) + sorted_prices[hi] * frac

    @staticmethod
    def _volume_confirms(volumes_before: list[float], current_volume: float,
                         setup: LeveragedBreakoutPullbackSetup) -> bool:
        """突破 bar 量是否满足放量确认: current_volume > 近 N 期均量 × mult。"""
        period = setup.volume_avg_period
        if len(volumes_before) < period:
            window = volumes_before
        else:
            window = volumes_before[-period:]
        if not window:
            return current_volume > 0
        avg_volume = sum(window) / len(window)
        if avg_volume <= 0:
            return current_volume > 0
        return current_volume > avg_volume * setup.volume_confirm_mult

    @staticmethod
    def _rsi(prices: list[float], period: int) -> float | None:
        """Wilder RSI(基于收盘价)。数据不足返回 None。"""
        if len(prices) < period + 1:
            return None
        gains = 0.0
        losses = 0.0
        for i in range(-period, 0):
            diff = prices[i] - prices[i - 1]
            if diff >= 0:
                gains += diff
            else:
                losses -= diff
        avg_gain = gains / period
        avg_loss = losses / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _generate_leveraged_exit_signal(self, strategy: TradingStrategy, quote: Quote, now: float,
                                        setup: LeveragedBreakoutPullbackSetup,
                                        active_position: dict) -> StrategySignal | None:
        price = float(quote.price)
        # 持仓 bar 计数(用于时间止损)
        active_position["bars_held"] = int(active_position.get("bars_held", 0)) + 1
        active_position["highest_price"] = max(float(active_position.get("highest_price", price)), price)
        entry_price = float(active_position["entry_price"])
        risk_per_share = float(active_position["risk_per_share"])
        initial_stop = float(active_position["stop_price"])
        partial_target = entry_price + setup.partial_take_profit_r * risk_per_share
        # 分级移动止损(P1-5): 部分止盈后用更紧的止损
        trailing_pct = setup.trailing_stop_pct
        if (active_position.get("partial_taken")
                and setup.trailing_stop_pct_after_partial is not None):
            trailing_pct = setup.trailing_stop_pct_after_partial
        trailing_stop = max(
            initial_stop,
            float(active_position["highest_price"]) * (1 - trailing_pct / 100.0),
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
        # 时间止损(P1-5): 持仓 N 根 bar 仍未到 1R 浮盈则平仓
        if setup.time_stop_bars is not None and active_position["bars_held"] >= setup.time_stop_bars:
            unrealized_r = (price - entry_price) / risk_per_share if risk_per_share > 0 else 0.0
            if unrealized_r < 1.0:
                return self._build_signal(
                    strategy=strategy,
                    quote=quote,
                    now=now,
                    action="sell",
                    trigger_field="price",
                    trigger_op="time_stop",
                    trigger_value=float(setup.time_stop_bars),
                    current_value=price,
                    metadata={"position_fraction": 1.0, "exit_kind": "final"},
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
        if signal is not None:
            self._last_filled_at[strategy_id] = self._signal_epoch(signal)
        else:
            self._last_filled_at[strategy_id] = time.time()
        if signal is None or signal.strategy.type != "leveraged_breakout_pullback":
            return
        state = self._leveraged_states.setdefault(strategy_id, {"phase": "waiting_breakout", "bars": 0, "prices": []})
        # 仅 leveraged 策略需要 entry_pending 状态恢复
        is_filled = execution is not None and execution.status == "FILLED"
        if signal.action == "buy" and not is_filled:
            # 买入被拒(如现金不足): 解除 entry_pending,允许后续重新评估进场
            if state.get("phase") == "entry_pending":
                state["phase"] = "waiting_breakout"
            return
        if execution is None or not is_filled:
            return
        if signal.action == "buy":
            fill_price = float(execution.price)
            risk_per_share = float(signal.metadata.get("risk_per_share", 0) or 0)
            stop_price = float(signal.metadata.get("stop_price", fill_price) or fill_price)
            # 成交后解除 entry_pending 并建仓
            state["phase"] = "waiting_breakout"
            state["bars"] = 0
            state["position"] = {
                "entry_price": fill_price,
                "stop_price": stop_price,
                "risk_per_share": risk_per_share,
                "highest_price": fill_price,
                "partial_taken": False,
                "bars_held": 0,
            }
        elif signal.action == "sell":
            position = state.get("position")
            if not position:
                return
            if signal.metadata.get("exit_kind") == "partial":
                position["partial_taken"] = True
            else:
                # 最终离场: 记录止损时间(供 cooldown_after_stop_minutes 使用)
                state["position"] = None
                state["last_stop_time"] = self._signal_epoch(signal)