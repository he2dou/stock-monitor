import pytest
import requests
from unittest.mock import MagicMock, patch
from src.sources.eastmoney_source import EastmoneySource


def _resp(payload):
    """Build a fake requests.Response-like object."""
    r = MagicMock()
    r.raise_for_status.return_value = None
    r.json.return_value = payload
    return r


def _quote_payload(code, name, price, change, volume):
    return {"data": {"f57": code, "f58": name, "f43": price,
                     "f170": change, "f47": volume}}


def _secid_of(session):
    """Extract the secid from the (first) GET call's params."""
    _, kwargs = session.get.call_args
    return kwargs["params"]["secid"]


# --- secid mapping (pure logic) -------------------------------------------

@pytest.mark.parametrize("market,symbol,expected", [
    ("A股", "600000", ["1.600000"]),   # SH stock
    ("A股", "688981", ["1.688981"]),   # SH STAR
    ("A股", "562500", ["1.562500"]),   # SH ETF
    ("A股", "000001", ["0.000001"]),   # SZ stock
    ("A股", "300750", ["0.300750"]),   # SZ ChiNext
    ("A股", "159995", ["0.159995"]),   # SZ ETF
    ("港股", "00700", ["116.00700"]),
    ("美股", "AAPL", ["105.AAPL", "106.AAPL"]),  # NASDAQ then NYSE fallback
])
def test_secid_candidates(market, symbol, expected):
    assert EastmoneySource._secid_candidates(market, symbol) == expected


# --- end-to-end fetch with mocked HTTP ------------------------------------

def test_fetch_a_share_sz_etf():
    session = MagicMock()
    session.get.return_value = _resp(_quote_payload("159995", "芯片ETF", 1.234, 2.5, 1000000))
    src = EastmoneySource(session=session, delay=0, max_retries=0)

    quotes = src.fetch_quotes([{"symbol": "159995", "name": "芯片ETF", "market": "A股"}])

    assert len(quotes) == 1
    q = quotes[0]
    assert (q.symbol, q.name, q.market) == ("159995", "芯片ETF", "A股")
    assert q.price == 1.234 and q.change_pct == 2.5 and q.volume == 1000000
    assert _secid_of(session) == "0.159995"


def test_fetch_a_share_sh_etf_uses_shanghai_prefix():
    session = MagicMock()
    session.get.return_value = _resp(_quote_payload("562500", "机器人ETF", 1.0, 0.0, 0))
    src = EastmoneySource(session=session, delay=0, max_retries=0)

    src.fetch_quotes([{"symbol": "562500", "name": "机器人ETF", "market": "A股"}])
    assert _secid_of(session) == "1.562500"


def test_fetch_hk_stock():
    session = MagicMock()
    session.get.return_value = _resp(_quote_payload("00700", "腾讯控股", 350.0, 1.5, 50000))
    src = EastmoneySource(session=session, delay=0, max_retries=0)

    quotes = src.fetch_quotes([{"symbol": "00700", "name": "腾讯", "market": "港股"}])
    assert len(quotes) == 1 and quotes[0].price == 350.0
    assert _secid_of(session) == "116.00700"


