from src.models import Quote
from src.trading_store import TradingStore


def test_store_initializes_accounts_and_quotes(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    store.ensure_accounts({"USD": 50000})
    assert store.get_balance("USD") == 50000

    q = Quote("SOXL", "半导体ETF", "美股", 120.0, -11.0, 1000, timestamp="2026-01-01T00:00:00Z")
    store.save_quote_snapshots([q])
    loaded = store.load_quote_snapshots()
    assert len(loaded) == 1
    assert loaded[0].symbol == "SOXL"
    assert loaded[0].price == 120.0
    store.close()


def test_store_records_order_fill_and_position(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    order_id = store.record_order("sig", "s1", "SOXL", "美股", "buy", 10, 100, "USD", "FILLED")
    store.record_fill(order_id, 100, 10)
    store.upsert_position("美股", "SOXL", "半导体ETF", "USD", 10, 100, 0)
    assert store.order_count() == 1
    assert store.fill_count() == 1
    assert store.get_position("美股", "SOXL")["quantity"] == 10
    store.close()

def test_store_seeds_and_loads_watchlist(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    stocks = [
        {"symbol": "SOXL", "name": "半导体ETF", "market": "美股"},
        {"symbol": "159995", "name": "芯片ETF", "market": "A股"},
    ]
    assert store.seed_watchlist(stocks) == 2
    assert store.seed_watchlist(stocks) == 0
    loaded = store.load_watchlist()
    assert [s["symbol"] for s in loaded] == ["SOXL", "159995"]
    store.set_stock_enabled("SOXL", False)
    assert [s["symbol"] for s in store.load_watchlist()] == ["159995"]
    assert len(store.load_watchlist(include_disabled=True)) == 2
    store.close()


def test_store_import_watchlist_replace(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    store.import_watchlist([{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    store.import_watchlist([{"symbol": "SOXL", "name": "半导体ETF", "market": "美股"}], replace=True)
    assert [s["symbol"] for s in store.load_watchlist()] == ["SOXL"]
    store.close()


def test_store_alert_rules_enable_disable(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    rules = [{"symbol": "SOXL", "field": "change_pct", "op": "below", "value": -10.0}]
    assert store.seed_alert_rules(rules) == 1
    assert store.seed_alert_rules(rules) == 0
    loaded = store.load_alert_rules()
    assert len(loaded) == 1
    assert loaded[0]["symbol"] == "SOXL"
    rule_id = loaded[0]["rule_id"]
    store.set_alert_rule_enabled(rule_id, False)
    assert store.load_alert_rules() == []
    assert len(store.load_alert_rules(include_disabled=True)) == 1
    store.close()


def test_store_add_stock_and_alert(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    store.add_stock("00700", "腾讯控股", "港股")
    assert store.load_watchlist()[0]["symbol"] == "00700"
    rule_id = store.add_alert_rule("00700", "price", "above", 400)
    assert store.load_alert_rules()[0]["rule_id"] == rule_id
    store.close()
