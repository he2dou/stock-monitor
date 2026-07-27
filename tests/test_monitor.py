import pytest
from unittest.mock import MagicMock, patch
from src.monitor import MonitorService
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
