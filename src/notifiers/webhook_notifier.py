import logging
import requests
from src.notifiers.base import Notifier
from src.models import Alert

logger = logging.getLogger(__name__)


class WebhookNotifier(Notifier):
    """Pushes alerts to a group-bot webhook.

    Supports the three common Chinese IM bots by auto-detecting the platform
    from the URL and shaping the payload accordingly:

      - Feishu (飞书): https://open.feishu.cn/open-apis/bot/v2/hook/<id>
      - DingTalk (钉钉): https://oapi.dingtalk.com/robot/send?access_token=...
      - WeCom (企业微信): https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...

    All three report errors in the JSON body (often with HTTP 200), so we also
    inspect the response body, not just the status code, to detect failures.
    """

    def __init__(self, url: str = "", timeout: int = 10):
        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        # Bypass any local proxy (Clash/V2Ray) so the webhook POST is not dropped.
        self.session.trust_env = False

    def send(self, alerts: list[Alert]) -> None:
        if not self.url or not alerts:
            return
        text = "\n---\n".join(a.message for a in alerts)
        payload = self._build_payload(text)
        try:
            resp = self.session.post(self.url, json=payload, timeout=self.timeout)
        except Exception as e:
            logger.error(f"Webhook send failed: {e}")
            return
        if not self._is_success(resp):
            logger.warning(f"Webhook returned {resp.status_code}: {resp.text[:200]}")

    # -- payload shaping ----------------------------------------------------
    def _build_payload(self, text: str) -> dict:
        title = "股票通知"
        markdown = self._format_markdown(text)
        if "open.feishu.cn" in self.url:
            return {
                "msg_type": "interactive",
                "card": {
                    "config": {"wide_screen_mode": True},
                    "header": {"title": {"tag": "plain_text", "content": title}},
                    "elements": [{"tag": "markdown", "content": markdown}],
                },
            }
        if "oapi.dingtalk.com" in self.url:
            return {"msgtype": "markdown", "markdown": {"title": title, "text": markdown}}
        if "qyapi.weixin.qq.com" in self.url:
            return {"msgtype": "markdown", "markdown": {"content": markdown}}
        # Unknown platform: default to the DingTalk-compatible markdown shape.
        return {"msgtype": "markdown", "markdown": {"title": title, "text": markdown}}

    @staticmethod
    def _format_markdown(text: str) -> str:
        messages = [part.strip() for part in text.split("\n---\n") if part.strip()]
        if not messages:
            return ""
        return "\n\n---\n\n".join(messages)

    @staticmethod
    def _is_success(resp) -> bool:
        if resp.status_code != 200:
            return False
        try:
            body = resp.json()
        except ValueError:
            return True  # non-JSON 200; assume success
        # Feishu: StatusCode/code == 0; DingTalk: errcode == 0; WeCom: errcode == 0.
        for key in ("StatusCode", "code", "errcode"):
            if key in body:
                return int(body[key]) == 0
        return True
