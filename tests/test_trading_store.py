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
