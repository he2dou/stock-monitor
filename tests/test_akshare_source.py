import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from src.sources.akshare_source import AkshareSource

def test_fetch_a_stock(mock_akshare_a):
    source = AkshareSource()
    stocks = [{"symbol": "159995", "name": "芯片ETF", "market": "A股"}]
    quotes = source.fetch_quotes(stocks)
    assert len(quotes) == 1
    assert quotes[0].symbol == "159995"
    assert quotes[0].price == 1.234
    assert quotes[0].market == "A股"

def test_fetch_multiple_markets(mock_akshare_all):
    source = AkshareSource()
    stocks = [
        {"symbol": "159995", "name": "芯片ETF", "market": "A股"},
        {"symbol": "AAPL", "name": "Apple", "market": "美股"},
        {"symbol": "00700", "name": "腾讯", "market": "港股"},
    ]
    quotes = source.fetch_quotes(stocks)
    assert len(quotes) == 3
    markets = {q.market for q in quotes}
    assert markets == {"A股", "美股", "港股"}

def test_fetch_handles_api_error(mock_akshare_error):
    source = AkshareSource()
    stocks = [{"symbol": "159995", "name": "芯片ETF", "market": "A股"}]
    quotes = source.fetch_quotes(stocks)
    assert quotes == []

@pytest.fixture
def mock_akshare_a():
    df = pd.DataFrame({
        "代码": ["159995"], "名称": ["芯片ETF"],
        "最新价": [1.234], "涨跌幅": [2.5], "成交量": [1000000],
    })
    with patch("src.sources.akshare_source.ak.stock_zh_a_spot_em", return_value=df):
        yield

@pytest.fixture
def mock_akshare_all():
    # The implementation calls each akshare method without market args
    # (e.g. ak.stock_hk_spot_em()), so dispatch on the method itself, not args.
    df_a = pd.DataFrame({
        "代码": ["159995"], "名称": ["芯片ETF"],
        "最新价": [1.234], "涨跌幅": [2.5], "成交量": [1000000]})
    df_hk = pd.DataFrame({
        "代码": ["00700"], "名称": ["腾讯"],
        "最新价": [350.0], "涨跌幅": [1.5], "成交量": [50000]})
    df_us = pd.DataFrame({
        "代码": ["AAPL"], "名称": ["Apple"],
        "最新价": [195.0], "涨跌幅": [-0.5], "成交量": [20000]})
    with patch("src.sources.akshare_source.ak", MagicMock()) as mock_ak:
        mock_ak.stock_zh_a_spot_em = MagicMock(return_value=df_a)
        mock_ak.stock_hk_spot_em = MagicMock(return_value=df_hk)
        mock_ak.stock_us_spot_em = MagicMock(return_value=df_us)
        yield

@pytest.fixture
def mock_akshare_error():
    with patch("src.sources.akshare_source.ak.stock_zh_a_spot_em",
               side_effect=Exception("API timeout")):
        yield
