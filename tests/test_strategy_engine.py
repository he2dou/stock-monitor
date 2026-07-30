import pytest
from src.models import Quote
from src.strategy_engine import StrategyConfigError, StrategyEngine, parse_strategy


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
