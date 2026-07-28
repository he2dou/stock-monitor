from unittest.mock import patch

import pandas as pd

from src.index_history import TencentIndexHistorySource, backfill_index_snapshots
from src.trading_store import TradingStore


def test_tencent_index_history_source_filters_range_and_computes_change_pct():
    df = pd.DataFrame({
        "date": ["2026-06-30", "2026-07-01", "2026-07-02"],
        "open": [90, 100, 105],
        "close": [100, 110, 99],
        "high": [101, 111, 106],
        "low": [89, 98, 98],
        "amount": [1000, 2000, 3000],
    })
    with patch("src.index_history.ak.stock_zh_index_daily_tx", return_value=df) as mock_tx:
        rows = TencentIndexHistorySource().fetch_index_rows(
            {"symbol": "000001", "name": "上证指数", "market": "A股", "tencent_symbol": "sh000001"},
            "2026-07-01",
            "2026-07-02",
        )

    assert mock_tx.call_args.kwargs["symbol"] == "sh000001"
    assert [r["snapshot_date"] for r in rows] == ["2026-07-01", "2026-07-02"]
    assert rows[0]["price"] == 110
    assert rows[0]["change_pct"] == 10.0
    assert rows[1]["change_pct"] == (99 - 110) / 110 * 100


def test_backfill_index_snapshots_upserts_rows(tmp_path):
    df = pd.DataFrame({
        "date": ["2026-06-30", "2026-07-01"],
        "open": [90, 100],
        "close": [100, 110],
        "high": [101, 111],
        "low": [89, 98],
        "amount": [1000, 2000],
    })
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    config = {
        "market_indices": {
            "indices": [
                {"symbol": "000001", "name": "上证指数", "market": "A股", "tencent_symbol": "sh000001"}
            ]
        }
    }
    with patch("src.index_history.ak.stock_zh_index_daily_tx", return_value=df):
        result = backfill_index_snapshots(store, config, "2026-07-01", "2026-07-01")

    rows = store.load_index_snapshots()
    assert result["rows"] == 1
    assert result["saved"] == 1
    assert rows[0]["symbol"] == "000001"
    assert rows[0]["snapshot_date"] == "2026-07-01"
    assert rows[0]["price"] == 110
    store.close()