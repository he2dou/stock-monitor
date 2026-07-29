from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config_loader import load_alerts, load_app_config, load_strategies, load_watchlist
from src.index_history import backfill_index_snapshots
from src.index_snapshots import load_market_indices, market_snapshot_date
from src.market_hours import is_market_open
from src.sources.sinatx_source import SinaTxSource
from src.trading_store import TradingStore

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"


def _resolve_path(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


def load_default_app_config() -> dict:
    return load_app_config(str(CONFIG_DIR / "config.yaml"))


def default_store() -> TradingStore:
    app_config = load_default_app_config()
    paper = app_config.get("paper_trading", {}) or {}
    return TradingStore(_resolve_path(paper.get("db_path", "data/trading.sqlite3")))


def print_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _split_values(values: list[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        result.update(part.strip() for part in value.split(",") if part.strip())
    return result


def _filter_items(items: list[dict], symbols: set[str], markets: set[str]) -> list[dict]:
    selected: list[dict] = []
    for item in items:
        if symbols and str(item.get("symbol")) not in symbols:
            continue
        if markets and str(item.get("market")) not in markets:
            continue
        selected.append(item)
    return selected


def _open_items(items: list[dict], ignore_hours: bool) -> tuple[list[dict], list[dict]]:
    if ignore_hours:
        return items, []
    open_items: list[dict] = []
    skipped: list[dict] = []
    for item in items:
        if is_market_open(str(item.get("market", ""))):
            open_items.append(item)
        else:
            skipped.append(item)
    return open_items, skipped


def cmd_import_yaml(args) -> None:
    store = default_store()
    stocks = load_watchlist(str(CONFIG_DIR / "watchlist.yaml"))
    rules = load_alerts(str(CONFIG_DIR / "alerts.yaml"))
    strategies = load_strategies(str(CONFIG_DIR / "strategies.yaml"))
    stock_count = store.import_watchlist(stocks, replace=args.replace)
    rule_count = store.import_alert_rules(rules, replace=args.replace)
    strategy_count = store.import_strategies(strategies, replace=args.replace)
    print_json({
        "watchlist_imported": stock_count,
        "alert_rules_imported": rule_count,
        "strategies_imported": strategy_count,
    })
    store.close()


def cmd_list_watchlist(args) -> None:
    store = default_store()
    print_json(store.load_watchlist(include_disabled=args.all))
    store.close()


def cmd_add_stock(args) -> None:
    store = default_store()
    store.add_stock(args.symbol, args.name, args.market, enabled=not args.disabled)
    print_json({"ok": True, "symbol": args.symbol})
    store.close()


def cmd_disable_stock(args) -> None:
    store = default_store()
    store.set_stock_enabled(args.symbol, False)
    print_json({"ok": True, "symbol": args.symbol, "enabled": False})
    store.close()


def cmd_enable_stock(args) -> None:
    store = default_store()
    store.set_stock_enabled(args.symbol, True)
    print_json({"ok": True, "symbol": args.symbol, "enabled": True})
    store.close()


def cmd_del_stock(args) -> None:
    store = default_store()
    deleted = store.delete_stock(args.symbol)
    print_json({"ok": deleted > 0, "symbol": args.symbol, "deleted": deleted})
    store.close()


def cmd_list_alerts(args) -> None:
    store = default_store()
    print_json(store.load_alert_rules(include_disabled=args.all))
    store.close()


def cmd_list_index_snapshots(args) -> None:
    store = default_store()
    if args.backfill:
        backfill_index_snapshots(store, load_default_app_config(), args.start, args.end)
    print_json(store.load_index_snapshots(start=args.start, end=args.end))
    store.close()


def cmd_backfill_index_snapshots(args) -> None:
    store = default_store()
    result = backfill_index_snapshots(store, load_default_app_config(), args.start, args.end)
    print_json(result)
    store.close()


def cmd_update_snapshots(args) -> None:
    store = default_store()
    try:
        app_config = load_default_app_config()
        symbols = _split_values(args.symbol)
        markets = _split_values(args.market)
        if args.target == "stock":
            items = store.load_watchlist(include_disabled=args.include_disabled)
        else:
            items = load_market_indices(app_config)
        selected = _filter_items(items, symbols, markets)
        to_fetch, skipped = _open_items(selected, args.ignore_hours)
        quotes = SinaTxSource().fetch_quotes(to_fetch) if to_fetch else []

        saved = 0
        if quotes and args.target == "stock":
            store.save_quote_snapshots(quotes)
            saved = len(quotes)
        elif quotes:
            snapshot_dates = {q.symbol: market_snapshot_date(q.market) for q in quotes}
            saved = store.save_index_snapshots(quotes, snapshot_dates)

        print_json({
            "target": args.target,
            "selected": len(selected),
            "requested": len(to_fetch),
            "fetched": len(quotes),
            "saved": saved,
            "skipped_closed": [item.get("symbol") for item in skipped],
            "updated_symbols": [q.symbol for q in quotes],
        })
    finally:
        store.close()

def cmd_add_alert(args) -> None:
    store = default_store()
    rule_id = store.add_alert_rule(
        symbol=args.symbol,
        field=args.field,
        op=args.op,
        value=args.value,
        enabled=not args.disabled,
        cooldown_seconds=args.cooldown_seconds,
    )
    print_json({"ok": True, "rule_id": rule_id})
    store.close()


def cmd_disable_alert(args) -> None:
    store = default_store()
    store.set_alert_rule_enabled(args.rule_id, False)
    print_json({"ok": True, "rule_id": args.rule_id, "enabled": False})
    store.close()


def cmd_enable_alert(args) -> None:
    store = default_store()
    store.set_alert_rule_enabled(args.rule_id, True)
    print_json({"ok": True, "rule_id": args.rule_id, "enabled": True})
    store.close()


def cmd_del_alert(args) -> None:
    store = default_store()
    deleted = store.delete_alert_rule(args.rule_id)
    print_json({"ok": deleted > 0, "rule_id": args.rule_id, "deleted": deleted})
    store.close()


def _strategy_from_args(args) -> dict:
    strategy = {
        "id": args.strategy_id,
        "enabled": not args.disabled,
        "symbol": args.symbol,
        "action": args.action,
        "trigger": {
            "field": args.trigger_field,
            "op": args.trigger_op,
            "value": args.trigger_value,
        },
        "sizing": {
            "type": "fixed_amount",
            "amount": args.amount,
        },
        "constraints": {
            "cooldown_minutes": args.cooldown_minutes,
        },
    }
    if args.currency:
        strategy["sizing"]["currency"] = args.currency
    if args.lot_size is not None:
        strategy["sizing"]["lot_size"] = args.lot_size
    if args.max_position_amount is not None:
        strategy["constraints"]["max_position_amount"] = args.max_position_amount
    return strategy


def cmd_add_strategy(args) -> None:
    store = default_store()
    strategy_id = store.add_strategy(_strategy_from_args(args))
    print_json({"ok": True, "strategy_id": strategy_id})
    store.close()


def cmd_del_strategy(args) -> None:
    store = default_store()
    deleted = store.delete_strategy(args.strategy_id)
    print_json({"ok": deleted > 0, "strategy_id": args.strategy_id, "deleted": deleted})
    store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage stock monitor runtime config stored in SQLite")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import-yaml", help="Import config/watchlist.yaml, config/alerts.yaml, and config/strategies.yaml into SQLite")
    p.add_argument("--replace", action="store_true", help="Clear DB config rows before importing")
    p.set_defaults(func=cmd_import_yaml)

    p = sub.add_parser("list-watchlist")
    p.add_argument("--all", action="store_true", help="Include disabled stocks")
    p.set_defaults(func=cmd_list_watchlist)

    p = sub.add_parser("add-stock")
    p.add_argument("--symbol", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--market", required=True, choices=["A股", "港股", "美股"])
    p.add_argument("--disabled", action="store_true")
    p.set_defaults(func=cmd_add_stock)

    p = sub.add_parser("disable-stock")
    p.add_argument("--symbol", required=True)
    p.set_defaults(func=cmd_disable_stock)

    p = sub.add_parser("enable-stock")
    p.add_argument("--symbol", required=True)
    p.set_defaults(func=cmd_enable_stock)

    p = sub.add_parser("del-stock")
    p.add_argument("--symbol", required=True)
    p.set_defaults(func=cmd_del_stock)

    p = sub.add_parser("list-alerts")
    p.add_argument("--all", action="store_true", help="Include disabled alert rules")
    p.set_defaults(func=cmd_list_alerts)

    p = sub.add_parser("list-index-snapshots", help="List saved daily market index snapshots")
    p.add_argument("--from", dest="start", default=None, help="Start snapshot date, YYYY-MM-DD")
    p.add_argument("--to", dest="end", default=None, help="End snapshot date, YYYY-MM-DD")
    p.add_argument("--backfill", action="store_true", help="Fetch missing historical index snapshots before listing")
    p.set_defaults(func=cmd_list_index_snapshots)

    p = sub.add_parser("backfill-index-snapshots", help="Fetch historical daily market index snapshots into SQLite")
    p.add_argument("--from", dest="start", required=True, help="Start snapshot date, YYYY-MM-DD")
    p.add_argument("--to", dest="end", required=True, help="End snapshot date, YYYY-MM-DD")
    p.set_defaults(func=cmd_backfill_index_snapshots)

    p = sub.add_parser("update-snapshots", help="Fetch realtime quotes and update stock or index snapshots")
    p.add_argument("--target", required=True, choices=["stock", "index"], help="Snapshot type to update")
    p.add_argument("--symbol", action="append", help="Symbol to update; repeat or comma-separate for multiple")
    p.add_argument("--market", action="append", help="Market to update; repeat or comma-separate for multiple")
    p.add_argument("--ignore-hours", action="store_true", help="Update even when the market is outside trading hours")
    p.add_argument("--include-disabled", action="store_true", help="For stock target, include disabled watchlist rows")
    p.set_defaults(func=cmd_update_snapshots)

    p = sub.add_parser("add-alert")
    p.add_argument("--symbol", required=True)
    p.add_argument("--field", required=True, choices=["price", "change_pct"])
    p.add_argument("--op", required=True, choices=["above", "below"])
    p.add_argument("--value", required=True, type=float)
    p.add_argument("--cooldown-seconds", type=int, default=None)
    p.add_argument("--disabled", action="store_true")
    p.set_defaults(func=cmd_add_alert)

    p = sub.add_parser("disable-alert")
    p.add_argument("--rule-id", required=True)
    p.set_defaults(func=cmd_disable_alert)

    p = sub.add_parser("enable-alert")
    p.add_argument("--rule-id", required=True)
    p.set_defaults(func=cmd_enable_alert)

    p = sub.add_parser("del-alert")
    p.add_argument("--rule-id", required=True)
    p.set_defaults(func=cmd_del_alert)

    p = sub.add_parser("add-strategy")
    p.add_argument("--id", "--strategy-id", dest="strategy_id", required=True)
    p.add_argument("--symbol", required=True)
    p.add_argument("--action", required=True, choices=["buy", "sell"])
    p.add_argument("--trigger-field", required=True, choices=["price", "change_pct"])
    p.add_argument("--trigger-op", required=True, choices=["above", "below"])
    p.add_argument("--trigger-value", required=True, type=float)
    p.add_argument("--amount", required=True, type=float)
    p.add_argument("--currency", default=None)
    p.add_argument("--lot-size", type=int, default=None)
    p.add_argument("--cooldown-minutes", type=int, default=0)
    p.add_argument("--max-position-amount", type=float, default=None)
    p.add_argument("--disabled", action="store_true")
    p.set_defaults(func=cmd_add_strategy)

    p = sub.add_parser("del-strategy")
    p.add_argument("--id", "--strategy-id", dest="strategy_id", required=True)
    p.set_defaults(func=cmd_del_strategy)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
