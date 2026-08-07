from src.backtest import run_backtest, write_markdown_report, write_trades_csv
from src.models import Quote
from src.trading_store import TradingStore


def test_run_backtest_evaluates_any_symbol_regardless_of_binding(tmp_path):
    """A strategy bound to one symbol can be backtested on another symbol.

    Regression test: the engine matches quotes by strategy.symbol, so without
    retargeting a backtest on an unbound symbol silently produced 0 fills.
    """
    db = tmp_path / "trading.sqlite3"
    store = TradingStore(str(db))
    # Save a MSFT snapshot that satisfies the trigger (change_pct < -10).
    store.save_quote_snapshots([
        Quote("MSFT", "Microsoft", "美股", 100, -11, 1000, timestamp="2026-01-01T00:00:00Z")
    ])
    store.close()
    strategies = [{
        "id": "buy_aapl",
        "enabled": True,
        "symbol": "AAPL",  # bound symbol, intentionally != backtest symbol
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        source="quote-snapshots", symbols=["MSFT"],
        strategy_ids=["buy_aapl"],
    )
    # Without retargeting this would be 0; the strategy must run on MSFT.
    assert summary["quotes_replayed"] == 1
    assert summary["orders"] == 1
    assert summary["fills"] == 1



from src.models import Quote
from src.trading_store import TradingStore


def test_run_backtest_replays_saved_quotes(tmp_path):
    db = tmp_path / "trading.sqlite3"
    store = TradingStore(str(db))
    store.save_quote_snapshots([
        Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-01-01T00:00:00Z")
    ])
    store.close()
    strategies = [{
        "id": "buy_soxl",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    summary = run_backtest(str(db), strategies, {"USD": 50000})
    assert summary["quotes_replayed"] == 1
    assert summary["orders"] == 1
    assert summary["fills"] == 1
    assert summary["ending_cash"]["USD"] == 49000

def test_run_backtest_replays_daily_bars_and_writes_report(tmp_path):
    db = tmp_path / "trading.sqlite3"
    store = TradingStore(str(db))
    store.save_daily_bars([
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-28",
            "open": 10, "high": 11, "low": 9, "close": 10,
            "adj_close": 10, "volume": 1000, "source": "test",
        },
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-29",
            "open": 10, "high": 11, "low": 9, "close": 8,
            "adj_close": 8, "volume": 1000, "source": "test",
        },
    ])
    store.close()
    strategies = [{
        "id": "buy_soxl",
        "enabled": False,
        "symbol": "SOXL",
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]

    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        start="2026-07-28", end="2026-07-29", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["buy_soxl"], enable_selected=True,
    )
    report = tmp_path / "report.md"
    csv_path = tmp_path / "trades.csv"
    write_markdown_report(summary, str(report))
    write_trades_csv(summary, str(csv_path))

    assert summary["source"] == "daily-bars"
    assert summary["quotes_replayed"] == 2
    assert summary["orders"] == 1
    assert summary["fills"] == 1
    assert summary["trades"][0]["date"] == "2026-07-29"
    assert "Strategy Backtest Report" in report.read_text(encoding="utf-8")
    assert "buy_soxl" in csv_path.read_text(encoding="utf-8")


def test_backtest_reports_usd_drawdown_separately(tmp_path):
    db = tmp_path / "trading.sqlite3"
    store = TradingStore(str(db))
    store.save_daily_bars([
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-27",
            "open": 10, "high": 10, "low": 10, "close": 10,
            "adj_close": 10, "volume": 1000, "source": "test",
        },
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-28",
            "open": 8, "high": 8, "low": 8, "close": 8,
            "adj_close": 8, "volume": 1000, "source": "test",
        },
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-29",
            "open": 4, "high": 4, "low": 4, "close": 4,
            "adj_close": 4, "volume": 1000, "source": "test",
        },
    ])
    store.close()
    strategies = [{
        "id": "buy_soxl",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 10000},
    }]

    summary = run_backtest(
        str(db), strategies, {"USD": 50000, "CNY": 100000},
        start="2026-07-27", end="2026-07-29", source="daily-bars", symbols=["SOXL"],
    )
    report = tmp_path / "report.md"
    write_markdown_report(summary, str(report))

    assert round(summary["max_drawdown_pct"], 2) == -0.33
    assert round(summary["usd_max_drawdown_pct"], 2) == -1.00
    assert "USD max drawdown: -1.00%" in report.read_text(encoding="utf-8")

def test_backtest_cooldown_uses_quote_dates_not_runtime_clock(tmp_path):
    db = tmp_path / "trading.sqlite3"
    store = TradingStore(str(db))
    store.save_daily_bars([
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-27",
            "open": 10, "high": 10, "low": 10, "close": 10,
            "adj_close": 10, "volume": 1000, "source": "test",
        },
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-28",
            "open": 8, "high": 8, "low": 8, "close": 8,
            "adj_close": 8, "volume": 1000, "source": "test",
        },
        {
            "symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2026-07-29",
            "open": 6, "high": 6, "low": 6, "close": 6,
            "adj_close": 6, "volume": 1000, "source": "test",
        },
    ])
    store.close()
    strategies = [{
        "id": "buy_soxl",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 600, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 300},
    }]

    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        start="2026-07-27", end="2026-07-29", source="daily-bars", symbols=["SOXL"],
    )

    assert summary["orders"] == 2
    assert summary["fills"] == 2
    assert all(trade["status"] == "FILLED" for trade in summary["trades"])
