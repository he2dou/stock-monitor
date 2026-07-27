import pytest
from unittest.mock import MagicMock, patch
from src.monitor import MonitorService


@pytest.fixture(autouse=True)
def _market_open():
    """Default: treat market as open so existing tests are time-independent."""
    with patch("src.monitor.is_market_open", return_value=True):
        yield
from src.models import Quote, Alert, AlertRule

@pytest.fixture
def monitor_setup():
    mock_source = MagicMock()
    mock_source.fetch_quotes.return_value = [
        Quote("AAPL", "Apple", "美股", 210, 1.0, 100)]
    mock_engine = MagicMock()
    mock_notifier = MagicMock()
    return mock_source, mock_engine, mock_notifier

def test_monitor_run_fetches_and_checks(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    svc.run_once()
    src.fetch_quotes.assert_called_once()
    engine.check.assert_called_once()
    notifier.send.assert_called_once_with([])

def test_monitor_sends_alerts(monitor_setup):
    src, engine, notifier = monitor_setup
    alert = Alert("AAPL", "Apple", AlertRule("price", "above", 200), 210, "msg")
    engine.check.return_value = [alert]
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
    svc.run_once()
    notifier.send.assert_called_once_with([alert])

def test_monitor_logs_all_quotes(monitor_setup, caplog):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    import logging
    with caplog.at_level(logging.INFO):
        svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                             stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
        svc.run_once()
    assert any("AAPL" in r.message for r in caplog.records)


def test_monitor_skips_when_market_closed(monitor_setup):
    src, engine, notifier = monitor_setup
    with patch("src.monitor.is_market_open", return_value=False):
        svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                             stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}])
        svc.run_once()
    src.fetch_quotes.assert_not_called()
    engine.check.assert_not_called()
    notifier.send.assert_not_called()


def test_monitor_fetches_only_open_market_stocks(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    stocks = [
        {"symbol": "159995", "name": "芯片ETF", "market": "A股"},
        {"symbol": "00700", "name": "腾讯控股", "market": "港股"},
        {"symbol": "AAPL", "name": "Apple", "market": "美股"},
    ]
    with patch("src.monitor.is_market_open", side_effect=lambda market: market == "港股"):
        svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                             stocks=stocks)
        svc.run_once()
    src.fetch_quotes.assert_called_once_with([stocks[1]])

def test_monitor_reloads_watchlist_each_cycle(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    first = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]
    second = [{"symbol": "00700", "name": "腾讯控股", "market": "港股"}]
    stocks_loader = MagicMock(side_effect=[first, second])
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=first, stocks_loader=stocks_loader)

    svc.run_once()
    svc.run_once()

    assert src.fetch_quotes.call_args_list[0].args[0] == first
    assert src.fetch_quotes.call_args_list[1].args[0] == second


def test_monitor_reloads_alert_rules_each_cycle(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    rules1 = [{"symbol": "AAPL", "field": "price", "op": "above", "value": 200}]
    rules2 = [{"symbol": "AAPL", "field": "price", "op": "below", "value": 100}]
    rules_loader = MagicMock(side_effect=[rules1, rules2])
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"}],
                         rules_loader=rules_loader)

    svc.run_once()
    svc.run_once()

    assert engine.set_rules.call_args_list[0].args[0] == rules1
    assert engine.set_rules.call_args_list[1].args[0] == rules2


def test_monitor_keeps_previous_watchlist_when_reload_fails(monitor_setup):
    src, engine, notifier = monitor_setup
    engine.check.return_value = []
    original = [{"symbol": "AAPL", "name": "Apple", "market": "美股"}]
    stocks_loader = MagicMock(side_effect=Exception("bad yaml"))
    svc = MonitorService(source=src, alert_engine=engine, notifiers=[notifier],
                         stocks=original, stocks_loader=stocks_loader)

    svc.run_once()

    src.fetch_quotes.assert_called_once_with(original)
