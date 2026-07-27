import pytest
import yaml
from src.config_loader import load_watchlist, load_alerts, ConfigError

def test_load_watchlist(tmp_path):
    f = tmp_path / "watchlist.yaml"
    f.write_text("""
stocks:
  - symbol: "159995"
    name: "芯片ETF"
    market: "A股"
""")
    result = load_watchlist(str(f))
    assert len(result) == 1
    assert result[0]["symbol"] == "159995"
    assert result[0]["market"] == "A股"

def test_load_watchlist_invalid_market(tmp_path):
    f = tmp_path / "watchlist.yaml"
    f.write_text("""
stocks:
  - symbol: "X"
    name: "X"
    market: "期货"
""")
    with pytest.raises(ConfigError, match="market"):
        load_watchlist(str(f))

def test_load_watchlist_missing_file():
    with pytest.raises(FileNotFoundError):
        load_watchlist("nonexistent.yaml")

def test_load_alerts(tmp_path):
    f = tmp_path / "alerts.yaml"
    f.write_text("""
rules:
  - symbol: "AAPL"
    field: "price"
    op: "above"
    value: 200.0
""")
    result = load_alerts(str(f))
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"

def test_load_alerts_empty(tmp_path):
    f = tmp_path / "alerts.yaml"
    f.write_text("rules: []\n")
    result = load_alerts(str(f))
    assert result == []

def test_load_watchlist_100_stocks(tmp_path):
    stocks = [{"symbol": str(i), "name": f"S{i}", "market": "A股"} for i in range(100)]
    f = tmp_path / "watchlist.yaml"
    f.write_text(yaml.dump({"stocks": stocks}))
    result = load_watchlist(str(f))
    assert len(result) == 100
