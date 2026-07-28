from src.backtest import run_backtest
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
