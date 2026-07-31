import pytest
from src.models import Quote
from src.strategy_engine import StrategyConfigError, StrategyEngine, parse_strategy
from src.trading_models import OrderExecution


def strategy(**overrides):
    base = {
        "id": "s1",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10.0},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 300},
    }
    base.update(overrides)
    return base


def test_parse_strategy_valid():
    s = parse_strategy(strategy())
    assert s.id == "s1"
    assert s.action == "buy"
    assert s.trigger.matches(-11)
    assert s.sizing.amount == 1000


def test_parse_strategy_rejects_invalid_action():
    with pytest.raises(StrategyConfigError):
        parse_strategy(strategy(action="hold"))


def test_strategy_engine_generates_signal_when_trigger_matches():
    engine = StrategyEngine([strategy()])
    quote = Quote("SOXL", "半导体ETF", "美股", 120, -11, 1000)
    signals = engine.generate_signals([quote])
    assert len(signals) == 1
    assert signals[0].strategy.id == "s1"
    assert signals[0].current_value == -11


def test_strategy_engine_ignores_disabled_strategy():
    engine = StrategyEngine([strategy(enabled=False)])
    quote = Quote("SOXL", "半导体ETF", "美股", 120, -11, 1000)
    assert engine.generate_signals([quote]) == []


def test_strategy_engine_reports_cooldown_after_fill():
    engine = StrategyEngine([strategy()])
    quote = Quote("SOXL", "半导体ETF", "美股", 120, -11, 1000)
    first = engine.generate_signals([quote])[0]
    assert first.cooldown_remaining_seconds == 0
    engine.mark_filled("s1")
    second = engine.generate_signals([quote])[0]
    assert second.cooldown_remaining_seconds > 0

def breakout_pullback_strategy(**overrides):
    base = {
        "id": "bp1",
        "type": "breakout_pullback",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "breakout_pullback": {
            "resistance": 100.0,
            "breakout_buffer_pct": 1.0,
            "pullback_tolerance_pct": 1.0,
            "confirmation_pct": 0.3,
            "max_pullback_bars": 4,
            "invalidation_pct": 2.0,
        },
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }
    base.update(overrides)
    return base


def test_parse_breakout_pullback_strategy_valid():
    s = parse_strategy(breakout_pullback_strategy())
    assert s.type == "breakout_pullback"
    assert s.trigger is None
    assert s.breakout_pullback.resistance == 100.0


def test_breakout_pullback_generates_signal_after_confirmed_support():
    engine = StrategyEngine([breakout_pullback_strategy()])
    quotes = [
        Quote("SOXL", "半导体ETF", "美股", 99.0, -1, 1000),
        Quote("SOXL", "半导体ETF", "美股", 101.5, 2, 1000),
        Quote("SOXL", "半导体ETF", "美股", 100.2, 0.2, 1000),
        Quote("SOXL", "半导体ETF", "美股", 100.5, 0.5, 1000),
    ]

    assert engine.generate_signals([quotes[0]]) == []
    assert engine.generate_signals([quotes[1]]) == []
    assert engine.generate_signals([quotes[2]]) == []
    signals = engine.generate_signals([quotes[3]])

    assert len(signals) == 1
    assert signals[0].strategy.id == "bp1"
    assert signals[0].trigger_field == "price"
    assert signals[0].trigger_op == "breakout_pullback_confirmed"
    assert signals[0].trigger_value == 100.0
    assert signals[0].current_value == 100.5


def test_breakout_pullback_state_survives_strategy_reload():
    raw = breakout_pullback_strategy()
    engine = StrategyEngine([raw])
    assert engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 101.5, 2, 1000)]) == []

    engine.set_strategies([raw])
    assert engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 100.1, 0.1, 1000)]) == []
    signals = engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 100.4, 0.4, 1000)])
    assert len(signals) == 1


def test_breakout_pullback_resets_when_support_fails():
    engine = StrategyEngine([breakout_pullback_strategy()])
    engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 101.5, 2, 1000)])
    engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 97.5, -2, 1000)])
    assert engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 100.5, 0.5, 1000)]) == []

def leveraged_breakout_pullback_strategy(**overrides):
    base = {
        "id": "soxl_lbp",
        "type": "leveraged_breakout_pullback",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "leveraged_breakout_pullback": {
            "lookback_bars": 3,
            "breakout_buffer_pct": 0.0,
            "pullback_tolerance_pct": 5.0,
            "confirmation_pct": 0.0,
            "max_pullback_bars": 4,
            "invalidation_pct": 8.0,
            "trend_short_bars": 2,
            "trend_long_bars": 4,
            "partial_take_profit_r": 3.0,
            "partial_sell_fraction": 0.5,
            "trailing_stop_pct": 12.0,
        },
        "sizing": {"type": "risk_percent", "amount": 1.0, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0, "max_position_amount": 20000},
    }
    base.update(overrides)
    return base


