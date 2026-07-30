import logging
from collections.abc import Callable

from src.alerts_engine import AlertEngine
from src.index_snapshots import market_snapshot_date
from src.market_hours import is_market_open
from src.models import Quote
from src.notifiers.base import Notifier
from src.sources.base import DataSource

logger = logging.getLogger(__name__)


class MonitorService:
    """Core monitor flow: reload config, fetch quotes, alert, paper trade, snapshot indices."""

    def __init__(self, source: DataSource, alert_engine: AlertEngine,
                 notifiers: list[Notifier], stocks: list[dict],
                 stocks_loader: Callable[[], list[dict]] | None = None,
                 rules_loader: Callable[[], list[dict]] | None = None,
                 notifiers_loader: Callable[[], list[Notifier]] | None = None,
                 trading_service=None,
                 strategies_loader: Callable[[], list[dict]] | None = None,
                 app_config_loader: Callable[[], dict] | None = None,
                 index_store=None,
                 market_indices: list[dict] | None = None,
                 market_indices_loader: Callable[[], list[dict]] | None = None):
        self.source = source
        self.engine = alert_engine
        self.notifiers = notifiers
        self.stocks = stocks
        self.trading_service = trading_service
        self.index_store = index_store
        self.market_indices = market_indices or []
        self._stocks_loader = stocks_loader
        self._rules_loader = rules_loader
        self._notifiers_loader = notifiers_loader
        self._strategies_loader = strategies_loader
        self._app_config_loader = app_config_loader
        self._market_indices_loader = market_indices_loader

    def _reload_runtime_config(self) -> None:
        """Reload runtime configs before each cycle.

        A partially edited config should not kill the long-running monitor. If
        reload fails, keep the last known-good config and try again next run.
        """
        if self._stocks_loader is not None:
            try:
                self.stocks = self._stocks_loader()
            except Exception as e:
                logger.error(f"Failed to reload runtime watchlist from SQLite; keeping previous stocks: {e}")

        if self._rules_loader is not None:
            try:
                self.engine.set_rules(self._rules_loader())
            except Exception as e:
                logger.error(f"Failed to reload runtime alerts from SQLite; keeping previous rules: {e}")

        if self._market_indices_loader is not None:
            try:
                self.market_indices = self._market_indices_loader()
            except Exception as e:
                logger.error(f"Failed to reload market index config; keeping previous indices: {e}")

        if self._notifiers_loader is not None:
            try:
                self.notifiers = self._notifiers_loader()
            except Exception as e:
                logger.error(f"Failed to reload config.yaml; keeping previous notifiers: {e}")

        if self.trading_service is not None and self._strategies_loader is not None:
            try:
                self.trading_service.set_strategies(self._strategies_loader())
            except Exception as e:
                logger.error(f"Failed to reload strategies.yaml; keeping previous strategies: {e}")

        if self.trading_service is not None and self._app_config_loader is not None:
            try:
                self.trading_service.apply_config(self._app_config_loader())
            except Exception as e:
                logger.error(f"Failed to reload paper trading config; keeping previous config: {e}")

    def run_once(self) -> None:
        """Run one monitor cycle."""
        self._reload_runtime_config()
        self._save_daily_index_snapshots()

        # Only fetch symbols whose market is currently in a trading session.
        open_stocks = [s for s in self.stocks if is_market_open(s.get("market", ""))]
        if not open_stocks:
            markets = sorted({s.get("market", "") for s in self.stocks})
            logger.info(
                f"Skipping stock cycle: none of {markets} is in a trading session")
            return
        logger.info(
            f"Fetching quotes for {len(open_stocks)}/{len(self.stocks)} "
            "stocks in open markets...")
        quotes = self.source.fetch_quotes(open_stocks)

        if not quotes:
            logger.warning("No quotes fetched this cycle")
            return

        for q in quotes:
            logger.info(
                f"{q.market} | {q.name}({q.symbol}) "
                f"price:{q.price:.4f} change:{q.change_pct:+.2f}% volume:{q.volume:.0f}")

        alerts = self.engine.check(quotes)
        if alerts:
            logger.info(f"{len(alerts)} alert(s) triggered")
            for n in self.notifiers:
                n.send(alerts)
        else:
            logger.info("No alerts triggered")
            for n in self.notifiers:
                n.send([])

        if self.trading_service is not None:
            trade_messages = self.trading_service.process(quotes)
            if trade_messages:
                logger.info(f"{len(trade_messages)} paper trading event(s) generated")
                for n in self.notifiers:
                    n.send(trade_messages)

    def _save_daily_index_snapshots(self) -> None:
        if self.index_store is None or not self.market_indices:
            return

        snapshot_dates: dict[str, str] = {}
        open_indices: list[dict] = []
        for item in self.market_indices:
            market = item.get("market", "")
            if not is_market_open(market):
                continue
            symbol = str(item["symbol"])
            snapshot_dates[symbol] = market_snapshot_date(market)
            open_indices.append(item)

        if not open_indices:
            return

        logger.info(f"Fetching {len(open_indices)} market index snapshot(s)...")
        quotes = self.source.fetch_quotes(open_indices)
        if not quotes:
            logger.warning("No market index snapshots fetched this cycle")
            return

        inserted = self.index_store.save_index_snapshots(quotes, snapshot_dates)
        logger.info(f"Saved {inserted}/{len(quotes)} market index daily snapshot update(s)")