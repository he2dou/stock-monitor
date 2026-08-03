from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from src.config_loader import load_app_config, load_strategies
from src.models import Quote
from src.paper_trading import PaperBroker
from src.strategy_engine import StrategyEngine
from src.trading_store import TradingStore

BASE_DIR = Path(__file__).parent.parent
CONFIG_DIR = BASE_DIR / "config"


def _resolve_path(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


def _split_values(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        for part in value.split(","):
            item = part.strip()
            if item and item not in result:
                result.append(item)
    return result


def _select_strategies(strategies: list[dict], strategy_ids: list[str] | None,
                       enable_selected: bool = False) -> list[dict]:
    if not strategy_ids:
        return strategies
    selected: list[dict] = []
    wanted = set(strategy_ids)
    for strategy in strategies:
        if str(strategy.get("id")) not in wanted:
            continue
        item = dict(strategy)
        if enable_selected:
            item["enabled"] = True
        selected.append(item)
    return selected


def _quotes_from_daily_bars(rows: list[dict]) -> list[Quote]:
    previous_close: dict[str, float] = {}
    quotes: list[Quote] = []
    for row in rows:
        close = float(row["close"])
        prev = previous_close.get(row["symbol"])
        change_pct = ((close - prev) / prev * 100.0) if prev else 0.0
        previous_close[row["symbol"]] = close
        quotes.append(Quote(
            symbol=row["symbol"],
            name=row["name"],
            market=row["market"],
            price=close,
            change_pct=change_pct,
            volume=float(row.get("volume", 0) or 0),
            timestamp=f"{row['date']}T22:00:00+08:00",
        ))
    return quotes


def _load_quotes(store: TradingStore, source: str, symbols: list[str] | None,
                 start: str | None, end: str | None) -> tuple[list[Quote], list[dict]]:
    if source == "daily-bars":
        rows = store.load_daily_bars(symbols=symbols or None, start=start, end=end)
        return _quotes_from_daily_bars(rows), rows
    quotes = store.load_quote_snapshots(start, end)
    if symbols:
        quotes = [q for q in quotes if q.symbol in symbols]
    return quotes, []


def _balances(store: TradingStore) -> dict[str, float]:
    return {
        row["currency"]: float(row["cash"])
        for row in store.conn.execute("SELECT currency, cash FROM account_balances")
    }


def _positions(store: TradingStore) -> list[dict]:
    return [
        dict(row)
        for row in store.conn.execute("SELECT * FROM positions ORDER BY market, symbol").fetchall()
    ]


def _equity_by_currency(store: TradingStore, last_prices: dict[str, float]) -> dict[str, float]:
    equity = _balances(store)
    for pos in _positions(store):
        qty = int(pos["quantity"])
        if qty == 0:
            continue
        currency = pos["currency"]
        equity[currency] = equity.get(currency, 0.0) + qty * last_prices.get(pos["symbol"], 0.0)
    return equity


def _total_equity(equity_by_currency: dict[str, float]) -> float:
    return sum(float(value) for value in equity_by_currency.values())


def _max_drawdown(equity_curve: list[dict]) -> float:
    peak: float | None = None
    max_dd = 0.0
    for point in equity_curve:
        equity = float(point["total_equity"])
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            max_dd = min(max_dd, (equity - peak) / peak * 100.0)
    return max_dd


def _max_drawdown_for_currency(equity_curve: list[dict], currency: str) -> float:
    peak: float | None = None
    max_dd = 0.0
    for point in equity_curve:
        equity = float((point.get("equity_by_currency") or {}).get(currency, 0.0))
        if peak is None or equity > peak:
            peak = equity
        if peak and peak > 0:
            max_dd = min(max_dd, (equity - peak) / peak * 100.0)
    return max_dd

def _buy_hold_return(quotes: list[Quote], symbol: str | None) -> float | None:
    if not symbol:
        return None
    selected = [q for q in quotes if q.symbol == symbol]
    if len(selected) < 2 or selected[0].price <= 0:
        return None
    return (selected[-1].price - selected[0].price) / selected[0].price * 100.0


def run_backtest(db_path: str, strategies: list[dict], accounts: dict[str, float],
                 start: str | None = None, end: str | None = None,
                 source: str = "quote-snapshots", symbols: list[str] | None = None,
                 strategy_ids: list[str] | None = None,
                 enable_selected: bool = False) -> dict:
    source_store = TradingStore(db_path)
    quotes, source_rows = _load_quotes(source_store, source, symbols, start, end)
    selected_strategies = _select_strategies(strategies, strategy_ids, enable_selected)

    test_store = TradingStore(":memory:")
    test_store.ensure_accounts(accounts)
    engine = StrategyEngine(selected_strategies)
    broker = PaperBroker(test_store)

    trades: list[dict] = []
    equity_curve: list[dict] = []
    last_prices: dict[str, float] = {}
    starting_equity_by_currency = _balances(test_store)
    starting_equity = _total_equity(starting_equity_by_currency)

    for quote in quotes:
        last_prices[quote.symbol] = quote.price
        for signal in engine.generate_signals([quote]):
            pre_position = test_store.get_position(signal.market, signal.symbol)
            execution = broker.execute(signal)
            realized_pnl = None
            if execution.status == "FILLED":
                if signal.action == "sell":
                    realized_pnl = (execution.price - float(pre_position["avg_cost"])) * execution.quantity
                engine.mark_filled(signal.strategy.id, signal, execution)
            trades.append({
                "date": quote.timestamp[:10],
                "strategy_id": signal.strategy.id,
                "symbol": signal.symbol,
                "side": signal.action,
                "status": execution.status,
                "trigger": signal.trigger_op,
                "quantity": execution.quantity,
                "price": execution.price,
                "amount": execution.amount,
                "currency": execution.currency,
                "reason": execution.reason,
                "realized_pnl": realized_pnl,
            })
        equity_by_currency = _equity_by_currency(test_store, last_prices)
        equity_curve.append({
            "date": quote.timestamp[:10],
            "total_equity": _total_equity(equity_by_currency),
            "equity_by_currency": equity_by_currency,
        })

    ending_equity_by_currency = _equity_by_currency(test_store, last_prices)
    ending_total_equity = _total_equity(ending_equity_by_currency)
    return_pct_by_currency = {
        currency: ((ending_equity_by_currency.get(currency, 0.0) - start_value) / start_value * 100.0)
        for currency, start_value in starting_equity_by_currency.items()
        if start_value
    }
    sell_fills = [t for t in trades if t["status"] == "FILLED" and t["side"] == "sell"]
    winning_sells = [t for t in sell_fills if (t.get("realized_pnl") or 0) > 0]
    primary_symbol = (symbols or [quotes[0].symbol if quotes else None])[0]

    summary = {
        "source": source,
        "symbols": symbols or sorted({q.symbol for q in quotes}),
        "strategy_ids": [s.get("id") for s in selected_strategies],
        "from": start,
        "to": end,
        "source_rows": len(source_rows) if source == "daily-bars" else len(quotes),
        "quotes_replayed": len(quotes),
        "orders": test_store.order_count(),
        "fills": test_store.fill_count(),
        "trade_events": len(trades),
        "realized_pnl": test_store.realized_pnl(),
        "starting_equity": starting_equity,
        "starting_equity_by_currency": starting_equity_by_currency,
        "ending_cash": _balances(test_store),
        "ending_equity": ending_equity_by_currency,
        "ending_total_equity": ending_total_equity,
        "total_return_pct": ((ending_total_equity - starting_equity) / starting_equity * 100.0) if starting_equity else 0.0,
        "return_pct_by_currency": return_pct_by_currency,
        "buy_hold_return_pct": _buy_hold_return(quotes, primary_symbol),
        "max_drawdown_pct": _max_drawdown(equity_curve),
        "usd_max_drawdown_pct": _max_drawdown_for_currency(equity_curve, "USD"),
        "sell_win_rate_pct": (len(winning_sells) / len(sell_fills) * 100.0) if sell_fills else None,
        "open_positions": _positions(test_store),
        "trades": trades,
        "equity_curve": equity_curve,
    }
    source_store.close()
    test_store.close()
    return summary


def write_trades_csv(summary: dict, path: str) -> None:
    output = Path(_resolve_path(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "date", "strategy_id", "symbol", "side", "status", "trigger",
        "quantity", "price", "amount", "currency", "realized_pnl", "reason",
    ]
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for trade in summary.get("trades", []):
            writer.writerow({field: trade.get(field) for field in fields})


def write_markdown_report(summary: dict, path: str) -> None:
    output = Path(_resolve_path(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    trades = summary.get("trades", [])
    filled = [t for t in trades if t["status"] == "FILLED"]
    rejected = [t for t in trades if t["status"] == "REJECTED"]
    lines = [
        "# SOXL Strategy Backtest Report",
        "",
        "## Summary",
        f"- Source: `{summary['source']}`",
        f"- Symbols: {', '.join(summary.get('symbols') or [])}",
        f"- Strategies: {', '.join(summary.get('strategy_ids') or [])}",
        f"- Period: {summary.get('from')} to {summary.get('to')}",
        f"- Bars replayed: {summary['quotes_replayed']}",
        f"- Orders: {summary['orders']}, fills: {summary['fills']}, rejections: {len(rejected)}",
        f"- Starting equity: {summary['starting_equity']:.2f}",
        f"- Ending total equity: {summary['ending_total_equity']:.2f}",
        f"- Total return: {summary['total_return_pct']:.2f}%",
        f"- USD account return: {_fmt_pct((summary.get('return_pct_by_currency') or {}).get('USD'))}",
        f"- Buy and hold return: {_fmt_pct(summary.get('buy_hold_return_pct'))}",
        f"- Max drawdown: {summary['max_drawdown_pct']:.2f}%",
        f"- USD max drawdown: {summary.get('usd_max_drawdown_pct', summary['max_drawdown_pct']):.2f}%",
        f"- Realized PnL: {summary['realized_pnl']:.2f}",
        f"- Sell win rate: {_fmt_pct(summary.get('sell_win_rate_pct'))}",
        "",
        "## Ending Balances",
    ]
    for currency, cash in sorted((summary.get("ending_cash") or {}).items()):
        equity = (summary.get("ending_equity") or {}).get(currency, cash)
        start_value = (summary.get("starting_equity_by_currency") or {}).get(currency)
        return_pct = (summary.get("return_pct_by_currency") or {}).get(currency)
        start_text = f", start {start_value:.2f}" if start_value is not None else ""
        return_text = f", return {return_pct:.2f}%" if return_pct is not None else ""
        lines.append(f"- {currency}: cash {cash:.2f}, equity {equity:.2f}{start_text}{return_text}")

    lines.extend(["", "## Open Positions"])
    positions = [p for p in summary.get("open_positions", []) if int(p.get("quantity", 0)) != 0]
    if positions:
        for pos in positions:
            lines.append(
                f"- {pos['symbol']} {pos['quantity']} shares, avg cost {float(pos['avg_cost']):.4f}, "
                f"realized PnL {float(pos['realized_pnl']):.2f} {pos['currency']}"
            )
    else:
        lines.append("- None")

    lines.extend([
        "",
        "## Trade Details",
        "| Date | Strategy | Side | Status | Trigger | Qty | Price | Amount | Realized PnL | Reason |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---|",
    ])
    if trades:
        for trade in trades:
            lines.append(
                f"| {trade['date']} | {trade['strategy_id']} | {trade['side'].upper()} | {trade['status']} | "
                f"{trade['trigger']} | {trade['quantity']} | {trade['price']:.4f} | "
                f"{trade['amount']:.2f} {trade['currency']} | {_fmt_num(trade.get('realized_pnl'))} | "
                f"{trade.get('reason') or ''} |"
            )
    else:
        lines.append("| - | - | - | - | - | - | - | - | - | No trades generated |")

    lines.extend(["", "## Analysis", *_analysis_lines(summary, filled, rejected)])
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _analysis_lines(summary: dict, filled: list[dict], rejected: list[dict]) -> list[str]:
    lines: list[str] = []
    total_return = float(summary.get("total_return_pct") or 0.0)
    buy_hold = summary.get("buy_hold_return_pct")
    usd_return = (summary.get("return_pct_by_currency") or {}).get("USD")
    if buy_hold is not None:
        base_return = float(usd_return if usd_return is not None else total_return)
        diff = base_return - float(buy_hold)
        lines.append(f"- USD strategy return minus buy-and-hold: {diff:.2f} percentage points.")
    if not filled:
        lines.append("- No filled trades were generated. The strategy filters may be too strict for this period/data frequency.")
    else:
        buys = len([t for t in filled if t["side"] == "buy"])
        sells = len([t for t in filled if t["side"] == "sell"])
        lines.append(f"- Filled trade mix: {buys} buys and {sells} sells.")
    if rejected:
        reasons = sorted({t.get("reason", "") for t in rejected if t.get("reason")})
        lines.append(f"- Rejection reasons observed: {', '.join(reasons)}.")
    if summary.get("open_positions"):
        open_qty = sum(int(p.get("quantity", 0)) for p in summary.get("open_positions", []))
        if open_qty:
            lines.append("- Ending equity includes mark-to-market value of open positions using the last replayed close.")
    return lines


def _fmt_pct(value) -> str:
    return "N/A" if value is None else f"{float(value):.2f}%"


def _fmt_num(value) -> str:
    return "" if value is None else f"{float(value):.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest strategies from saved quote snapshots or daily bars")
    parser.add_argument("--from", dest="from_date", default=None, help="Start timestamp/date")
    parser.add_argument("--to", dest="to_date", default=None, help="End timestamp/date")
    parser.add_argument("--db", dest="db_path", default=None, help="SQLite db path")
    parser.add_argument("--source", choices=["quote-snapshots", "daily-bars"], default="quote-snapshots")
    parser.add_argument("--symbol", action="append", help="Symbol to replay; repeat or comma-separate")
    parser.add_argument("--strategy-id", action="append", help="Strategy id to backtest; repeat or comma-separate")
    parser.add_argument("--enable-selected", action="store_true", help="Force selected strategies enabled during backtest")
    parser.add_argument("--report", default=None, help="Write Markdown report to path")
    parser.add_argument("--trades-csv", default=None, help="Write trade details CSV to path")
    args = parser.parse_args()

    app_config = load_app_config(str(CONFIG_DIR / "config.yaml"))
    paper = app_config.get("paper_trading", {}) or {}
    db_path = _resolve_path(args.db_path or paper.get("db_path", "data/trading.sqlite3"))
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    strategies = load_strategies(str(CONFIG_DIR / "strategies.yaml"))
    summary = run_backtest(
        db_path,
        strategies,
        accounts,
        args.from_date,
        args.to_date,
        source=args.source,
        symbols=_split_values(args.symbol),
        strategy_ids=_split_values(args.strategy_id),
        enable_selected=args.enable_selected,
    )
    if args.report:
        write_markdown_report(summary, args.report)
        summary["report_path"] = _resolve_path(args.report)
    if args.trades_csv:
        write_trades_csv(summary, args.trades_csv)
        summary["trades_csv_path"] = _resolve_path(args.trades_csv)
    printable = dict(summary)
    printable.pop("equity_curve", None)
    printable.pop("trades", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
