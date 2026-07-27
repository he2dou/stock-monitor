from src.models import Quote, Alert, AlertRule

def test_quote_creation():
    q = Quote(symbol="159995", name="芯片ETF", market="A股",
              price=1.234, change_pct=2.5, volume=1000000)
    assert q.symbol == "159995"
    assert q.price == 1.234

def test_quote_serialization():
    q = Quote(symbol="AAPL", name="Apple", market="美股",
              price=195.5, change_pct=-1.2, volume=50000)
    d = q.to_dict()
    assert d["symbol"] == "AAPL"
    assert "timestamp" in d

def test_alert_rule_above():
    rule = AlertRule(field="price", op="above", value=2.0)
    assert rule.matches(2.5) is True
    assert rule.matches(1.5) is False

def test_alert_rule_below():
    rule = AlertRule(field="price", op="below", value=2.0)
    assert rule.matches(1.5) is True
    assert rule.matches(2.5) is False

def test_alert_rule_change_pct():
    rule = AlertRule(field="change_pct", op="above", value=5.0)
    assert rule.matches(6.0) is True
    assert rule.matches(3.0) is False