def test_fetch_us_nasdaq_first_try():
    session = MagicMock()
    session.get.return_value = _resp(_quote_payload("AAPL", "Apple", 195.0, -0.5, 20000))
    src = EastmoneySource(session=session, delay=0, max_retries=0)

    quotes = src.fetch_quotes([{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    assert len(quotes) == 1 and quotes[0].price == 195.0
    assert session.get.call_count == 1  # no NYSE fallback needed
    assert _secid_of(session) == "105.AAPL"


def test_fetch_us_falls_back_to_nyse():
    session = MagicMock()
    # NASDAQ (105) returns no data, NYSE (106) returns the quote
    session.get.side_effect = [
        _resp({"data": None}),
        _resp(_quote_payload("BRK.A", "Berkshire", 540000.0, 0.3, 100)),
    ]
    src = EastmoneySource(session=session, delay=0, max_retries=0)

    quotes = src.fetch_quotes([{"symbol": "BRK.A", "name": "Berkshire", "market": "美股"}])
    assert len(quotes) == 1 and quotes[0].price == 540000.0
    assert session.get.call_count == 2
    # second call used the NYSE prefix
    second_kwargs = session.get.call_args_list[1].kwargs
    assert second_kwargs["params"]["secid"] == "106.BRK.A"


def test_fetch_multiple_markets():
    session = MagicMock()
    session.get.side_effect = [
        _resp(_quote_payload("159995", "芯片ETF", 1.2, 1.0, 100)),
        _resp(_quote_payload("AAPL", "Apple", 195.0, -0.5, 20000)),
        _resp(_quote_payload("00700", "腾讯", 350.0, 1.5, 50000)),
    ]
    src = EastmoneySource(session=session, delay=0, max_retries=0)
    stocks = [
        {"symbol": "159995", "name": "芯片ETF", "market": "A股"},
        {"symbol": "AAPL", "name": "Apple", "market": "美股"},
        {"symbol": "00700", "name": "腾讯", "market": "港股"},
    ]
    quotes = src.fetch_quotes(stocks)
    assert len(quotes) == 3
    assert {q.market for q in quotes} == {"A股", "美股", "港股"}


# --- robustness -----------------------------------------------------------

def test_invalid_price_is_skipped():
    session = MagicMock()
    session.get.return_value = _resp({"data": {"f57": "159995", "f58": "x", "f43": "-",
                                               "f170": 0, "f47": 0}})
    src = EastmoneySource(session=session, delay=0, max_retries=0)
    assert src.fetch_quotes([{"symbol": "159995", "market": "A股"}]) == []


def test_connection_error_yields_no_quote():
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("RemoteDisconnected")
    src = EastmoneySource(session=session, delay=0, max_retries=0)
    assert src.fetch_quotes([{"symbol": "159995", "market": "A股"}]) == []


def test_retries_on_transient_error_then_succeeds():
    session = MagicMock()
    session.get.side_effect = [
        requests.ConnectionError("blocked"),
        _resp(_quote_payload("159995", "芯片ETF", 1.2, 1.0, 100)),
    ]
    with patch("src.sources.eastmoney_source.time.sleep") as mock_sleep:
        src = EastmoneySource(session=session, delay=0, max_retries=2, backoff=15.0)
        quotes = src.fetch_quotes([{"symbol": "159995", "market": "A股"}])
    assert len(quotes) == 1 and quotes[0].price == 1.2
    assert session.get.call_count == 2
    mock_sleep.assert_called()  # backed off before the retry


def test_spacing_sleep_between_symbols_not_after_last():
    session = MagicMock()
    session.get.return_value = _resp(_quote_payload("159995", "x", 1.0, 0.0, 0))
    with patch("src.sources.eastmoney_source.time.sleep") as mock_sleep:
        src = EastmoneySource(session=session, delay=1.0, max_retries=0)
        src.fetch_quotes([{"symbol": "159995", "market": "A股"},
                          {"symbol": "000001", "market": "A股"},
                          {"symbol": "600000", "market": "A股"}])
    # 3 symbols -> 2 gaps (no sleep after the last)
    spacing_calls = [c for c in mock_sleep.call_args_list if c.args[0] == 1.0]
    assert len(spacing_calls) == 2


def test_name_falls_back_to_watchlist_name():
    session = MagicMock()
    session.get.return_value = _resp({"data": {"f57": "159995", "f43": 1.0,
                                               "f170": 0, "f47": 0}})  # no f58
    src = EastmoneySource(session=session, delay=0, max_retries=0)
    quotes = src.fetch_quotes([{"symbol": "159995", "name": "我的ETF", "market": "A股"}])
    assert len(quotes) == 1 and quotes[0].name == "我的ETF"
