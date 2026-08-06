import logging
import sys
import os
from pathlib import Path

# Bypass any HTTP/HTTPS proxy for this process. On Windows the `requests`
# library auto-reads proxy settings; local proxy tools can break domestic quote
# and webhook traffic. These endpoints are directly reachable.
os.environ.setdefault("NO_PROXY", "*")

from apscheduler.schedulers.background import BackgroundScheduler
import uvicorn

from src.web.app import create_app
from apscheduler.triggers.interval import IntervalTrigger

from src.config_loader import load_watchlist, load_alerts, load_app_config, load_strategies
from src.sources.sinatx_source import SinaTxSource
from src.alerts_engine import AlertEngine
from src.strategy_engine import StrategyEngine
from src.index_snapshots import load_market_indices
from src.trading_store import TradingStore
from src.paper_trading import PaperTradingService
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


def _resolve_path(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


def load_runtime_app_config() -> dict:
    return load_app_config(str(CONFIG_DIR / "config.yaml"))


def runtime_db_path() -> str:
    app_config = load_runtime_app_config()
    paper = app_config.get("paper_trading", {}) or {}
    return _resolve_path(paper.get("db_path", "data/trading.sqlite3"))


def build_store() -> TradingStore:
    store = TradingStore(runtime_db_path())
    seed_runtime_config(store)
    return store


def seed_runtime_config(store: TradingStore) -> None:
    """Seed DB config tables from YAML once when tables are empty."""
    inserted_stocks = store.seed_watchlist(load_watchlist(str(CONFIG_DIR / "watchlist.yaml")))
    if inserted_stocks:
        logger.info(f"Seeded {inserted_stocks} watchlist item(s) into SQLite")
    inserted_rules = store.seed_alert_rules(load_alerts(str(CONFIG_DIR / "alerts.yaml")))
    if inserted_rules:
        logger.info(f"Seeded {inserted_rules} alert rule(s) into SQLite")
    inserted_strategies = store.seed_strategies(load_strategies(str(CONFIG_DIR / "strategies.yaml")))
    if inserted_strategies:
        logger.info(f"Seeded {inserted_strategies} strateg(y/ies) into SQLite")



def load_runtime_stocks_from_store(store: TradingStore) -> list[dict]:
    return store.load_watchlist()


def load_runtime_rules_from_store(store: TradingStore) -> list[dict]:
    return store.load_alert_rules()


def load_runtime_strategies(store: TradingStore | None = None) -> list[dict]:
    if store is not None:
        return store.load_runtime_strategies()
    return load_strategies(str(CONFIG_DIR / "strategies.yaml"))


def load_runtime_market_indices() -> list[dict]:
    return load_market_indices(load_runtime_app_config())


def load_runtime_notifiers() -> list[ConsoleNotifier | WebhookNotifier]:
    app_config = load_runtime_app_config()
    webhook_url = os.environ.get("WEBHOOK_URL") or app_config.get("webhook_url", "")
    webhook_timeout = int(app_config.get("webhook_timeout", 10) or 10)
    notifiers = [ConsoleNotifier()]
    if webhook_url:
        notifiers.append(WebhookNotifier(url=webhook_url, timeout=webhook_timeout))
        logger.info("Webhook notifier enabled from config.yaml/WEBHOOK_URL")
    else:
        logger.info("Webhook notifier disabled: no webhook_url configured")
    return notifiers


def build_trading_service(store: TradingStore) -> PaperTradingService:
    app_config = load_runtime_app_config()
    paper = app_config.get("paper_trading", {}) or {}
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    store.ensure_accounts(accounts)
    return PaperTradingService(
        store=store,
        strategy_engine=StrategyEngine(load_runtime_strategies(store)),
        enabled=bool(paper.get("enabled", True)),
        quote_history_enabled=bool(paper.get("quote_history_enabled", True)),
    )


def build_monitor() -> MonitorService:
    store = build_store()
    source = SinaTxSource()
    stocks = load_runtime_stocks_from_store(store)
    rules = load_runtime_rules_from_store(store)
    engine = AlertEngine(rules, cooldown_seconds=300)
    trading_service = build_trading_service(store)
    return MonitorService(
        source=source,
        alert_engine=engine,
        notifiers=load_runtime_notifiers(),
        stocks=stocks,
        stocks_loader=lambda: load_runtime_stocks_from_store(store),
        rules_loader=lambda: load_runtime_rules_from_store(store),
        notifiers_loader=load_runtime_notifiers,
        trading_service=trading_service,
        strategies_loader=lambda: store.load_runtime_strategies(),
        app_config_loader=load_runtime_app_config,
        index_store=store,
        market_indices=load_runtime_market_indices(),
        market_indices_loader=load_runtime_market_indices,
    )


def main():
    logger.info("=" * 60)
    logger.info("Stock Price Monitor starting...")
    logger.info("=" * 60)

    app_config = load_runtime_app_config()
    interval_minutes = int((app_config.get("monitor") or {}).get("interval_minutes", 30) or 30)
    monitor = build_monitor()

    logger.info("Running initial fetch...")
    monitor.run_once()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        monitor.run_once,
        IntervalTrigger(minutes=interval_minutes),
        id="stock_monitor",
        name=f"Fetch stock quotes every {interval_minutes} minutes",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(f"Scheduler started in background. Next run in {interval_minutes} minutes.")

    web_cfg = app_config.get("web", {}) or {}
    host = web_cfg.get("host", "127.0.0.1")
    port = int(web_cfg.get("port", 8000))
    app = create_app(store=monitor.index_store, config_dir=CONFIG_DIR)
    app.state.monitor = monitor

    logger.info(f"Web admin running at http://{host}:{port}")
    try:
        uvicorn.run(app, host=host, port=port)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    main()
