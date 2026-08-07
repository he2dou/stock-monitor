"""Tests for YahooDailyBarSource local-proxy fallback."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.kline_history import YahooDailyBarSource


def _ok_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _boom_response():
    resp = MagicMock()
    resp.raise_for_status.side_effect = ConnectionError("blocked")
    return resp


def _sample_chart_payload():
    return {
        "chart": {
            "result": [{
                "timestamp": [1704067200],
                "indicators": {
                    "quote": [{
                        "open": [10.0],
                        "high": [11.0],
                        "low": [9.0],
                        "close": [10.5],
                        "volume": [1000],
                    }],
                    "adjclose": [{"adjclose": [10.4]}],
                },
            }],
            "error": None,
        }
    }


def test_direct_success_does_not_use_proxy():
    """When the direct request succeeds, the proxy is never touched."""
    source = YahooDailyBarSource(proxy="http://127.0.0.1:7890")
    payload = _sample_chart_payload()

    with patch("src.kline_history.requests.get", return_value=_ok_response(payload)) as mock_get,          patch("src.kline_history.requests.Session") as mock_session_cls:
        bars = source.fetch_daily_bars("AAPL", "Apple", "US", "2024-01-01", "2024-01-01")

    assert len(bars) == 1
    assert bars[0].close == 10.5
    # Direct request was made exactly once; no proxy session was created.
    assert mock_get.call_count == 1
    mock_session_cls.assert_not_called()


def test_falls_back_to_proxy_on_failure():
    """A failed direct request triggers a retry through the local proxy."""
    source = YahooDailyBarSource(proxy="http://127.0.0.1:7890")
    payload = _sample_chart_payload()

    proxy_session = MagicMock()
    proxy_session.get.return_value = _ok_response(payload)

    with patch("src.kline_history.requests.get", return_value=_boom_response()) as mock_get,          patch("src.kline_history.requests.Session", return_value=proxy_session) as mock_session_cls:
        bars = source.fetch_daily_bars("AAPL", "Apple", "US", "2024-01-01", "2024-01-01")

    assert len(bars) == 1
    assert bars[0].close == 10.5
    # Direct attempt was tried first.
    assert mock_get.call_count == 1
    # Proxy session was created and used.
    mock_session_cls.assert_called_once()
    proxy_session.get.assert_called_once()
    # trust_env must be disabled so NO_PROXY=* does not cancel the proxy.
    assert proxy_session.trust_env is False
    assert proxy_session.proxies == {
        "http": "http://127.0.0.1:7890",
        "https": "http://127.0.0.1:7890",
    }


def test_no_proxy_reraises_on_failure():
    """When the proxy is disabled, a failed direct request is not retried."""
    source = YahooDailyBarSource(proxy="")

    with patch("src.kline_history.requests.get", return_value=_boom_response()),          patch("src.kline_history.requests.Session") as mock_session_cls:
        with pytest.raises(ConnectionError):
            source.fetch_daily_bars("AAPL", "Apple", "US", "2024-01-01", "2024-01-01")

    mock_session_cls.assert_not_called()


def test_proxy_default_from_env(monkeypatch):
    """YAHOO_PROXY env var overrides the default proxy address."""
    monkeypatch.setenv("YAHOO_PROXY", "http://10.0.0.1:8888")
    source = YahooDailyBarSource()
    assert source.proxy == "http://10.0.0.1:8888"


def test_proxy_empty_env_disables(monkeypatch):
    """An empty YAHOO_PROXY env var disables the proxy fallback."""
    monkeypatch.setenv("YAHOO_PROXY", "")
    source = YahooDailyBarSource()
    assert source.proxy is None
