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
