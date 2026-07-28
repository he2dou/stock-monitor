import pytest
from unittest.mock import MagicMock, patch
from src.sources.sinatx_source import SinaTxSource


def _resp(content_bytes, status=200):
    r = MagicMock()
    r.status_code = status
    r.raise_for_status.return_value = None
    r.content = content_bytes
    return r


# Full real Tencent payloads (GBK), so field index 32 == change_pct aligns.
TX_A = 'v_sz159995="51~芯片ETF华夏~159995~1.272~1.270~1.260~6652677~3489216~3162553~1.272~1931~1.271~76979~1.270~62202~1.269~68400~1.268~71315~1.273~5565~1.274~35554~1.275~39474~1.276~9951~1.277~4107~~20260727145133~0.002~0.16~1.288~1.208~1.272/6652677/833108453~6652677~83311~2.93~~~1.288~1.208~6.30~289.08~289.08~0.00~1.397~1.143~0.61~186176~1.252~~~~~~83310.8453~0.0000~0~   A~ETF~46.88~6.27~~~~1.750~0.621~-12.76~-22.86~28.48~22726126012~22726126012~49.58~35.18~22726126012~-0.02~1.2723~99.06~0.08~1.2698~CNY~0~~1.280~-30796~";'
TX_HK = 'v_hk00700="100~腾讯控股~00700~443.400~434.600~438.800~9685023.0~0~0~443.400~0~0~0~0~0~0~0~0~0~443.400~0~0~0~0~0~0~0~0~0~9685023.0~2026/07/27 14:36:32~8.800~2.02~446.400~435.400~443.400~9685023.0~4271916890.572~0~16.20~~0~0~2.53~40316.2172~40316.2172~TENCENT~1.20~677.700~411.000~0.39~-14.80~0~0~0~0~0~15.14~3.20~0.11~100~-25.32~-7.20~GP~20.59~11.53~-3.10~7.67~-5.36~9092516289.00~9092516289.00~15.32~5.315~441.085~-30.69~HKD~1~30";'
TX_US = 'v_usAAPL="200~苹果~AAPL.OQ~333.02~321.66~321.79~47489415~0~0~333.72~40~0~0~0~0~0~0~0~0~333.86~960~0~0~0~0~0~0~0~0~~2026-07-24 16:00:01~11.36~3.53~334.37~321.62~USD~47489415~15737693988~0.32~40.32~~44.64~~3.96~48881.62430~48911.83295~Apple Inc.~8.26~334.99~200.72~-920~45.93~0.32~48911.83295~22.72~-0.22~GP~141.47~34.91~5.61~21.03~23.13~14687356000~14678284878~1.00~34.33~1.05~331.39~~~";'

SN_A = 'var hq_str_sz159995="芯片ETF,1.260,1.270,1.272,1.288,1.208,1.272,1.273,665383718,833256048.591,136900,1.272,7725200,1.271,6302000,1.270,6761100,1.269,7131500,1.268,510100,1.273,3476900,1.274,3969500,1.275,995100,1.276,400700,1.277,2026-07-27,14:51:36,00";'
SN_HK = 'var hq_str_hk00700="TENCENT,腾讯控股,438.800,434.600,446.400,435.400,443.400,8.800,2.025,443.20001,443.39999,4193772385,9508850,0.000,0.000,675.134,411.000,2026/07/27,14:25";'
SN_US = 'var hq_str_gb_aapl="苹果,333.0200,3.53,2026-07-25 09:46:26,11.3600,321.7900,334.3700,321.6200,334.9900,200.4500,47489415,47546553,4891183295120,8.30,40.120000,0.00,0.00,0.00,0.00,14687356000,63,333.8000,0.23,0.78,Jul 24 08:01PM EDT,Jul 24 04:00PM EDT,321.6600,2143647,1,2026,15740806625.0000,333.8800,321.8200,713975249.0749,333.0200,321.6600";'


@pytest.mark.parametrize("market,symbol,expected", [
    ("A股", "600519", "sh600519"),
    ("A股", "562500", "sh562500"),
    ("A股", "159995", "sz159995"),
    ("A股", "000001", "sz000001"),
    ("A股", "300750", "sz300750"),
    ("港股", "00700", "hk00700"),
    ("美股", "AAPL", "usAAPL"),
])
def test_tencent_symbol(market, symbol, expected):
    assert SinaTxSource._tencent_symbol({"market": market, "symbol": symbol}) == expected


@pytest.mark.parametrize("market,symbol,expected", [
    ("A股", "159995", "sz159995"),
    ("港股", "00700", "hk00700"),
    ("美股", "AAPL", "gb_aapl"),     # Sina uses gb_ + lowercase for US
    ("美股", "TSLA", "gb_tsla"),
])
def test_sina_symbol(market, symbol, expected):
    assert SinaTxSource._sina_symbol({"market": market, "symbol": symbol}) == expected