def q(price):
    return Quote("SOXL", "半导体ETF", "美股", price, 0, 1000)


def drive_lbp_entry(engine):
    for price in [10, 12, 14, 16, 17, 16.2]:
        assert engine.generate_signals([q(price)]) == []
    return engine.generate_signals([q(17.0)])


def test_parse_leveraged_breakout_pullback_strategy_valid():
    s = parse_strategy(leveraged_breakout_pullback_strategy())
    assert s.type == "leveraged_breakout_pullback"
    assert s.trigger is None
    assert s.sizing.type == "risk_percent"
    assert s.leveraged_breakout_pullback.invalidation_pct == 8.0
    assert s.leveraged_breakout_pullback.pullback_tolerance_pct == 5.0


def test_parse_leveraged_breakout_pullback_rejects_invalid_trend_bars():
    raw = leveraged_breakout_pullback_strategy(leveraged_breakout_pullback={
        "lookback_bars": 3,
        "trend_short_bars": 4,
        "trend_long_bars": 4,
    })
    with pytest.raises(StrategyConfigError):
        parse_strategy(raw)


def test_parse_leveraged_breakout_pullback_rejects_invalid_partial_fraction():
    raw = leveraged_breakout_pullback_strategy(leveraged_breakout_pullback={
        "lookback_bars": 3,
        "trend_short_bars": 2,
        "trend_long_bars": 4,
        "partial_sell_fraction": 1.2,
    })
    with pytest.raises(StrategyConfigError):
        parse_strategy(raw)


def test_leveraged_breakout_pullback_generates_entry_after_trend_breakout_pullback_confirm():
    engine = StrategyEngine([leveraged_breakout_pullback_strategy()])
    signals = drive_lbp_entry(engine)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.action == "buy"
    assert signal.trigger_op == "leveraged_breakout_pullback_confirmed"
    assert signal.metadata["setup_type"] == "leveraged_breakout_pullback"
    assert signal.metadata["resistance"] == 16
    assert signal.metadata["stop_price"] == pytest.approx(14.72)
    assert signal.metadata["risk_per_share"] == pytest.approx(2.28)


def test_leveraged_breakout_pullback_blocks_entry_when_trend_filter_fails():
    engine = StrategyEngine([leveraged_breakout_pullback_strategy()])
    for price in [14, 13, 12, 11, 14, 13.2, 13.3]:
        signals = engine.generate_signals([q(price)])
    assert signals == []


def test_leveraged_breakout_pullback_state_survives_strategy_reload():
    raw = leveraged_breakout_pullback_strategy()
    engine = StrategyEngine([raw])
    for price in [10, 12, 14, 16, 17]:
        assert engine.generate_signals([q(price)]) == []

    engine.set_strategies([raw])
    assert engine.generate_signals([q(16.2)]) == []
    signals = engine.generate_signals([q(17.0)])
    assert len(signals) == 1


def test_leveraged_breakout_pullback_generates_partial_then_trailing_exit_after_fills():
    engine = StrategyEngine([leveraged_breakout_pullback_strategy()])
    buy_signal = drive_lbp_entry(engine)[0]
    engine.mark_filled("soxl_lbp", buy_signal, OrderExecution(
        strategy_id="soxl_lbp", symbol="SOXL", market="美股", side="buy",
        status="FILLED", quantity=100, price=buy_signal.quote_price, amount=1700,
        currency="USD",
    ))

    partial = engine.generate_signals([q(24.0)])[0]
    assert partial.action == "sell"
    assert partial.trigger_op == "partial_take_profit"
    assert partial.metadata["position_fraction"] == 0.5

    engine.mark_filled("soxl_lbp", partial, OrderExecution(
        strategy_id="soxl_lbp", symbol="SOXL", market="美股", side="sell",
        status="FILLED", quantity=50, price=24.0, amount=1200,
        currency="USD",
    ))
    final = engine.generate_signals([q(20.0)])[0]
    assert final.action == "sell"
    assert final.trigger_op == "trailing_or_initial_stop"
    assert final.metadata["position_fraction"] == 1.0

def test_leveraged_exit_signal_is_not_blocked_by_entry_cooldown():
    engine = StrategyEngine([leveraged_breakout_pullback_strategy(
        constraints={"cooldown_minutes": 300, "max_position_amount": 20000},
    )])
    buy_signal = drive_lbp_entry(engine)[0]
    engine.mark_filled("soxl_lbp", buy_signal, OrderExecution(
        strategy_id="soxl_lbp", symbol="SOXL", market="美股", side="buy",
        status="FILLED", quantity=100, price=buy_signal.quote_price, amount=1700,
        currency="USD",
    ))

    partial = engine.generate_signals([q(24.0)])[0]
    assert partial.trigger_op == "partial_take_profit"
    assert partial.cooldown_remaining_seconds == 0.0
