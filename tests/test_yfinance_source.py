import pytest
from unittest.mock import patch, MagicMock
from src.sources.yfinance_source import YfinanceSource

def test_fetch_us_stock():
    mock_ticker = MagicMock()
    mock_ticker.info = {"regularMarketPrice": 195.5, "regularMarketChangePercent": -1.2,
                        "regularMarketVolume": 50000, "shortName": "Apple"}
    with patch("src.sources.yfinance_source.yf.Ticker", return_value=mock_ticker):
        source = YfinanceSource()
        stocks = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]
        quotes = source.fetch_quotes(stocks)
        assert len(quotes) == 1
        assert quotes[0].price == 195.5

def test_fetch_skip_non_us():
    source = YfinanceSource()
    stocks = [{"symbol": "159995", "name": "芯片ETF", "market": "A股"}]
    quotes = source.fetch_quotes(stocks)
    assert quotes == []

def test_fetch_handles_error():
    with patch("src.sources.yfinance_source.yf.Ticker",
               side_effect=Exception("Network error")):
        source = YfinanceSource()
        stocks = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]
        quotes = source.fetch_quotes(stocks)
        assert quotes == []
