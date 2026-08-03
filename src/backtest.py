from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from src.config_loader import load_app_config, load_strategies
from src.models import Quote
from src.paper_trading import PaperBroker
from src.strategy_engine import StrategyEngine
from src.trading_models import StrategySignal
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

def _buy_hold_return(quotes: list[Quote], symbol: str | None,
                     fraction: float = 1.0) -> float | None:
    """买入持有收益率。fraction<1 表示部分仓位(如 0.5 = 半仓),作为更公平的对比基准。"""
    if not symbol:
        return None
    selected = [q for q in quotes if q.symbol == symbol]
    if len(selected) < 2 or selected[0].price <= 0:
        return None
    full_return = (selected[-1].price - selected[0].price) / selected[0].price * 100.0
    return full_return * fraction


def _compute_risk_metrics(equity_curve: list[dict], trades: list[dict],
                          source: str, starting_equity: float,
                          ending_equity: float) -> dict:
    """计算风险调整指标与诚实胜率相关统计。"""
    import math as _math

    # --- 日收益率序列(用于 Sharpe/Sortino) ---
    # source=daily-bars -> 年化 252; quote-snapshots 视为日内,年化 252*6.5(保守)
    periods_per_year = 252.0 if source == "daily-bars" else 252.0 * 6.5
    equities = [float(p["total_equity"]) for p in equity_curve] if equity_curve else []
    returns: list[float] = []
    for i in range(1, len(equities)):
        prev = equities[i - 1]
        if prev > 0:
            returns.append((equities[i] - prev) / prev)
    avg_ret = sum(returns) / len(returns) if returns else 0.0
    std_ret = (sum((r - avg_ret) ** 2 for r in returns) / len(returns)) ** 0.5 if returns else 0.0
    downside = [r for r in returns if r < 0]
    downside_std = (sum(r * r for r in downside) / len(downside)) ** 0.5 if downside else 0.0
    sharpe = (avg_ret / std_ret * (periods_per_year ** 0.5)) if std_ret > 0 else None
    sortino = (avg_ret / downside_std * (periods_per_year ** 0.5)) if downside_std > 0 else None

    # --- Calmar: 年化收益 / |最大回撤| ---
    max_dd_pct = abs(_max_drawdown(equity_curve)) / 100.0
    n_years = len(equities) / periods_per_year if periods_per_year else 0
    if starting_equity > 0 and ending_equity > 0 and n_years > 0:
        cagr = (ending_equity / starting_equity) ** (1.0 / n_years) - 1.0
        calmar = (cagr / max_dd_pct) if max_dd_pct > 0 else None
    else:
        calmar = None

    # --- 最大回撤恢复时间(bars) ---
    recovery_bars = _max_drawdown_recovery(equity_curve)

    # --- R 倍数 / 盈亏比 / 期望收益 ---
    # 配对 buy->sell,用入场时的 risk_per_share 计算 R 倍数
    r_multiples: list[float] = []
    wins: list[float] = []
    losses: list[float] = []
    pending_risk: dict[str, float] = {}   # symbol -> risk_per_share of last buy
    pending_qty: dict[str, int] = {}
    for t in trades:
        if t["status"] != "FILLED":
            continue
        sym = t["symbol"]
        if t["side"] == "buy":
            rps = t.get("risk_per_share")
            if rps and rps > 0:
                pending_risk[sym] = float(rps)
                pending_qty[sym] = int(t["quantity"])
        elif t["side"] == "sell":
            rps = pending_risk.get(sym)
            qty = int(t["quantity"])
            pnl = float(t.get("realized_pnl") or 0)
            if rps and rps > 0 and qty > 0:
                r_mult = pnl / (rps * qty)
                r_multiples.append(r_mult)
            if pnl > 0:
                wins.append(pnl)
            elif pnl < 0:
                losses.append(pnl)
            # 部分卖出不清除 pending(剩余仓位仍用同一风险)
    avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else None
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    all_pnl = wins + losses
    expectancy = (sum(all_pnl) / len(all_pnl)) if all_pnl else None

    # --- 回吐比(give-back): 用 equity_curve 估算每笔交易的峰值浮盈 vs 最终实现 ---
    give_backs = _compute_give_back(equity_curve, trades)
    avg_give_back = (sum(give_backs) / len(give_backs)) if give_backs else None

    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown_recovery_bars": recovery_bars,
        "avg_r_multiple": avg_r,
        "profit_factor": profit_factor,
        "expectancy_per_trade": expectancy,
        "avg_give_back_pct": avg_give_back,
    }


