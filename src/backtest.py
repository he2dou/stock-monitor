from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config_loader import load_app_config, load_strategies
from src.paper_trading import PaperTradingService
from src.strategy_engine import StrategyEngine
from src.trading_store import TradingStore

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"


def _resolve_path(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


def run_backtest(db_path: str, strategies: list[dict], accounts: dict[str, float],
                 start: str | None = None, end: str | None = None) -> dict:
    source_store = TradingStore(db_path)
    quotes = source_store.load_quote_snapshots(start, end)

    test_store = TradingStore(":memory:")
    test_store.ensure_accounts(accounts)
    service = PaperTradingService(
        store=test_store,
        strategy_engine=StrategyEngine(strategies),
        enabled=True,
        quote_history_enabled=False,
    )
    for quote in quotes:
        service.process([quote])

    balances = {
        row["currency"]: row["cash"]
        for row in test_store.conn.execute("SELECT currency, cash FROM account_balances")
    }
    summary = {
        "quotes_replayed": len(quotes),
        "orders": test_store.order_count(),
        "fills": test_store.fill_count(),
        "realized_pnl": test_store.realized_pnl(),
        "ending_cash": balances,
    }
    source_store.close()
    test_store.close()
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest strategies from saved quote snapshots")
    parser.add_argument("--from", dest="from_date", default=None, help="Start timestamp/date")
    parser.add_argument("--to", dest="to_date", default=None, help="End timestamp/date")
    parser.add_argument("--db", dest="db_path", default=None, help="SQLite db path")
    args = parser.parse_args()

    app_config = load_app_config(str(CONFIG_DIR / "config.yaml"))
    paper = app_config.get("paper_trading", {}) or {}
    db_path = _resolve_path(args.db_path or paper.get("db_path", "data/trading.sqlite3"))
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    strategies = load_strategies(str(CONFIG_DIR / "strategies.yaml"))
    summary = run_backtest(db_path, strategies, accounts, args.from_date, args.to_date)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
