from src.notifiers.base import Notifier
from src.models import Alert

class ConsoleNotifier(Notifier):
    def send(self, alerts: list[Alert]) -> None:
        for a in alerts:
            title = "PRICE ALERT" if hasattr(a, "rule") else "PAPER TRADE"
            print(f"\n{'='*60}")
            print(f"  🚨 {title}")
            print(f"  {a.message}")
            print(f"{'='*60}\n")
