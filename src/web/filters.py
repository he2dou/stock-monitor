"""Custom Jinja2 filters shared across all templates."""
from __future__ import annotations

from datetime import datetime


def fmt_time(value) -> str:
    """Format an ISO-8601 timestamp as 'YYYY-MM-DD HH:MM:SS'.

    Strips the timezone offset and microseconds, keeping the stored clock time
    intact: '2026-07-30T02:01:50.098445+00:00' -> '2026-07-30 02:01:50'.

    Renders None/empty as an empty string. Pure-date values (no time
    component, e.g. snapshot_date) are returned unchanged. Unparseable strings
    fall through unchanged so a bad value never blanks out the page.
    """
    if value in (None, "", "-"):
        return ""
    text = str(value)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return text
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def register_template_filters(env) -> None:
    env.filters["fmt_time"] = fmt_time
