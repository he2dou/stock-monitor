from concurrent.futures import ThreadPoolExecutor
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


def test_store_upserts_one_quote_snapshot_per_symbol_per_day(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    first = Quote("159995", "芯片ETF", "A股", 1.20, -11.0, 1000, timestamp="2026-07-29T10:00:00+08:00")
    latest = Quote("159995", "芯片ETF", "A股", 1.25, -8.0, 2000, timestamp="2026-07-29T14:00:00+08:00")

    store.save_quote_snapshots([first])
    store.save_quote_snapshots([latest])

    loaded = store.load_quote_snapshots()
    assert len(loaded) == 1
    assert loaded[0].symbol == "159995"
    assert loaded[0].price == 1.25
    assert loaded[0].volume == 2000
    rows = store.conn.execute("SELECT symbol, snapshot_date FROM quote_snapshots").fetchall()
    assert dict(rows[0]) == {"symbol": "159995", "snapshot_date": "2026-07-29"}
    store.close()


def test_store_creates_daily_snapshot_unique_indexes(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    quote_indexes = {row["name"] for row in store.conn.execute("PRAGMA index_list(quote_snapshots)").fetchall()}
    index_indexes = {row["name"] for row in store.conn.execute("PRAGMA index_list(index_snapshots)").fetchall()}
    assert "idx_quote_snapshots_symbol_day" in quote_indexes
    assert "idx_index_snapshots_symbol_day" in index_indexes
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


def test_store_upserts_one_index_snapshot_per_symbol_per_day(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    first = Quote(".DJI", "道琼斯工业平均指数", "美股", 52210.0, 0.51, 1000, timestamp="2026-07-28T01:00:00Z")
    latest = Quote(".DJI", "道琼斯工业平均指数", "美股", 52300.0, 0.68, 2000, timestamp="2026-07-28T02:00:00Z")

    assert store.index_snapshot_exists(".DJI", "2026-07-27") is False
    assert store.save_index_snapshots([first], {".DJI": "2026-07-27"}) == 1
    assert store.index_snapshot_exists(".DJI", "2026-07-27") is True
    assert store.save_index_snapshots([latest], {".DJI": "2026-07-27"}) == 1

    rows = store.load_index_snapshots()
    assert len(rows) == 1
    assert rows[0]["symbol"] == ".DJI"
    assert rows[0]["snapshot_date"] == "2026-07-27"
    assert rows[0]["price"] == 52300.0
    assert rows[0]["volume"] == 2000
    store.close()

def test_store_can_be_used_from_scheduler_worker_thread(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    store.add_stock("AAPL", "Apple", "美股")

    def worker():
        loaded = store.load_watchlist()
        quote = Quote(".DJI", "道琼斯工业平均指数", "美股", 52210.0, 0.51, 1000)
        saved = store.save_index_snapshots([quote], {".DJI": "2026-07-29"})
        return loaded[0]["symbol"], saved

    with ThreadPoolExecutor(max_workers=1) as executor:
        symbol, saved = executor.submit(worker).result(timeout=5)

    assert symbol == "AAPL"
    assert saved == 1
    assert store.load_index_snapshots()[0]["symbol"] == ".DJI"
    store.close()

def test_store_upserts_daily_bars_per_symbol_date(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    first = {
        "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-28",
        "open": 20.0, "high": 21.0, "low": 19.5, "close": 20.5,
        "adj_close": 20.5, "volume": 1000, "source": "yahoo",
    }
    latest = dict(first, close=22.0, volume=2000)

    assert store.save_daily_bars([first]) == 1
    assert store.save_daily_bars([latest]) == 1
    rows = store.load_daily_bars(symbols=["SOXL"], start="2026-07-28", end="2026-07-28")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "SOXL"
    assert rows[0]["close"] == 22.0
    assert rows[0]["volume"] == 2000
    store.close()