def _max_drawdown_recovery(equity_curve: list[dict]) -> int | None:
    """从最大回撤谷底恢复到前高所用的 bar 数;未恢复返回 None。"""
    if not equity_curve:
        return None
    peak = float(equity_curve[0]["total_equity"])
    trough_equity = peak
    trough_idx = 0
    found_dd = False
    for i, point in enumerate(equity_curve):
        eq = float(point["total_equity"])
        if eq > peak:
            peak = eq
        if eq < peak:
            dd = peak - eq
            best_dd = peak - trough_equity
            if not found_dd or dd > best_dd:
                trough_equity = eq
                trough_idx = i
                found_dd = True
    if not found_dd:
        return 0
    # 从谷底往后找首次 >= trough 时的 peak
    for j in range(trough_idx + 1, len(equity_curve)):
        if float(equity_curve[j]["total_equity"]) >= peak:
            return j - trough_idx
    return None  # 未恢复


def _compute_give_back(equity_curve: list[dict], trades: list[dict]) -> list[float]:
    """估算每笔已平仓交易的"回吐比" = (峰值浮盈 - 最终实现) / 峰值浮盈。
    基于交易期间账户总权益的峰值与平仓时权益之差。"""
    if not equity_curve or not trades:
        return []
    # 建立日期 -> equity 索引
    date_to_eq = {p["date"]: float(p["total_equity"]) for p in equity_curve}
    dates_ordered = [p["date"] for p in equity_curve]
    # 配对 buy->final sell
    give_backs: list[float] = []
    open_entry_date: dict[str, str] = {}
    for t in trades:
        if t["status"] != "FILLED":
            continue
        sym = t["symbol"]
        t_date = t.get("date")
        if t_date is None:
            continue
        if t["side"] == "buy":
            open_entry_date[sym] = t_date
        elif t["side"] == "sell" and t.get("trigger") not in ("partial_take_profit",):
            entry_date = open_entry_date.get(sym)
            if not entry_date:
                continue
            try:
                start_idx = dates_ordered.index(entry_date)
                end_idx = dates_ordered.index(t_date)
            except ValueError:
                continue
            if end_idx <= start_idx:
                open_entry_date.pop(sym, None)
                continue
            window = [float(equity_curve[k]["total_equity"])
                      for k in range(start_idx, end_idx + 1)]
            if len(window) < 2:
                open_entry_date.pop(sym, None)
                continue
            peak_eq = max(window)
            close_eq = window[-1]
            base_eq = window[0]
            peak_gain = peak_eq - base_eq
            close_gain = close_eq - base_eq
            if peak_gain > 0:
                give_backs.append((peak_gain - close_gain) / peak_gain * 100.0)
            open_entry_date.pop(sym, None)
    return give_backs


def _warn_timeframe_mismatch(strategies: list, source: str) -> None:
    """校验策略 bar_timeframe 与回测数据频率是否一致;不一致打 warning(不报错)。"""
    from src.strategy_engine import parse_strategy as _ps
    # source 语义: daily-bars = 日线; quote-snapshots = 实盘轮询(通常 30 分钟级)
    expected = "daily" if source == "daily-bars" else "intraday"
    for raw in strategies:
        try:
            strat = raw if not isinstance(raw, dict) else _ps(raw)
        except Exception:
            continue
        setup = getattr(strat, "leveraged_breakout_pullback", None)
        if setup is None:
            continue
        tf = (getattr(setup, "bar_timeframe", "unknown") or "unknown").lower()
        if tf == "unknown":
            continue
        intraday_tfs = {"30m", "intraday", "1m", "5m", "15m", "60m", "hourly"}
        is_daily_tf = tf in {"daily", "1d", "day"}
        is_intraday_tf = tf in intraday_tfs
        mismatch = (
            (expected == "daily" and is_intraday_tf)
            or (expected == "intraday" and is_daily_tf)
        )
        if mismatch:
            import warnings
            warnings.warn(
                f"策略 '{strat.id}' 的 bar_timeframe='{tf}' 与回测数据频率('{source}', "
                f"按 {expected} 解释)不一致。lookback_bars/trend_*_bars 的语义会变化,回测结果可能不代表实盘。",
                stacklevel=2,
            )


