from datetime import datetime

from src.index_snapshots import DEFAULT_MARKET_INDICES, load_market_indices, market_snapshot_date
from src.market_hours import CST


def at(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=CST)


def test_default_market_indices_cover_three_markets():
    indices = load_market_indices({})
    assert len(indices) == 9
    assert sum(1 for item in indices if item["market"] == "A股") == 3
    assert sum(1 for item in indices if item["market"] == "港股") == 3
    assert sum(1 for item in indices if item["market"] == "美股") == 3
    assert indices == DEFAULT_MARKET_INDICES


def test_market_indices_can_be_disabled():
    assert load_market_indices({"market_indices": {"enabled": False}}) == []


def test_market_indices_can_be_overridden():
    custom = [{"symbol": "X", "name": "Custom", "market": "美股", "sina_symbol": "gb_x"}]
    assert load_market_indices({"market_indices": {"indices": custom}}) == custom


def test_us_snapshot_date_uses_market_trading_day_after_midnight_beijing():
    assert market_snapshot_date("美股", at(2026, 7, 28, 3, 30)) == "2026-07-27"
    assert market_snapshot_date("美股", at(2026, 7, 27, 21, 30)) == "2026-07-27"


def test_a_and_hk_snapshot_date_use_beijing_day():
    assert market_snapshot_date("A股", at(2026, 7, 28, 10, 0)) == "2026-07-28"
    assert market_snapshot_date("港股", at(2026, 7, 28, 10, 0)) == "2026-07-28"