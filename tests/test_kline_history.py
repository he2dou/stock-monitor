from datetime import date

import pandas as pd
import pytest

from src.kline_history import (
    AkshareDailyBarSource,
    parse_nasdaq_historical,
    parse_yahoo_chart,
)


# -- Yahoo parser tests (unchanged) ----------------------------------------

def test_parse_yahoo_chart_daily_bars_filters_and_skips_nulls():
    payload = {
        "chart": {
            "result": [{
                "timestamp": [1704067200, 1704153600, 1704240000],
                "indicators": {
                    "quote": [{
                        "open": [10.0, None, 12.0],
                        "high": [11.0, None, 13.0],
                        "low": [9.0, None, 11.5],
                        "close": [10.5, None, 12.5],
                        "volume": [1000, 2000, 3000],
                    }],
                    "adjclose": [{"adjclose": [10.4, None, 12.4]}],
                },
            }],
            "error": None,
        }
    }

    bars = parse_yahoo_chart("SOXL", "SOXL", "缇庤偂", payload, date(2024, 1, 1), date(2024, 1, 3))

    assert len(bars) == 2
    assert bars[0].date == "2024-01-01"
    assert bars[0].close == 10.5
    assert bars[1].date == "2024-01-03"
    assert bars[1].adj_close == 12.4


# -- Nasdaq parser tests (unchanged) --------------------------------------

def test_parse_nasdaq_historical_sorts_rows_and_parses_numbers():
    payload = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": "07/30/2026",
                        "close": "$114.72",
                        "volume": "109,931,900",
                        "open": "$107.38",
                        "high": "$118.00",
                        "low": "$105.12",
                    },
                    {
                        "date": "07/29/2026",
                        "close": "91.99",
                        "volume": "149,950,200",
                        "open": "107.62",
                        "high": "112.6685",
                        "low": "91.50",
                    },
                ]
            }
        }
    }

    bars = parse_nasdaq_historical("SOXL", "SOXL", "缇庤偂", payload, date(2026, 7, 29), date(2026, 7, 30))

    assert [bar.date for bar in bars] == ["2026-07-29", "2026-07-30"]
    assert bars[0].close == 91.99
    assert bars[1].volume == 109931900
    assert bars[1].source == "nasdaq"


# -- akshare source tests -------------------------------------------------

def _make_us_df():
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "open": [10.0, 11.0, 12.0],
        "high": [10.5, 11.5, 12.5],
        "low": [9.5, 10.5, 11.5],
        "close": [10.2, 11.2, 12.2],
        "volume": [1000, 2000, 3000],
    })


def _make_hk_df():
    return pd.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        "open": [100.0, 101.0, 102.0],
        "high": [100.5, 101.5, 102.5],
        "low": [99.5, 100.5, 101.5],
        "close": [100.2, 101.2, 102.2],
        "volume": [5000, 6000, 7000],
    })


def _make_a_df():
    return pd.DataFrame({
        "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)],
        "open": [50.0, 51.0, 52.0],
        "high": [50.5, 51.5, 52.5],
        "low": [49.5, 50.5, 51.5],
        "close": [50.2, 51.2, 52.2],
        "volume": [10000, 20000, 30000],
    })


def test_akshare_fetch_us_bars(mocker):
    mocker.patch("src.kline_history.ak.stock_us_daily", return_value=_make_us_df())
    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("AAPL", "Apple", "缇庤偂", "2024-01-01", "2024-01-03")
    assert len(bars) == 3
    assert bars[0].date == "2024-01-01"
    assert bars[0].close == 10.2
    assert bars[0].source == "akshare"
    assert bars[2].close == 12.2


def test_akshare_fetch_us_filters_date_range(mocker):
    mocker.patch("src.kline_history.ak.stock_us_daily", return_value=_make_us_df())
    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("AAPL", "Apple", "缇庤偂", "2024-01-02", "2024-01-02")
    assert len(bars) == 1
    assert bars[0].date == "2024-01-02"


def test_akshare_fetch_hk_bars(mocker):
    mocker.patch("src.kline_history.ak.stock_hk_daily", return_value=_make_hk_df())
    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("00700", "Tencent", "娓偂", "2024-01-01", "2024-01-03")
    assert len(bars) == 3
    assert bars[0].close == 100.2
    assert bars[0].source == "akshare"


def test_akshare_fetch_a_bars(mocker):
    mocker.patch("src.kline_history.ak.stock_zh_a_daily", return_value=_make_a_df())
    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("600519", " Moutai", "A鑲?, "2024-01-01", "2024-01-03")
    assert len(bars) == 3
    assert bars[0].close == 50.2
    assert bars[0].source == "akshare"


def test_akshare_skips_none_rows(mocker):
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "open": [10.0, None],
        "high": [10.5, None],
        "low": [9.5, None],
        "close": [10.2, None],
        "volume": [1000, 2000],
    })
    mocker.patch("src.kline_history.ak.stock_us_daily", return_value=df)
    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("AAPL", "Apple", "缇庤偂", "2024-01-01", "2024-01-02")
    assert len(bars) == 1
    assert bars[0].date == "2024-01-01"


def test_akshare_rejects_reversed_dates():
    source = AkshareDailyBarSource()
    with pytest.raises(ValueError):
        source.fetch_daily_bars("AAPL", "Apple", "缇庤偂", "2024-01-05", "2024-01-01")