def run_backtest(db_path: str, strategies: list[dict], accounts: dict[str, float],
                 start: str | None = None, end: str | None = None,
                 source: str = "quote-snapshots", symbols: list[str] | None = None,
                 strategy_ids: list[str] | None = None,
                 enable_selected: bool = False,
                 next_bar_execution: bool = False,
                 apply_costs: bool = False) -> dict:
    """回测策略。

    next_bar_execution: True = 信号在下一根 bar 的价格成交(更真实,推荐回测启用);
                        False(默认) = 信号当根即成交(旧行为,保持向后兼容)。
    apply_costs: True = 应用策略配置中的 commission_bps/slippage_bps; False(默认) = 无成本(旧行为)。
    """
    source_store = TradingStore(db_path)
    quotes, source_rows = _load_quotes(source_store, source, symbols, start, end)
    selected_strategies = _select_strategies(strategies, strategy_ids, enable_selected)
    _warn_timeframe_mismatch(selected_strategies, source)

    # 若启用成本,为每个 leveraged 策略注入 PaperBroker 的全局 costs(取策略自身配置;无则0)
    broker_costs = None
    if apply_costs:
        from src.trading_models import TradingCosts as _TC
        # 取第一个带 costs 的策略作为 broker 级成本(多策略场景可后续按策略拆分)
        for raw in selected_strategies:
            from src.strategy_engine import parse_strategy as _ps
            parsed = raw if not isinstance(raw, dict) else _ps(raw)
            if parsed.costs is not None:
                broker_costs = _TC(commission_bps=parsed.costs.commission_bps,
                                   slippage_bps=parsed.costs.slippage_bps)
                break

    test_store = TradingStore(":memory:")
    test_store.ensure_accounts(accounts)
    engine = StrategyEngine(selected_strategies)
    broker = PaperBroker(test_store, costs=broker_costs)

    trades: list[dict] = []
    equity_curve: list[dict] = []
    last_prices: dict[str, float] = {}
    starting_equity_by_currency = _balances(test_store)
    starting_equity = _total_equity(starting_equity_by_currency)

    # 次根成交: 挂起的买入信号(入场)延迟到下一根 bar 执行;卖出(出场)信号立即执行。
    pending_entry: list[StrategySignal] = []

    def _execute_now(signal: StrategySignal, exec_quote: Quote) -> None:
        """用 exec_quote 的价格立即执行一个信号并记录。"""
        pre_position = test_store.get_position(signal.market, signal.symbol)
        # 把信号重定价到执行 bar(次根成交时用下一根价格)
        signal = _reprice_signal(signal, exec_quote)
        execution = broker.execute(signal)
        realized_pnl = None
        risk_per_share = None
        if execution.status == "FILLED":
            if signal.action == "sell":
                realized_pnl = (execution.price - float(pre_position["avg_cost"])) * execution.quantity
            else:
                risk_per_share = float(signal.metadata.get("risk_per_share") or 0) or None
            engine.mark_filled(signal.strategy.id, signal, execution)
        trades.append({
            "date": exec_quote.timestamp[:10],
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
            "commission": execution.commission,
            "risk_per_share": risk_per_share,
        })

    for quote in quotes:
        last_prices[quote.symbol] = quote.price
        # 1) 先用本根价格执行上一根挂起的入场信号(次根成交)
        if next_bar_execution:
            for sig in pending_entry:
                _execute_now(sig, quote)
            pending_entry = []
        # 2) 生成本根信号
        new_signals = engine.generate_signals([quote])
        for signal in new_signals:
            is_entry = signal.action == "buy"
            if next_bar_execution and is_entry:
                # 入场延迟到下一根;但先记录一条"信号生成"事件便于审计
                pending_entry.append(signal)
            else:
                # 出场信号(卖出)立即执行,或 same-bar 模式下全部立即执行
                _execute_now(signal, quote)
        equity_by_currency = _equity_by_currency(test_store, last_prices)
        equity_curve.append({
            "date": quote.timestamp[:10],
            "total_equity": _total_equity(equity_by_currency),
            "equity_by_currency": equity_by_currency,
        })

    # 收尾: 仍有挂起入场信号时,按最后一根价格执行(如实盘会在下一交易日成交)
    if pending_entry and quotes:
        last_quote = quotes[-1]
        for sig in pending_entry:
            _execute_now(sig, last_quote)
        pending_entry = []

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

    risk_metrics = _compute_risk_metrics(
        equity_curve, trades, source, starting_equity, ending_total_equity)

    summary = {
        "source": source,
        "symbols": symbols or sorted({q.symbol for q in quotes}),
        "strategy_ids": [s.get("id") if isinstance(s, dict) else s.id for s in selected_strategies],
        "from": start,
        "to": end,
        "source_rows": len(source_rows) if source == "daily-bars" else len(quotes),
        "quotes_replayed": len(quotes),
        "next_bar_execution": next_bar_execution,
        "apply_costs": apply_costs,
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
        "max_drawdown_recovery_bars": risk_metrics["max_drawdown_recovery_bars"],
        "sell_win_rate_pct": (len(winning_sells) / len(sell_fills) * 100.0) if sell_fills else None,
        "sharpe_ratio": risk_metrics["sharpe_ratio"],
        "sortino_ratio": risk_metrics["sortino_ratio"],
        "calmar_ratio": risk_metrics["calmar_ratio"],
        "avg_r_multiple": risk_metrics["avg_r_multiple"],
        "profit_factor": risk_metrics["profit_factor"],
        "expectancy_per_trade": risk_metrics["expectancy_per_trade"],
        "avg_give_back_pct": risk_metrics["avg_give_back_pct"],
        "buy_hold_50pct_return_pct": _buy_hold_return(quotes, primary_symbol, fraction=0.5),
        "open_positions": _positions(test_store),
        "trades": trades,
        "equity_curve": equity_curve,
    }
    source_store.close()
    test_store.close()
    return summary


