import pytest
from unittest.mock import patch, MagicMock
from src.notifiers.console_notifier import ConsoleNotifier
from src.notifiers.webhook_notifier import WebhookNotifier
from src.models import Alert, AlertRule

@pytest.fixture
def sample_alert():
    return Alert(symbol="AAPL", name="Apple",
                 rule=AlertRule("price", "above", 200),
                 current_value=210,
                 message="⚠️ Apple(AAPL) price above 200 | 当前: 210.0")

def test_console_notifier(sample_alert, capsys):
    notifier = ConsoleNotifier()
    notifier.send([sample_alert])
    captured = capsys.readouterr()
    assert "AAPL" in captured.out

def test_webhook_notifier_calls_requests(sample_alert):
    with patch("src.notifiers.webhook_notifier.requests.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=200)
        notifier = WebhookNotifier(url="https://oapi.dingtalk.com/robot/send?access_token=xxx")
        notifier.send([sample_alert])
        assert mock_post.called
        call_args = mock_post.call_args
        assert "text" in str(call_args) or "msgtype" in str(call_args)

def test_webhook_notifier_no_url_skips():
    notifier = WebhookNotifier(url="")
    notifier.send([])

def test_webhook_notifier_handles_error(sample_alert):
    with patch("src.notifiers.webhook_notifier.requests.post",
               side_effect=Exception("Network error")):
        notifier = WebhookNotifier(url="https://example.com/hook")
        notifier.send([sample_alert])
