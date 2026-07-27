import logging
import sys
import os
from pathlib import Path

# Bypass any HTTP/HTTPS proxy for this process. On Windows the `requests`
# library (used by akshare) auto-reads the system proxy from the registry, so a
# local proxy tool such as Clash/V2Ray (typically on 127.0.0.1:7890) intercepts
# requests to our domestic data servers (eastmoney) and breaks them with
# 'RemoteDisconnected'. All of this app's traffic (akshare -> eastmoney, plus the
# WeChat Work/DingTalk webhook) is domestic and directly reachable, so skipping
# the proxy is correct here. `setdefault` lets an explicit NO_PROXY in the
# environment still take precedence.
os.environ.setdefault("NO_PROXY", "*")

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config_loader import load_watchlist, load_alerts, load_notify
from src.sources.sinatx_source import SinaTxSource
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
    notify = load_notify(str(CONFIG_DIR / "notify.yaml"))
    # Environment variable takes precedence over the config file so it can
    # still be overridden per-deploy (containers/CI) without editing the file.
    webhook_url = os.environ.get("WEBHOOK_URL") or notify.get("webhook_url", "")
    webhook_timeout = int(notify.get("webhook_timeout", 10) or 10)
    source = SinaTxSource()
    engine = AlertEngine(rules, cooldown_seconds=300)
    notifiers = [ConsoleNotifier()]
    if webhook_url:
        notifiers.append(WebhookNotifier(url=webhook_url, timeout=webhook_timeout))
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
        IntervalTrigger(minutes=10),
        id="stock_monitor",
        name="Fetch stock quotes every 10 minutes",
        max_instances=1,
        coalesce=True,
    )

    logger.info("Scheduler started. Next run in 10 minutes. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
        scheduler.shutdown()


if __name__ == "__main__":
    main()