def _reprice_signal(signal: StrategySignal, exec_quote: Quote) -> StrategySignal:
    """将信号重定价到执行 bar 的价格(次根成交)。保留原 stop_price/risk_per_share 等元数据。"""
    if (exec_quote.price == signal.quote_price
            and exec_quote.timestamp == signal.quote_timestamp):
        return signal
    from dataclasses import replace
    return replace(
        signal,
        quote_price=exec_quote.price,
        quote_timestamp=exec_quote.timestamp,
        current_value=exec_quote.price,
    )


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
        f"- Execution: {'next-bar (realistic)' if summary.get('next_bar_execution') else 'same-bar'}"
        f"{', with costs' if summary.get('apply_costs') else ', no costs'}",
        f"- Orders: {summary['orders']}, fills: {summary['fills']}, rejections: {len(rejected)}",
        f"- Starting equity: {summary['starting_equity']:.2f}",
        f"- Ending total equity: {summary['ending_total_equity']:.2f}",
        f"- Total return: {summary['total_return_pct']:.2f}%",
        f"- USD account return: {_fmt_pct((summary.get('return_pct_by_currency') or {}).get('USD'))}",
        f"- Buy and hold return: {_fmt_pct(summary.get('buy_hold_return_pct'))}",
        f"- Buy and hold (50% size) return: {_fmt_pct(summary.get('buy_hold_50pct_return_pct'))}",
        f"- Max drawdown: {summary['max_drawdown_pct']:.2f}%",
        f"- USD max drawdown: {summary.get('usd_max_drawdown_pct', summary['max_drawdown_pct']):.2f}%",
        f"- Max drawdown recovery: {_fmt_bars(summary.get('max_drawdown_recovery_bars'))}",
        f"- Realized PnL: {summary['realized_pnl']:.2f}",
        f"- Sell win rate: {_fmt_pct(summary.get('sell_win_rate_pct'))}",
        "",
        "## Risk-Adjusted Metrics",
        f"- Sharpe ratio (annualized): {_fmt_num3(summary.get('sharpe_ratio'))}",
        f"- Sortino ratio (annualized): {_fmt_num3(summary.get('sortino_ratio'))}",
        f"- Calmar ratio (CAGR / max DD): {_fmt_num3(summary.get('calmar_ratio'))}",
        f"- Average R-multiple per sell: {_fmt_num3(summary.get('avg_r_multiple'))} R",
        f"- Profit factor (gross win / gross loss): {_fmt_num3(summary.get('profit_factor'))}",
        f"- Expectancy per trade: {_fmt_num2(summary.get('expectancy_per_trade'))}",
        f"- Average give-back ratio: {_fmt_pct(summary.get('avg_give_back_pct'))} "
        "(峰值浮盈被回吐的比例,越低越好)",
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


