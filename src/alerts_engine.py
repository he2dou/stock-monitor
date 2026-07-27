import logging
import time
from src.models import Quote, Alert, AlertRule

logger = logging.getLogger(__name__)

class AlertEngine:
    def __init__(self, rules: list[dict], cooldown_seconds: int = 300):
        self.rules: dict[str, list[AlertRule]] = {}
        for r in rules:
            rule = AlertRule(field=r["field"], op=r["op"], value=r["value"])
            self.rules.setdefault(r["symbol"], []).append(rule)
        self.cooldown = cooldown_seconds
        self._last_triggered: dict[str, float] = {}

    def check(self, quotes: list[Quote]) -> list[Alert]:
        alerts: list[Alert] = []
        now = time.time()
        for q in quotes:
            rules = self.rules.get(q.symbol, [])
            for rule in rules:
                current = getattr(q, rule.field, None)
                if current is None:
                    continue
                if not rule.matches(current):
                    continue
                dedup_key = f"{q.symbol}:{rule.field}:{rule.op}:{rule.value}"
                last = self._last_triggered.get(dedup_key, 0)
                if now - last < self.cooldown:
                    continue
                self._last_triggered[dedup_key] = now
                msg = (f"⚠️ {q.name}({q.symbol}) {rule.field} {rule.op} "
                       f"{rule.value} | 当前: {current:.4f} | 涨跌: {q.change_pct:+.2f}%")
                alerts.append(Alert(
                    symbol=q.symbol, name=q.name, rule=rule,
                    current_value=current, message=msg,
                ))
                logger.info(f"Alert triggered: {msg}")
        return alerts
