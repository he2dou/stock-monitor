"""Trading-hours gating.

All sessions are expressed in Beijing time (UTC+8), the timezone the operator
runs in. The monitor only fetches quotes when at least one watched market is
open, so it stops wasting requests (and avoids tripping provider rate limits)
overnight and on weekends.

US hours straddle midnight (21:30 -> 04:00 next day), so we model each session
as (start_minute_of_day, end_minute_of_day) and split a cross-midnight session
into two same-day windows for easy membership testing.

NOTE: national public holidays are intentionally NOT hard-coded -- they vary by
country and change yearly. The operator is expected to keep the process off on
holidays; a holiday is simply treated like an empty (no-trade) day, which is the
correct safe behaviour (no fetch, no spurious alerts).
"""
from __future__ import annotations

from datetime import datetime, time, timezone, timedelta

# Fixed Beijing-time offset (no DST in mainland China).
CST = timezone(timedelta(hours=8))

# Per-market trading sessions in Beijing time, as (start, end) pairs of
# datetime.time. US session crosses midnight, represented as two windows.
SESSIONS: dict[str, list[tuple[time, time]]] = {
    "A股": [
        (time(9, 30), time(11, 30)),   # morning
        (time(13, 0), time(15, 0)),    # afternoon
    ],
    "港股": [
        (time(9, 30), time(12, 0)),    # morning
        (time(13, 0), time(16, 0)),    # afternoon
    ],
    # US standard time (winter): 22:30-05:00 next day (CST).
    # US daylight time (summer): 21:30-04:00 next day (CST).
    # The clock dates drift yearly; we pick the DST window by US rule of thumb
    # (2nd Sunday of March -> 1st Sunday of November) for reasonable accuracy.
    "美股": [],  # filled dynamically in _us_sessions()
}


def _us_sessions(when: datetime) -> list[tuple[time, time]]:
    """Return the US trading windows (Beijing time) for the given moment's date."""
    if _is_us_dst(when):
        return [(time(21, 30), time(23, 59, 59)),
                (time(0, 0), time(4, 0))]
    return [(time(22, 30), time(23, 59, 59)),
            (time(0, 0), time(5, 0))]


def _is_us_dst(when: datetime) -> bool:
    """US DST: 2nd Sunday of March 02:00 -> 1st Sunday of November 02:00 (local).

    DST depends only on the calendar date; `when` may be tz-aware or naive, so
    we compare on a date-only basis to avoid offset-mismatch errors.
    """
    year = when.year
    d1 = datetime(year, 3, 1)
    second_sunday = 1 + ((6 - d1.weekday()) % 7) + 7
    start = datetime(year, 3, second_sunday).date()
    d2 = datetime(year, 11, 1)
    first_sunday = 1 + ((6 - d2.weekday()) % 7)
    end = datetime(year, 11, first_sunday).date()
    return start <= when.date() < end


def sessions_for(market: str, when: datetime | None = None) -> list[tuple[time, time]]:
    """Trading windows for a market at a given moment (Beijing time)."""
    when = when or datetime.now(CST)
    if market == "美股":
        return _us_sessions(when)
    return SESSIONS.get(market, [])


def is_market_open(market: str, when: datetime | None = None) -> bool:
    """True if the given market is within a trading session at `when` (defaults now)."""
    when = (when or datetime.now(CST)).astimezone(CST)
    if when.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    now_t = when.time()
    for start, end in sessions_for(market, when):
        if start <= now_t <= end:
            return True
    return False


def any_market_open(markets: list[str], when: datetime | None = None) -> bool:
    """True if at least one of the given markets is open at `when`."""
    return any(is_market_open(m, when) for m in markets)