def _fmt_num2(value) -> str:
    return "N/A" if value is None else f"{float(value):.2f}"


def _fmt_num3(value) -> str:
    return "N/A" if value is None else f"{float(value):.3f}"


def _fmt_bars(value) -> str:
    if value is None:
        return "N/A (not recovered)"
    return f"{int(value)} bars"


def _month_windows(start: str, end: str, train_months: int, test_months: int) -> list[tuple[str, str, str]]:
    """生成滚动 walk-forward 窗口: (train_start, train_end, test_end)。
    每个窗口 train 段长 train_months,test 段(样本外)长 test_months,窗口每次前进 test_months。"""
    from datetime import datetime as _dt
    from dateutil.relativedelta import relativedelta as _rd
    try:
        s = _dt.fromisoformat(start)
        e = _dt.fromisoformat(end)
    except (ValueError, TypeError):
        return []
    windows: list[tuple[str, str, str]] = []
    train_start = s
    while True:
        train_end = train_start + _rd(months=train_months) - _rd(days=1)
        test_end = train_end + _rd(months=test_months)
        if train_end >= e:
            break
        if test_end > e:
            test_end = e
        windows.append((
            train_start.strftime("%Y-%m-%d"),
            train_end.strftime("%Y-%m-%d"),
            test_end.strftime("%Y-%m-%d"),
        ))
        # 窗口前进 test_months
        train_start = train_start + _rd(months=test_months)
    return windows


def run_walk_forward(db_path: str, strategies: list[dict], accounts: dict[str, float],
                     start: str, end: str,
                     train_months: int = 12, test_months: int = 3,
                     source: str = "daily-bars", symbols: list[str] | None = None,
                     strategy_ids: list[str] | None = None,
                     enable_selected: bool = False,
                     next_bar_execution: bool = False,
                     apply_costs: bool = False) -> dict:
    """Walk-forward 验证: 把全期切成滚动 train/test 窗口,
    在每个 test 窗口(样本外)独立回测,汇总结果以暴露过拟合。"""
    windows = _month_windows(start, end, train_months, test_months)
    results: list[dict] = []
    for train_start, train_end, test_end in windows:
        # 样本外(test)段独立回测,使用独立的初始资金
        test_summary = run_backtest(
            db_path, strategies, accounts,
            start=train_end,  # test 段从 train 结束的下一天开始(实际由数据过滤)
            end=test_end,
            source=source, symbols=symbols,
            strategy_ids=strategy_ids, enable_selected=enable_selected,
            next_bar_execution=next_bar_execution, apply_costs=apply_costs,
        )
        usd_ret = (test_summary.get("return_pct_by_currency") or {}).get("USD")
        results.append({
            "train_start": train_start, "train_end": train_end, "test_end": test_end,
            "test_usd_return_pct": usd_ret,
            "test_max_drawdown_pct": test_summary.get("usd_max_drawdown_pct"),
            "test_fills": test_summary.get("fills"),
            "test_sell_win_rate_pct": test_summary.get("sell_win_rate_pct"),
            "test_sharpe": test_summary.get("sharpe_ratio"),
            "test_avg_r": test_summary.get("avg_r_multiple"),
        })
    # 汇总
    oos_returns = [r["test_usd_return_pct"] for r in results if r["test_usd_return_pct"] is not None]
    positive_windows = sum(1 for r in oos_returns if r > 0)
    summary = {
        "method": "walk-forward",
        "train_months": train_months,
        "test_months": test_months,
        "start": start, "end": end,
        "windows": results,
        "window_count": len(results),
        "oos_positive_windows": positive_windows,
        "oos_negative_windows": len(oos_returns) - positive_windows,
        "oos_avg_return_pct": (sum(oos_returns) / len(oos_returns)) if oos_returns else None,
        "oos_min_return_pct": min(oos_returns) if oos_returns else None,
        "oos_max_return_pct": max(oos_returns) if oos_returns else None,
    }
    return summary


