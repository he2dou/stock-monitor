import pytest
import yaml
from src.config_loader import load_watchlist, load_alerts, load_app_config, load_notify, load_strategies, ConfigError

def test_load_watchlist(tmp_path):
    f = tmp_path / "watchlist.yaml"
    f.write_text("""
stocks:
  - symbol: "159995"
    name: "芯片ETF"
    market: "A股"
""", encoding="utf-8")
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
""", encoding="utf-8")
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
""", encoding="utf-8")
    result = load_alerts(str(f))
    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"

def test_load_alerts_empty(tmp_path):
    f = tmp_path / "alerts.yaml"
    f.write_text("rules: []\n", encoding="utf-8")
    result = load_alerts(str(f))
    assert result == []

def test_load_watchlist_100_stocks(tmp_path):
    stocks = [{"symbol": str(i), "name": f"S{i}", "market": "A股"} for i in range(100)]
    f = tmp_path / "watchlist.yaml"
    f.write_text(yaml.dump({"stocks": stocks}), encoding="utf-8")
    result = load_watchlist(str(f))
    assert len(result) == 100

def test_load_app_config_reads_webhook(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("""
webhook_url: "https://example.test/hook"
webhook_timeout: 5
""", encoding="utf-8")
    result = load_app_config(str(f))
    assert result["webhook_url"] == "https://example.test/hook"
    assert result["webhook_timeout"] == 5

def test_load_app_config_missing_file_returns_empty():
    assert load_app_config("nonexistent.yaml") == {}

def test_load_app_config_empty_file_returns_empty(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("", encoding="utf-8")
    assert load_app_config(str(f)) == {}

def test_load_app_config_disabled_when_url_blank(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text('webhook_url: ""\nwebhook_timeout: 10\n', encoding="utf-8")
    result = load_app_config(str(f))
    assert result["webhook_url"] == ""  # caller treats blank as disabled

def test_load_notify_alias(tmp_path):
    f = tmp_path / "notify.yaml"
    f.write_text('webhook_url: "https://example.test/old"\n', encoding="utf-8")
    assert load_notify(str(f))["webhook_url"] == "https://example.test/old"

def test_load_strategies_valid(tmp_path):
    f = tmp_path / "strategies.yaml"
    f.write_text("""
strategies:
  - id: "s1"
    enabled: true
    symbol: "SOXL"
    action: "buy"
    trigger:
      field: "change_pct"
      op: "below"
      value: -10
    sizing:
      type: "fixed_amount"
      amount: 1000
      currency: "USD"
      lot_size: 1
""", encoding="utf-8")
    strategies = load_strategies(str(f))
    assert len(strategies) == 1
    assert strategies[0]["id"] == "s1"


def test_load_strategies_invalid_action(tmp_path):
    f = tmp_path / "strategies.yaml"
    f.write_text("""
strategies:
  - id: "s1"
    symbol: "SOXL"
    action: "hold"
    trigger:
      field: "price"
      op: "above"
      value: 1
    sizing:
      type: "fixed_amount"
      amount: 1000
""", encoding="utf-8")
    with pytest.raises(ConfigError, match="action"):
        load_strategies(str(f))