def test_tencent_fetch_all_markets_single_request():
    batch = TX_A + TX_HK + TX_US
    session = MagicMock()
    session.get.return_value = _resp(batch.encode("gbk"))
    src = SinaTxSource(session=session, max_retries=0)
    stocks = [
        {"symbol": "159995", "name": "芯片ETF", "market": "A股"},
        {"symbol": "00700", "name": "腾讯", "market": "港股"},
        {"symbol": "AAPL", "name": "Apple", "market": "美股"},
    ]
    quotes = src.fetch_quotes(stocks)
    assert len(quotes) == 3
    assert session.get.call_count == 1
    assert session.get.call_args[0][0].startswith("https://qt.gtimg.cn/q=")
    by = {q.symbol: q for q in quotes}
    assert by["159995"].price == pytest.approx(1.272)
    assert by["159995"].change_pct == pytest.approx(0.16)
    assert by["00700"].price == pytest.approx(443.400)
    assert by["00700"].change_pct == pytest.approx(2.02)
    assert by["AAPL"].price == pytest.approx(333.02)
    assert by["AAPL"].change_pct == pytest.approx(3.53)


def test_tencent_names_decoded_from_gbk():
    session = MagicMock()
    session.get.return_value = _resp(TX_A.encode("gbk"))
    src = SinaTxSource(session=session, max_retries=0)
    q = src.fetch_quotes([{"symbol": "159995", "name": "x", "market": "A股"}])[0]
    assert q.name == "芯片ETF华夏"


def test_sina_fallback_when_tencent_misses_symbol():
    session = MagicMock()
    session.get.side_effect = [
        _resp(TX_HK.encode("gbk")),   # tencent: only HK present, AAPL missing
        _resp(SN_US.encode("gbk")),   # sina: fills AAPL via gb_aapl
    ]
    src = SinaTxSource(session=session, max_retries=0)
    quotes = src.fetch_quotes([
        {"symbol": "00700", "name": "腾讯", "market": "港股"},
        {"symbol": "AAPL", "name": "Apple", "market": "美股"},
    ])
    assert {q.symbol for q in quotes} == {"00700", "AAPL"}
    aapl = next(q for q in quotes if q.symbol == "AAPL")
    assert aapl.price == pytest.approx(333.02)
    sina_url = session.get.call_args_list[1][0][0]
    assert sina_url.startswith("https://hq.sinajs.cn/list=")
    assert "gb_aapl" in sina_url


def test_sina_parse_a_share_computes_change_pct():
    session = MagicMock()
    session.get.side_effect = [_resp(b""), _resp(SN_A.encode("gbk"))]
    src = SinaTxSource(session=session, max_retries=0)
    q = src.fetch_quotes([{"symbol": "159995", "name": "芯片ETF", "market": "A股"}])[0]
    assert q.price == pytest.approx(1.272)
    assert q.change_pct == pytest.approx((1.272 - 1.270) / 1.270 * 100, rel=1e-6)


def test_sina_parse_hk():
    session = MagicMock()
    session.get.side_effect = [_resp(b""), _resp(SN_HK.encode("gbk"))]
    src = SinaTxSource(session=session, max_retries=0)
    q = src.fetch_quotes([{"symbol": "00700", "name": "腾讯", "market": "港股"}])[0]
    assert q.price == pytest.approx(443.400)
    assert q.change_pct == pytest.approx(2.025)


def test_tencent_connection_error_falls_back_to_sina():
    import requests
    session = MagicMock()
    session.get.side_effect = [requests.ConnectionError("x"), _resp(SN_US.encode("gbk"))]
    with patch("src.sources.sinatx_source.time.sleep"):
        src = SinaTxSource(session=session, max_retries=0)
        quotes = src.fetch_quotes([{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    assert len(quotes) == 1 and quotes[0].price == pytest.approx(333.02)


def test_unknown_market_yields_nothing():
    session = MagicMock()
    session.get.return_value = _resp(b"")
    src = SinaTxSource(session=session, max_retries=0)
    assert src.fetch_quotes([{"symbol": "X", "market": "期货"}]) == []


def test_empty_watchlist():
    session = MagicMock()
    src = SinaTxSource(session=session, max_retries=0)
    assert src.fetch_quotes([]) == []
    session.get.assert_not_called()



def test_provider_exception_does_not_crash_caller():
    # Any provider raising must be isolated: never propagate, just degrade.
    with patch.object(SinaTxSource, "_fetch_via_tencent", side_effect=RuntimeError("boom")), \
         patch.object(SinaTxSource, "_fetch_via_sina", side_effect=RuntimeError("boom2")):
        src = SinaTxSource(max_retries=0)
        assert src.fetch_quotes([{"symbol": "AAPL", "market": "美股"}]) == []

def test_trust_env_disabled():
    src = SinaTxSource()
    assert src.session.trust_env is False


def test_provider_symbol_overrides_default_mapping():
    stock = {"symbol": ".DJI", "market": "美股", "tencent_symbol": "usDJI", "sina_symbol": "gb_dji"}
    assert SinaTxSource._tencent_symbol(stock) == "usDJI"
    assert SinaTxSource._sina_symbol(stock) == "gb_dji"


def test_generic_provider_symbol_override_applies_to_both_sources():
    stock = {"symbol": "000001", "market": "A股", "provider_symbol": "sh000001"}
    assert SinaTxSource._tencent_symbol(stock) == "sh000001"
    assert SinaTxSource._sina_symbol(stock) == "sh000001"