def write_walk_forward_report(summary: dict, path: str) -> None:
    output = Path(_resolve_path(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Walk-Forward 验证报告",
        "",
        "## 概述",
        f"- 方法: 滚动 walk-forward (train {summary['train_months']} 月 / test {summary['test_months']} 月)",
        f"- 全期: {summary['start']} 至 {summary['end']}",
        f"- 样本外窗口数: {summary['window_count']}",
        f"- 样本外正收益窗口: {summary['oos_positive_windows']} / 负收益: {summary['oos_negative_windows']}",
        f"- 样本外平均收益: {_fmt_pct(summary.get('oos_avg_return_pct'))}",
        f"- 样本外最差窗口: {_fmt_pct(summary.get('oos_min_return_pct'))}",
        f"- 样本外最佳窗口: {_fmt_pct(summary.get('oos_max_return_pct'))}",
        "",
        "> 若存在多个负收益样本外窗口,说明参数可能过拟合于特定区间,实盘需谨慎。",
        "",
        "## 各窗口明细(样本外 test 段)",
        "| Train 起 | Train 止 | Test 止 | 样本外收益 | 最大回撤 | 成交 | 胜率 | Sharpe | 均R |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for w in summary.get("windows", []):
        lines.append(
            f"| {w['train_start']} | {w['train_end']} | {w['test_end']} | "
            f"{_fmt_pct(w.get('test_usd_return_pct'))} | "
            f"{_fmt_pct(w.get('test_max_drawdown_pct'))} | "
            f"{w.get('test_fills', 0)} | {_fmt_pct(w.get('test_sell_win_rate_pct'))} | "
            f"{_fmt_num3(w.get('test_sharpe'))} | {_fmt_num3(w.get('test_avg_r'))} |"
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    # 新增: 回测真实性选项
    parser.add_argument("--next-bar", dest="next_bar", action="store_true",
                        help="信号在下一根 bar 成交(更真实;默认 same-bar)")
    parser.add_argument("--apply-costs", dest="apply_costs", action="store_true",
                        help="应用策略配置中的 commission_bps/slippage_bps")
    # 新增: walk-forward 子模式
    parser.add_argument("--walk-forward", dest="walk_forward", action="store_true",
                        help="运行 walk-forward 滚动验证而非单段回测")
    parser.add_argument("--train-months", dest="train_months", type=int, default=12,
                        help="walk-forward 训练窗口月数(默认12)")
    parser.add_argument("--test-months", dest="test_months", type=int, default=3,
                        help="walk-forward 样本外窗口月数(默认3)")
    args = parser.parse_args()

    app_config = load_app_config(str(CONFIG_DIR / "config.yaml"))
    paper = app_config.get("paper_trading", {}) or {}
    db_path = _resolve_path(args.db_path or paper.get("db_path", "data/trading.sqlite3"))
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    strategies = load_strategies(str(CONFIG_DIR / "strategies.yaml"))

    if args.walk_forward:
        if not args.from_date or not args.to_date:
            parser.error("--walk-forward 需要同时指定 --from 和 --to")
        summary = run_walk_forward(
            db_path, strategies, accounts, args.from_date, args.to_date,
            train_months=args.train_months, test_months=args.test_months,
            source=args.source, symbols=_split_values(args.symbol),
            strategy_ids=_split_values(args.strategy_id),
            enable_selected=args.enable_selected,
            next_bar_execution=args.next_bar, apply_costs=args.apply_costs,
        )
        if args.report:
            write_walk_forward_report(summary, args.report)
            summary["report_path"] = _resolve_path(args.report)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return

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
        next_bar_execution=args.next_bar,
        apply_costs=args.apply_costs,
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
