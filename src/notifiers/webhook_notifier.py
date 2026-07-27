import logging
import requests
from src.notifiers.base import Notifier
from src.models import Alert

logger = logging.getLogger(__name__)

class WebhookNotifier(Notifier):
    def __init__(self, url: str = "", timeout: int = 10):
        self.url = url
        self.timeout = timeout

    def send(self, alerts: list[Alert]) -> None:
        if not self.url or not alerts:
            return
        text = "\n".join(a.message for a in alerts)
        payload = {
            "msgtype": "text",
            "text": {"content": f"📊 股票预警\n{text}"},
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=self.timeout)
            if resp.status_code != 200:
                logger.warning(f"Webhook returned {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Webhook send failed: {e}")
