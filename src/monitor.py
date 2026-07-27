import logging
from src.market_hours import is_market_open
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


    def run_once(self) -> None:
        """执行一轮监控"""
        # Only fetch symbols whose market is currently in a trading session.
        # This avoids overnight/weekend requests and stops a still-open market
        # (e.g. HK) from causing closed-market symbols (e.g. A-shares) to run.
        open_stocks = [s for s in self.stocks if is_market_open(s.get("market", ""))]
        if not open_stocks:
            markets = sorted({s.get("market", "") for s in self.stocks})
            logger.info(
                f"Skipping cycle: none of {markets} is in a trading session")
            return
        logger.info(
            f"Fetching quotes for {len(open_stocks)}/{len(self.stocks)} "
            "stocks in open markets...")
        quotes = self.source.fetch_quotes(open_stocks)

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

