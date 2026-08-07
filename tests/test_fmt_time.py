"""Tests for the fmt_time Jinja template filter."""
from __future__ import annotations

from src.web.filters import fmt_time


def test_fmt_time_iso_with_tz_and_micros():
    assert fmt_time("2026-07-30T02:01:50.098445+00:00") == "2026-07-30 02:01:50"


def test_fmt_time_iso_without_offset():
    assert fmt_time("2026-07-30T02:01:50") == "2026-07-30 02:01:50"


def test_fmt_time_none_returns_empty():
    assert fmt_time(None) == ""


def test_fmt_time_dash_returns_empty():
    assert fmt_time("-") == ""


def test_fmt_time_empty_string_returns_empty():
    assert fmt_time("") == ""


def test_fmt_time_unparseable_returns_as_is():
    assert fmt_time("not-a-date") == "not-a-date"


def test_filter_registered_on_env():
    """The filter must be registered on the Jinja env used by the app."""
    from src.web.filters import register_template_filters

    class FakeEnv:
        filters = {}

    env = FakeEnv()
    register_template_filters(env)
    assert "fmt_time" in env.filters
    assert env.filters["fmt_time"] is fmt_time
