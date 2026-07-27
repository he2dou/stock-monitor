"""集成测试：mock数据源 → 引擎 → 通知器 全链路"""
import pytest
from unittest.mock import MagicMock, patch
from src.monitor import MonitorService


@pytest.fixture(autouse=True)
def _market_open():
    with patch("src.monitor.is_market_open", return_value=True):
        yield
from src.models import Quote, Alert, AlertRule

def test_full_pipeline_alert_triggered():
    """模拟一只股票触发了价格上限"""
    mock_source = MagicMock()
    mock_source.fetch_quotes.return_value = [
        Quote("AAPL", "Apple", "美股", 210, 5.0, 100000),
        Quote("159995", "芯片ETF", "A股", 1.2, 0.5, 5000000),
    ]
    rules = [{"symbol": "AAPL", "field": "price", "op": "above", "value": 200}]
    from src.alerts_engine import AlertEngine
    engine = AlertEngine(rules, cooldown_seconds=0)
    mock_notifier = MagicMock()

    svc = MonitorService(
        source=mock_source, alert_engine=engine,
        notifiers=[mock_notifier],
        stocks=[{"symbol": "AAPL", "name": "Apple", "market": "美股"},
                {"symbol": "159995", "name": "芯片ETF", "market": "A股"}])

    svc.run_once()

    mock_notifier.send.assert_called_once()
    sent_alerts = mock_notifier.send.call_args[0][0]
    assert len(sent_alerts) == 1
    assert sent_alerts[0].symbol == "AAPL"

def test_full_pipeline_100_stocks():
    """验证100只股票全量处理"""
    mock_source = MagicMock()
    stocks_config = [{"symbol": str(i).zfill(6), "name": f"S{i}",
                      "market": "A股"} for i in range(100)]
    mock_source.fetch_quotes.return_value = [
        Quote(str(i).zfill(6), f"S{i}", "A股", 10.0, 0.0, 1000)
        for i in range(100)]
    from src.alerts_engine import AlertEngine
    engine = AlertEngine([], cooldown_seconds=0)
    mock_notifier = MagicMock()

    svc = MonitorService(source=mock_source, alert_engine=engine,
                         notifiers=[mock_notifier], stocks=stocks_config)
    svc.run_once()
    assert len(mock_source.fetch_quotes.call_args[0][0]) == 100

