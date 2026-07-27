import pytest
from src.alerts_engine import AlertEngine
from src.models import Quote, AlertRule

def test_no_alerts_when_no_rules():
    engine = AlertEngine(rules=[])
    quotes = [Quote("AAPL", "Apple", "美股", 200, 1.0, 100)]
    alerts = engine.check(quotes)
    assert alerts == []

def test_alert_triggered_price_above():
    rules = [{"symbol": "AAPL", "field": "price", "op": "above", "value": 200}]
    engine = AlertEngine(rules)
    quotes = [Quote("AAPL", "Apple", "美股", 210, 1.0, 100)]
    alerts = engine.check(quotes)
    assert len(alerts) == 1
    assert "AAPL" in alerts[0].message

def test_alert_not_triggered():
    rules = [{"symbol": "AAPL", "field": "price", "op": "above", "value": 200}]
    engine = AlertEngine(rules)
    quotes = [Quote("AAPL", "Apple", "美股", 150, 1.0, 100)]
    alerts = engine.check(quotes)
    assert alerts == []

def test_alert_change_pct_below():
    rules = [{"symbol": "159995", "field": "change_pct", "op": "below", "value": -3.0}]
    engine = AlertEngine(rules)
    quotes = [Quote("159995", "芯片ETF", "A股", 1.0, -5.0, 100)]
    alerts = engine.check(quotes)
    assert len(alerts) == 1

def test_dedup_no_repeat_within_cooldown():
    rules = [{"symbol": "AAPL", "field": "price", "op": "above", "value": 200}]
    engine = AlertEngine(rules, cooldown_seconds=60)
    quotes = [Quote("AAPL", "Apple", "美股", 210, 1.0, 100)]
    alerts1 = engine.check(quotes)
    alerts2 = engine.check(quotes)
    assert len(alerts1) == 1
    assert len(alerts2) == 0
