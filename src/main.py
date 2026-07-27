import logging
import sys
import os
from pathlib import Path
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config_loader import load_watchlist, load_alerts
from src.sources.akshare_source import AkshareSource
from src.alerts_engine import AlertEngine
from src.notifiers.console_notifier import ConsoleNotifier
from src.notifiers.webhook_notifier import WebhookNotifier
from src.monitor import MonitorService

# --- 日志配置 ---
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "stock_monitor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"


def build_monitor() -> MonitorService:
    stocks = load_watchlist(str(CONFIG_DIR / "watchlist.yaml"))
    rules = load_alerts(str(CONFIG_DIR / "alerts.yaml"))
    webhook_url = os.environ.get("WEBHOOK_URL", "")
    source = AkshareSource()
    engine = AlertEngine(rules, cooldown_seconds=300)
    notifiers = [ConsoleNotifier()]
    if webhook_url:
        notifiers.append(WebhookNotifier(url=webhook_url))
    return MonitorService(source=source, alert_engine=engine,
                          notifiers=notifiers, stocks=stocks)


def main():
    logger.info("=" * 60)
    logger.info("Stock Price Monitor starting...")
    logger.info("=" * 60)

    monitor = build_monitor()

    logger.info("Running initial fetch...")
    monitor.run_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(
        monitor.run_once,
        IntervalTrigger(minutes=5),
        id="stock_monitor",
        name="Fetch stock quotes every 5 minutes",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Scheduler started. Next run in 5 minutes. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
