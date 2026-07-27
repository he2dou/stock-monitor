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


def _ok(body=None):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = body or {"StatusCode": 0, "msg": "success"}
    r.text = str(body or {})
    return r


def test_console_notifier(sample_alert, capsys):
    ConsoleNotifier().send([sample_alert])
    captured = capsys.readouterr()
    assert "AAPL" in captured.out


def test_webhook_no_url_skips(sample_alert):
    # Empty url or empty alerts must never POST.
    with _patch_post() as mock_post:
        WebhookNotifier(url="").send([sample_alert])
        WebhookNotifier(url="https://example.com/hook").send([])
    mock_post.assert_not_called()


def _patch_post():
    # Patch the Session.post used by the notifier so no real network happens.
    return patch("requests.sessions.Session.post")


def test_webhook_dingtalk_payload_shape(sample_alert):
    with _patch_post() as mock_post:
        mock_post.return_value = _ok({"errcode": 0})
        WebhookNotifier(url="https://oapi.dingtalk.com/robot/send?access_token=x").send([sample_alert])
    assert mock_post.called
    payload = mock_post.call_args.kwargs["json"]
    assert payload == {"msgtype": "text", "text": {"content": "【股票预警】\n" + sample_alert.message}}


def test_webhook_feishu_payload_shape(sample_alert):
    with _patch_post() as mock_post:
        mock_post.return_value = _ok({"StatusCode": 0, "msg": "success"})
        WebhookNotifier(url="https://open.feishu.cn/open-apis/bot/v2/hook/abc").send([sample_alert])
    payload = mock_post.call_args.kwargs["json"]
    assert payload == {"msg_type": "text", "content": {"text": "【股票预警】\n" + sample_alert.message}}


def test_webhook_wecom_payload_shape(sample_alert):
    with _patch_post() as mock_post:
        mock_post.return_value = _ok({"errcode": 0})
        WebhookNotifier(url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=k").send([sample_alert])
    payload = mock_post.call_args.kwargs["json"]
    assert "content" in str(payload)


def test_webhook_unknown_url_defaults_to_text(sample_alert):
    with _patch_post() as mock_post:
        mock_post.return_value = _ok({"errcode": 0})
        WebhookNotifier(url="https://example.com/hook").send([sample_alert])
    assert mock_post.call_args.kwargs["json"]["msgtype"] == "text"


def test_webhook_detects_body_error_as_failure(sample_alert, caplog):
    # Feishu returns HTTP 200 but an error body -> must be logged as failure.
    with _patch_post() as mock_post:
        mock_post.return_value = _ok({"code": 19002, "msg": "params error"})
        WebhookNotifier(url="https://open.feishu.cn/open-apis/bot/v2/hook/abc").send([sample_alert])
    assert any("returned 200" in r.getMessage() for r in caplog.records)


def test_webhook_handles_network_exception(sample_alert, caplog):
    with _patch_post() as mock_post:
        mock_post.side_effect = Exception("Network error")
        WebhookNotifier(url="https://example.com/hook").send([sample_alert])
    assert any("Webhook send failed" in r.getMessage() for r in caplog.records)


def test_webhook_trust_env_disabled(sample_alert):
    n = WebhookNotifier(url="https://oapi.dingtalk.com/robot/send?access_token=x")
    assert n.session.trust_env is False

