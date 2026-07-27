from src.notifiers.base import Notifier
from src.models import Alert

class ConsoleNotifier(Notifier):
    def send(self, alerts: list[Alert]) -> None:
        for a in alerts:
            print(f"\n{'='*60}")
            print(f"  🚨 PRICE ALERT")
            print(f"  {a.message}")
            print(f"{'='*60}\n")
