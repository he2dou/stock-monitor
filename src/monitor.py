import logging
from src.market_hours import any_market_open
from src.sources.base import DataSource
from src.alerts_engine import AlertEngine
from src.notifiers.base import Notifier
from src.models import Quote

logger = logging.getLogger(__name__)

class MonitorService:
    """核心监控编排：拉数据 → 查规则 → 发通知"""

    def __init__(self, source: DataSource, alert_engine: AlertEngine,
                 notifiers: list[Notifier], stocks: list[dict]):
        self.source = source
        self.engine = alert_engine
        self.notifiers = notifiers
        self.stocks = stocks
        # Distinct markets in the watchlist, e.g. {"A股", "港股", "美股"}.
        self._markets = sorted({s.get("market", "") for s in self.stocks})

    def run_once(self) -> None:
        """执行一轮监控"""
        # Skip entirely when no watched market is in its trading session. This
        # avoids pointless overnight/weekend requests and provider rate limits.
        if not any_market_open(self._markets):
            logger.info(
                f"Skipping cycle: none of {self._markets} is in a trading session")
            return
        logger.info(f"Fetching quotes for {len(self.stocks)} stocks...")
        quotes = self.source.fetch_quotes(self.stocks)

        if not quotes:
            logger.warning("No quotes fetched this cycle")
            return

        # 记录所有行情
        for q in quotes:
            logger.info(
                f"{q.market} | {q.name}({q.symbol}) "
                f"价格:{q.price:.4f} 涨跌:{q.change_pct:+.2f}% 成交量:{q.volume:.0f}")

        # 检查预警
        alerts = self.engine.check(quotes)
        if alerts:
            logger.info(f"⚠️ {len(alerts)} alert(s) triggered")
            for n in self.notifiers:
                n.send(alerts)
        else:
            logger.info("No alerts triggered")
            for n in self.notifiers:
                n.send([])
