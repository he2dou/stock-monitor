"""Trading-hours gating.

All times are evaluated in Beijing time (UTC+8), which is the runtime timezone
for this monitor. The monitor fetches only symbols whose own market is open, so
closed-market symbols are not queried just because another market is trading.

Holiday calendars are intentionally not hard-coded here because A/HK/US holiday
rules change yearly. Regular weekdays and intraday sessions are covered; holiday
closures naturally return stale/no-trade quotes from providers if the process is
left running on a holiday.
"""
from __future__ import annotations

from datetime import datetime, time, timezone, timedelta

CST = timezone(timedelta(hours=8))

SESSIONS: dict[str, list[tuple[time, time]]] = {
    "A股": [
        (time(9, 30), time(11, 30)),
        (time(13, 0), time(15, 0)),
    ],
    "港股": [
        (time(9, 30), time(12, 0)),
        (time(13, 0), time(16, 0)),
    ],
}


def _is_us_dst(when: datetime) -> bool:
    """Approximate US DST by the NYSE calendar rule.

    DST is active from the second Sunday of March to the first Sunday of
    November. For quote-polling windows this date-level rule is enough, and it
    avoids needing platform timezone databases on Windows.
    """
    year = when.year
    march_first = datetime(year, 3, 1)
    second_sunday_march = 1 + ((6 - march_first.weekday()) % 7) + 7
    dst_start = datetime(year, 3, second_sunday_march).date()

    november_first = datetime(year, 11, 1)
    first_sunday_november = 1 + ((6 - november_first.weekday()) % 7)
    dst_end = datetime(year, 11, first_sunday_november).date()

    return dst_start <= when.date() < dst_end


def _us_bounds(when: datetime) -> tuple[time, time]:
    """Return (evening_open, morning_close) in Beijing time."""
    if _is_us_dst(when):
        return time(21, 30), time(4, 0)
    return time(22, 30), time(5, 0)


def _is_us_market_open(when: datetime) -> bool:
    """US regular session in Beijing time, including cross-midnight Fridays.

    Summer:  Mon-Fri 21:30-23:59, Tue-Sat 00:00-04:00
    Winter:  Mon-Fri 22:30-23:59, Tue-Sat 00:00-05:00
    """
    evening_open, morning_close = _us_bounds(when)
    now_t = when.time()

    if evening_open <= now_t <= time(23, 59, 59):
        # Same Beijing date as the US trading day: Mon-Fri evenings.
        return when.weekday() < 5

    if time(0, 0) <= now_t <= morning_close:
        # Continuation of the previous Beijing evening: Tue-Sat mornings.
        previous_day = when - timedelta(days=1)
        return previous_day.weekday() < 5

    return False


def sessions_for(market: str, when: datetime | None = None) -> list[tuple[time, time]]:
    """Trading windows for a market at a given moment (Beijing time)."""
    when = (when or datetime.now(CST)).astimezone(CST)
    if market == "美股":
        evening_open, morning_close = _us_bounds(when)
        return [(evening_open, time(23, 59, 59)), (time(0, 0), morning_close)]
    return SESSIONS.get(market, [])


def is_market_open(market: str, when: datetime | None = None) -> bool:
    """True if the given market is within a regular trading session."""
    when = (when or datetime.now(CST)).astimezone(CST)

    if market == "美股":
        return _is_us_market_open(when)

    if when.weekday() >= 5:
        return False

    now_t = when.time()
    for start, end in sessions_for(market, when):
        if start <= now_t <= end:
            return True
    return False


def any_market_open(markets: list[str], when: datetime | None = None) -> bool:
    """True if at least one of the given markets is open at `when`."""
    return any(is_market_open(m, when) for m in markets)
