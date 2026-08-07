"""Tests for AkshareDailyBarSource A-share ETF vs stock routing."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.kline_history import AkshareDailyBarSource


def _fund_df():
    # Columns returned by ak.fund_etf_hist_sina
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "open": [1.6, 1.65, 1.7],
        "high": [1.62, 1.68, 1.72],
        "low": [1.58, 1.63, 1.68],
        "close": [1.61, 1.67, 1.71],
        "volume": [100000, 200000, 300000],
    })


def _stock_df():
    # Columns returned by ak.stock_zh_a_daily
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]),
        "open": [50.0, 51.0, 52.0],
        "high": [50.5, 51.5, 52.5],
        "low": [49.5, 50.5, 51.5],
        "close": [50.2, 51.2, 52.2],
        "volume": [10000, 20000, 30000],
    })


def test_a_share_etf_uses_fund_function(mocker):
    """ETF codes (159326) must route to fund_etf_hist_sina, not stock_zh_a_daily."""
    fund_mock = mocker.patch("akshare.fund_etf_hist_sina", return_value=_fund_df())
    stock_mock = mocker.patch("akshare.stock_zh_a_daily", return_value=_stock_df())

    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("159326", "半导体ETF", "A股", "2024-01-01", "2024-01-03")

    assert len(bars) == 3
    assert bars[0].close == 1.61
    assert bars[0].source == "akshare"
    # The Sina fund function is called with the sz-prefixed symbol.
    fund_mock.assert_called_once_with(symbol="sz159326")
    stock_mock.assert_not_called()


def test_a_share_stock_uses_stock_function(mocker):
    """Stock codes (600519) route to stock_zh_a_daily with the sh prefix."""
    fund_mock = mocker.patch("akshare.fund_etf_hist_sina", return_value=_fund_df())
    stock_mock = mocker.patch("akshare.stock_zh_a_daily", return_value=_stock_df())

    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("600519", "贵州茅台", "A股", "2024-01-01", "2024-01-03")

    assert len(bars) == 3
    assert bars[0].close == 50.2
    stock_mock.assert_called_once_with(symbol="sh600519", adjust="qfq")
    fund_mock.assert_not_called()


def test_a_share_shanghai_etf_prefix(mocker):
    """Shanghai ETF (510300) uses the sh prefix with the fund function."""
    fund_mock = mocker.patch("akshare.fund_etf_hist_sina", return_value=_fund_df())
    mocker.patch("akshare.stock_zh_a_daily", return_value=_stock_df())

    source = AkshareDailyBarSource()
    source.fetch_daily_bars("510300", "沪深300ETF", "A股", "2024-01-01", "2024-01-03")

    fund_mock.assert_called_once_with(symbol="sh510300")


def test_a_share_etf_filters_date_range(mocker):
    mocker.patch("akshare.fund_etf_hist_sina", return_value=_fund_df())
    mocker.patch("akshare.stock_zh_a_daily", return_value=_stock_df())

    source = AkshareDailyBarSource()
    bars = source.fetch_daily_bars("159326", "半导体ETF", "A股", "2024-01-02", "2024-01-02")
    assert len(bars) == 1
    assert bars[0].date == "2024-01-02"
