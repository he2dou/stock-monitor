"""Unit tests for trading-hours gating (all times Beijing, UTC+8)."""
from datetime import datetime
from src.market_hours import (CST, is_market_open, any_market_open,
                              sessions_for, _is_us_dst)


def at(year, month, day, hour, minute=0):
    """Build an aware Beijing-time datetime."""
    return datetime(year, month, day, hour, minute, tzinfo=CST)


# --- A-share ---------------------------------------------------------------

def test_a_share_morning_open():
    assert is_market_open("A股", at(2026, 7, 27, 9, 30))   # Monday 09:30 open
    assert is_market_open("A股", at(2026, 7, 27, 10, 0))
    assert is_market_open("A股", at(2026, 7, 27, 11, 30))   # boundary inclusive


def test_a_share_lunch_break_closed():
    assert not is_market_open("A股", at(2026, 7, 27, 11, 31))
    assert not is_market_open("A股", at(2026, 7, 27, 12, 30))
    assert not is_market_open("A股", at(2026, 7, 27, 12, 59))


def test_a_share_afternoon_and_close():
    assert is_market_open("A股", at(2026, 7, 27, 13, 0))
    assert is_market_open("A股", at(2026, 7, 27, 15, 0))    # boundary inclusive
    assert not is_market_open("A股", at(2026, 7, 27, 15, 1))


# --- HK --------------------------------------------------------------------

def test_hk_morning_and_afternoon():
    assert is_market_open("港股", at(2026, 7, 27, 9, 30))
    assert is_market_open("港股", at(2026, 7, 27, 12, 0))   # morning close inclusive
    assert is_market_open("港股", at(2026, 7, 27, 13, 0))
    assert is_market_open("港股", at(2026, 7, 27, 16, 0))   # afternoon close inclusive
    assert not is_market_open("港股", at(2026, 7, 27, 16, 1))


# --- US --------------------------------------------------------------------

def test_us_winter_evening_open():
    # January (not DST) -> winter hours 22:30-05:00
    assert is_market_open("美股", at(2026, 1, 5, 22, 30))   # Mon 22:30 open
    assert is_market_open("美股", at(2026, 1, 5, 23, 59))
    assert is_market_open("美股", at(2026, 1, 6, 0, 0))     # post-midnight
    assert is_market_open("美股", at(2026, 1, 6, 4, 59))
    assert not is_market_open("美股", at(2026, 1, 6, 5, 1))


def test_us_summer_evening_open():
    # July (DST) -> summer hours 21:30-04:00
    assert is_market_open("美股", at(2026, 7, 27, 21, 30))
    assert not is_market_open("美股", at(2026, 7, 27, 21, 29))


def test_us_dst_detection():
    assert _is_us_dst(at(2026, 7, 15, 12, 0)) is True       # mid-summer
    assert _is_us_dst(at(2026, 1, 15, 12, 0)) is False      # mid-winter


# --- weekends --------------------------------------------------------------

def test_weekend_closed():
    # 2026-07-25 is Saturday, 07-26 is Sunday.
    assert not is_market_open("A股", at(2026, 7, 25, 10, 0))
    assert not is_market_open("港股", at(2026, 7, 25, 10, 0))
    assert not is_market_open("美股", at(2026, 7, 26, 22, 30))


# --- any_market_open -------------------------------------------------------

def test_any_market_open_mixed():
    # Monday morning Beijing: A & HK open, US closed.
    when = at(2026, 7, 27, 10, 0)
    assert any_market_open(["A股", "港股", "美股"], when) is True
    assert any_market_open(["美股"], when) is False


def test_any_market_open_all_closed():
    # Sunday afternoon: all closed.
    when = at(2026, 7, 26, 10, 0)
    assert any_market_open(["A股", "港股", "美股"], when) is False


def test_unknown_market_is_closed():
    assert is_market_open("期货", at(2026, 7, 27, 10, 0)) is False
    assert sessions_for("期货", at(2026, 7, 27, 10, 0)) == []

def test_us_friday_session_continues_into_saturday_morning_beijing():
    # US Friday regular session continues into Saturday morning in Beijing time.
    assert is_market_open("美股", at(2026, 7, 25, 3, 30)) is True


def test_us_monday_early_morning_beijing_is_closed():
    # Monday 03:30 Beijing corresponds to Sunday afternoon/evening in the US.
    assert is_market_open("美股", at(2026, 7, 27, 3, 30)) is False
