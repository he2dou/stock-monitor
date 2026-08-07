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
    """Convert daily_bars rows to Quote list with OHLC fields.

    Uses adj_close as price (P0-3: 除权除息后序列不失真),
    fell back to close when adj_close is unavailable or zero.
    """
    previous_close: dict[str, float] = {}
    quotes: list[Quote] = []
    for row in rows:
        adj = float(row.get("adj_close", 0) or 0)
        raw_close = float(row["close"])
        close = adj if adj > 0 else raw_close
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
            # P0-1: populate OHLC for intrabar execution model
            open=float(row.get("open", 0) or 0) or None,
            high=float(row.get("high", 0) or 0) or None,
            low=float(row.get("low", 0) or 0) or None,
        ))
    return quotes


def _quote_at_price(quote: Quote, price: float, timestamp: str | None = None) -> Quote:
    """Return a Quote copy with the given price (used for open/stop/target fills)."""
    return Quote(
        symbol=quote.symbol, name=quote.name, market=quote.market,
        price=price, change_pct=quote.change_pct, volume=quote.volume,
        timestamp=timestamp or quote.timestamp,
        open=quote.open, high=quote.high, low=quote.low,
    )


def _intrabar_fill_price(side: str, trigger_op: str, trigger_value: float,
                          quote: Quote, costs_bps: float = 0.0) -> float | None:
    """Determine if a signal would fill intrabar using OHLC.

    Returns the estimated fill price or None if the signal does not trigger
    intrabar (in which case it should be executed at close).

    For a **long** position:
    - Stop (trailing / initial): triggered when **low** <= stop.
      Conservative fill: max(low, stop) — price won't be worse than the stop.
    - Take-profit (partial): triggered when **high** >= target.
      Fill at target (price passed through the level).
    - All other exit kinds (time_stop): not intrabar — return None.
    """
    has_ohlc = quote.low is not None and quote.high is not None
    if not has_ohlc or side != "sell":
        return None

    if trigger_op in ("trailing_or_initial_stop", "initial_stop", "stop_loss"):
        if quote.low <= trigger_value:
            # Fill at the stop level (conservative: cannot be better than stop)
            # Slippage: if the bar gaps through the stop, fill at max(low, stop)
            filled = max(float(quote.low), trigger_value)
            return filled
        return None

    if trigger_op == "partial_take_profit":
        if quote.high >= trigger_value:
            return trigger_value
        return None

    # time_stop, confirmation, etc.: not an intrabar event
    return None


def _load_quotes(store: TradingStore, source: str, symbols: list[str] | None,
                 start: str | None, end: str | None,
                 warmup_days: int = 0) -> tuple[list[Quote], list[dict]]:
    """Load quotes.  When warmup_days > 0 and source is daily-bars, loads
    extra bars before *start* for warm-up (history seeding).

    Returns (quotes, source_rows).
    """
    if source == "daily-bars":
        load_start = start
        if warmup_days > 0 and start:
            from datetime import datetime as _dt, timedelta as _td
            try:
                s = _dt.fromisoformat(start)
                load_start = (s - _td(days=warmup_days)).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                load_start = start
        rows = store.load_daily_bars(symbols=symbols or None, start=load_start, end=end)
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


def _total_equity(equity_by_currency: dict[str, float],
                  fx_rates: dict[str, float] | None = None) -> float:
    """Sum multi-currency equity, optionally converting via fx_rates to a single base.

    fx_rates: e.g. {"USD": 7.2, "HKD": 0.92, "CNY": 1.0} means
    1 USD = 7.2 base-units, 1 HKD = 0.92 base-units.  When None the raw
    sum is returned (old behaviour).
    """
    if not fx_rates:
        return sum(float(value) for value in equity_by_currency.values())
    total = 0.0
    for currency, value in equity_by_currency.items():
        rate = fx_rates.get(currency)
        if rate is not None and rate > 0:
            total += float(value) * rate
        else:
            total += float(value)  # fallback: treat as 1:1
    return total


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
                 apply_costs: bool = False,
                 warmup_days: int = 0,
                 fx_rates: dict[str, float] | None = None) -> dict:
    """回测策略。

    next_bar_execution: True = 入场信号在下一根 bar 成交(推荐回测启用)。
                        OHLC 可用时用 open 成交,否则用 close。
                        False(默认) = 信号当根即成交(旧行为,向后兼容)。
    apply_costs: True = 应用策略佣金/滑点。
    warmup_days: >0 时,额外加载 start 之前的日线用于策略预热(仅计算指标,不交易)。
                 仅对 source=daily-bars 生效。
    fx_rates: 多币种汇率 {"USD": 7.2, "HKD": 0.92, "CNY": 1.0} → 以 CNY 计价。
              None 时沿用旧行为(直接求和)。
    """
    source_store = TradingStore(db_path)
    quotes, source_rows = _load_quotes(source_store, source, symbols, start, end,
                                        warmup_days=warmup_days)
    selected_strategies = _select_strategies(strategies, strategy_ids, enable_selected)
    # 回测允许在任意有行情的代码上评估策略,不局限于策略在策略页绑定的代码。
    # 指定单个 symbols 时,把所选策略的 symbol 临时指向该代码,使 StrategyEngine
    # (它按 strategy.symbol 匹配行情) 能匹配到回测喂入的行情。仅影响回测,
    # 不改变实盘监控里"策略只对绑定代码触发"的语义。
    if symbols and len(symbols) == 1:
        target_symbol = symbols[0]
        selected_strategies = [
            {**dict(s), "symbol": target_symbol} for s in selected_strategies
        ]
    _warn_timeframe_mismatch(selected_strategies, source)

    # 若启用成本,为每个 leveraged 策略注入 PaperBroker 的全局 costs(取策略自身配置;无则0)
    broker_costs = None
    if apply_costs:
        from src.trading_models import TradingCosts as _TC
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
    starting_equity = _total_equity(starting_equity_by_currency, fx_rates)

    # 次根成交: 挂起的买入信号(入场)延迟到下一根 bar;卖出(出场)信号立即执行。
    pending_entry: list[StrategySignal] = []

    # --- P0-5 预热: 把 start 之前的 bar 只喂给引擎(积累 history),不交易 ---
    warmup_quotes: list[Quote] = []
    active_quotes: list[Quote] = []
    if warmup_days > 0 and start and source == "daily-bars":
        for q in quotes:
            if q.timestamp[:10] < start:
                warmup_quotes.append(q)
            else:
                active_quotes.append(q)
    else:
        active_quotes = list(quotes)

    for wq in warmup_quotes:
        engine.generate_signals([wq])

    slippage_bps_val = broker_costs.slippage_bps if broker_costs else 0.0

    def _record_trade(signal: StrategySignal, execution: OrderExecution,
                      exec_date: str, pre_position: dict) -> None:
        realized_pnl = None
        risk_per_share = None
        if execution.status == "FILLED":
            if signal.action == "sell":
                realized_pnl = (execution.price - float(pre_position["avg_cost"])) * execution.quantity
            else:
                risk_per_share = float(signal.metadata.get("risk_per_share") or 0) or None
            engine.mark_filled(signal.strategy.id, signal, execution)
        trades.append({
            "date": exec_date,
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

    def _execute_and_record(signal: StrategySignal, exec_quote: Quote) -> None:
        pre_pos = test_store.get_position(signal.market, signal.symbol)
        signal = _reprice_signal(signal, exec_quote)
        execution = broker.execute(signal)
        _record_trade(signal, execution, exec_quote.timestamp[:10], pre_pos)

    for quote in active_quotes:
        last_prices[quote.symbol] = quote.price
        bar_date = quote.timestamp[:10]
        has_ohlc = (source == "daily-bars"
                    and quote.open is not None
                    and quote.high is not None
                    and quote.low is not None)

        # ---- 1) OPEN 阶段: 执行上一根挂起的入场信号 ----
        if next_bar_execution and pending_entry:
            fill_quote = _quote_at_price(quote, float(quote.open), quote.timestamp) if has_ohlc else quote
            for sig in pending_entry:
                _execute_and_record(sig, fill_quote)
            pending_entry = []

        # ---- 2) CLOSE 阶段: 生成本根信号 ----
        new_signals = engine.generate_signals([quote])

        # ---- 3) INTRABAR 阶段: 检查出场信号是否盘中已触发 ----
        # 将 sell 信号按 intrabar / close 分两组
        intrabar_signals: list[tuple[StrategySignal, float]] = []
        close_signals: list[StrategySignal] = []

        for signal in new_signals:
            is_entry = signal.action == "buy"
            if is_entry:
                # 入场信号: next_bar 模式延迟,否则立即执行
                if next_bar_execution:
                    pending_entry.append(signal)
                else:
                    # same-bar: 若 OHLC 可用,用 open 成交(模拟盘中突破入场)
                    exec_q = (_quote_at_price(quote, float(quote.open), quote.timestamp)
                              if (has_ohlc and signal.trigger_op not in ("above", "below"))
                              else quote)
                    _execute_and_record(signal, exec_q)
            else:
                # 出场信号: 先检查是否在盘中触发
                intrabar_price = _intrabar_fill_price(
                    signal.action, signal.trigger_op, signal.trigger_value,
                    quote, slippage_bps_val)
                if intrabar_price is not None:
                    intrabar_signals.append((signal, intrabar_price))
                else:
                    close_signals.append(signal)

        # 先执行盘中触发(partial 在 stop 前,保持强趋势)
        for signal, fill_price in intrabar_signals:
            exec_q = _quote_at_price(quote, fill_price, quote.timestamp)
            _execute_and_record(signal, exec_q)
        # 再执行收盘触发(可能因盘中已平仓而被拒——engine.mark_filled 已更新状态)
        for signal in close_signals:
            _execute_and_record(signal, quote)

        # ---- 4) 记录权益曲线 ----
        equity_by_currency = _equity_by_currency(test_store, last_prices)
        equity_curve.append({
            "date": bar_date,
            "total_equity": _total_equity(equity_by_currency, fx_rates),
            "equity_by_currency": equity_by_currency,
        })

    # 收尾: 仍有挂起入场信号时,按最后一根价格执行
    if pending_entry and active_quotes:
        last_quote = active_quotes[-1]
        for sig in pending_entry:
            _execute_and_record(sig, last_quote)
        pending_entry = []

    ending_equity_by_currency = _equity_by_currency(test_store, last_prices)
    ending_total_equity = _total_equity(ending_equity_by_currency, fx_rates)
    return_pct_by_currency = {
        currency: ((ending_equity_by_currency.get(currency, 0.0) - start_value) / start_value * 100.0)
        for currency, start_value in starting_equity_by_currency.items()
        if start_value
    }
    sell_fills = [t for t in trades if t["status"] == "FILLED" and t["side"] == "sell"]
    winning_sells = [t for t in sell_fills if (t.get("realized_pnl") or 0) > 0]
    primary_symbol = (symbols or [active_quotes[0].symbol if active_quotes else None])[0]

    # Risk metrics use fx_rates for consistency
    risk_metrics = _compute_risk_metrics(
        equity_curve, trades, source, starting_equity, ending_total_equity)

    active_source_rows = [r for r in (source_rows if source == "daily-bars" else [])
                          if not start or r["date"] >= start]

    summary = {
        "source": source,
        "symbols": symbols or sorted({q.symbol for q in active_quotes}),
        "strategy_ids": [s.get("id") if isinstance(s, dict) else s.id for s in selected_strategies],
        "from": start,
        "to": end,
        "source_rows": len(active_source_rows),
        "quotes_replayed": len(active_quotes),
        "warmup_bars_used": len(warmup_quotes),
        "intrabar_execution": (source == "daily-bars"
                                and active_quotes
                                and active_quotes[0].open is not None),
        "next_bar_execution": next_bar_execution,
        "apply_costs": apply_costs,
        "fx_rates": fx_rates,
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
        "buy_hold_return_pct": _buy_hold_return(active_quotes, primary_symbol),
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
        "buy_hold_50pct_return_pct": _buy_hold_return(active_quotes, primary_symbol, fraction=0.5),
        "open_positions": _positions(test_store),
        "trades": trades,
        "equity_curve": equity_curve,
    }
    # P2: 富化交易级统计
    _enrich_trade_stats(summary, trades, equity_curve, source)
    # P2: 月度收益表
    summary["monthly_returns"] = _monthly_returns(equity_curve)
    # P2: Bootstrap 置信区间
    if len(equity_curve) >= 2 and source == "daily-bars":
        eq = [float(p["total_equity"]) for p in equity_curve]
        daily_returns = [(eq[i] - eq[i - 1]) / eq[i - 1] for i in range(1, len(eq)) if eq[i - 1] > 0]
        summary["bootstrap"] = _bootstrap_confidence(daily_returns)
    # P2: 小样本警告
    total_fills = summary["fills"]
    if total_fills > 0 and (not sell_fills or len(sell_fills) < 30):
        n_sells = len(sell_fills) if sell_fills else 0
        summary.setdefault("warnings", []).append(
            f"仅 {n_sells} 笔卖出成交(共 {total_fills} 笔),统计指标(胜率/Sharpe等)可靠性有限")
    source_store.close()
    test_store.close()
    return summary


# ---------------------------------------------------------------------------
# P2: 交易级统计
# ---------------------------------------------------------------------------

def _enrich_trade_stats(summary: dict, trades: list[dict],
                         equity_curve: list[dict], source: str) -> None:
    """向 summary 注入交易级统计指标(平均持仓/连续亏损/最大单笔盈亏等)。"""
    filled = [t for t in trades if t["status"] == "FILLED"]
    buys = [t for t in filled if t["side"] == "buy"]
    sells = [t for t in filled if t["side"] == "sell"]
    losses = [t for t in sells if (t.get("realized_pnl") or 0) < 0]
    wins = [t for t in sells if (t.get("realized_pnl") or 0) > 0]

    # 持仓周期: 配对 buy→sell,按 symbol+strategy 分组
    holding_bars: list[int] = []
    entry_dates: dict[str, int] = {}  # key = strategy_id|symbol → bar index
    for idx, t in enumerate(filled):
        key = f"{t['strategy_id']}|{t['symbol']}"
        if t["side"] == "buy":
            entry_dates[key] = idx
        elif t["side"] == "sell" and key in entry_dates:
            bars = idx - entry_dates.pop(key)
            holding_bars.append(bars)

    avg_hold = sum(holding_bars) / len(holding_bars) if holding_bars else None

    # 连续亏损
    max_consecutive_losses = 0
    current_streak = 0
    for t in sells:
        pnl = t.get("realized_pnl") or 0
        if pnl < 0:
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        else:
            current_streak = 0

    # 最大单笔盈亏
    max_win = max((t.get("realized_pnl") or 0) for t in wins) if wins else None
    max_loss = min((t.get("realized_pnl") or 0) for t in losses) if losses else None

    # 收支比 (gross profit / gross loss)
    gross_profit = sum(t.get("realized_pnl") or 0 for t in wins)
    gross_loss = abs(sum(t.get("realized_pnl") or 0 for t in losses))

    # 年化交易频率
    n_years = (len(equity_curve) / 252.0) if source == "daily-bars" else (
        len(equity_curve) / (252.0 * 6.5) if source == "quote-snapshots" else 1.0)
    n_years = max(n_years, 0.01)
    trades_per_year = len(sells) / n_years if sells else 0.0

    # 亏损集中度: 最大单笔亏损 / 总亏损
    loss_concentration = (abs(max_loss) / gross_loss) if max_loss and gross_loss > 0 else None

    # 时间在市场中的占比
    exposure_days = 0.0
    in_position = False
    position_count = 0
    for t in filled:
        if t["side"] == "buy":
            position_count += int(t.get("quantity", 0))
            in_position = position_count > 0
        elif t["side"] == "sell":
            position_count -= int(t.get("quantity", 0))
            in_position = position_count > 0
        if in_position:
            exposure_days += 1.0
    exposure_pct = (exposure_days / len(equity_curve) * 100.0) if equity_curve else 0.0

    summary["trade_stats"] = {
        "filled_buys": len(buys),
        "filled_sells": len(sells),
        "avg_holding_bars": avg_hold,
        "max_consecutive_losses": max_consecutive_losses if max_consecutive_losses > 0 else None,
        "max_single_win": max_win,
        "max_single_loss": max_loss,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "trades_per_year": round(trades_per_year, 1),
        "loss_concentration_pct": (loss_concentration * 100.0) if loss_concentration is not None else None,
        "exposure_pct": round(exposure_pct, 1),
    }


# ---------------------------------------------------------------------------
# P2: 月度收益表
# ---------------------------------------------------------------------------

def _monthly_returns(equity_curve: list[dict]) -> list[dict]:
    """从权益曲线计算月度收益表。"""
    if not equity_curve:
        return []
    # 按月份分组,取每月首尾日权益
    monthly: dict[str, list[float]] = {}
    for p in equity_curve:
        month_key = p["date"][:7]  # YYYY-MM
        monthly.setdefault(month_key, []).append(float(p["total_equity"]))

    result: list[dict] = []
    months = sorted(monthly.keys())
    for i, m in enumerate(months):
        equities = monthly[m]
        start_eq = equities[0]
        end_eq = equities[-1]
        ret_pct = ((end_eq - start_eq) / start_eq * 100.0) if start_eq > 0 else 0.0
        result.append({
            "month": m,
            "return_pct": round(ret_pct, 2),
            "start_equity": round(start_eq, 2),
            "end_equity": round(end_eq, 2),
        })
    return result


# ---------------------------------------------------------------------------
# P2: Bootstrap 置信区间
# ---------------------------------------------------------------------------

def _bootstrap_confidence(returns: list[float], n_iter: int = 1000,
                           ci: float = 0.95) -> dict:
    """对日收益序列做 bootstrap,估计平均收益和 Sharpe 的置信区间。"""
    import random as _random
    if len(returns) < 2:
        return {}
    periods_per_year = 252.0
    _random.seed(42)
    means: list[float] = []
    sharpes: list[float] = []
    n = len(returns)
    for _ in range(n_iter):
        sample = [_random.choice(returns) for _ in range(n)]
        avg = sum(sample) / n
        std = (sum((r - avg) ** 2 for r in sample) / n) ** 0.5 if n > 1 else 0.0
        means.append(avg * 100.0)  # 百分比
        sharpes.append((avg / std * (periods_per_year ** 0.5)) if std > 0 else 0.0)

    means.sort()
    sharpes.sort()
    tail = int(n_iter * (1 - ci) / 2)
    return {
        "bootstrap_iterations": n_iter,
        "ci_level": ci,
        "mean_return_pct_ci_low": round(means[tail], 3),
        "mean_return_pct_ci_high": round(means[-tail - 1], 3),
        "sharpe_ci_low": round(sharpes[tail], 3),
        "sharpe_ci_high": round(sharpes[-tail - 1], 3),
    }


# ---------------------------------------------------------------------------
# P2: 成本敏感性分析
# ---------------------------------------------------------------------------

def run_cost_sensitivity(db_path: str, strategies: list[dict],
                          accounts: dict[str, float],
                          start: str, end: str,
                          multipliers: list[float] | None = None,
                          source: str = "daily-bars",
                          symbols: list[str] | None = None,
                          strategy_ids: list[str] | None = None,
                          enable_selected: bool = False,
                          next_bar_execution: bool = False,
                          warmup_days: int = 0,
                          fx_rates: dict[str, float] | None = None) -> dict:
    """成本敏感性: 用不同佣金/滑点倍数运行回测,看策略是否在成本上升时仍盈利。

    multipliers: 默认 [0, 1, 2, 3, 5] (0=无成本, 1=策略配置的成本, 2/3/5=2/3/5倍成本)
    """
    if multipliers is None:
        multipliers = [0.0, 1.0, 2.0, 3.0, 5.0]

    # 先读取策略自身成本
    from src.strategy_engine import parse_strategy as _ps
    from src.trading_models import TradingCosts as _TC
    base_costs = None
    for raw in strategies:
        parsed = _ps(raw) if isinstance(raw, dict) else raw
        if parsed.costs is not None:
            base_costs = _TC(commission_bps=parsed.costs.commission_bps,
                            slippage_bps=parsed.costs.slippage_bps)
            break

    results: list[dict] = []
    for mult in multipliers:
        variant_strategies = _copy_strategies(strategies)
        if mult == 0:
            # 无成本: 不设置 costs
            for s in variant_strategies:
                s.pop("costs", None)
            apply = False
        elif base_costs is not None:
            # 设置倍数成本
            for s in variant_strategies:
                s["costs"] = {
                    "commission_bps": base_costs.commission_bps * mult,
                    "slippage_bps": base_costs.slippage_bps * mult,
                }
            apply = True
        else:
            apply = False

        s = run_backtest(
            db_path, variant_strategies, accounts, start, end,
            source=source, symbols=symbols, strategy_ids=strategy_ids,
            enable_selected=enable_selected, next_bar_execution=next_bar_execution,
            apply_costs=apply, warmup_days=warmup_days, fx_rates=fx_rates,
        )
        results.append({
            "cost_multiplier": mult,
            "total_return_pct": s["total_return_pct"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "sharpe_ratio": s.get("sharpe_ratio"),
            "calmar_ratio": s.get("calmar_ratio"),
            "fills": s["fills"],
            "trade_events": s["trade_events"],
        })

    # 盈亏平衡点: return 从正变负的倍数
    breakeven = None
    for i in range(1, len(results)):
        if results[i - 1]["total_return_pct"] > 0 and results[i]["total_return_pct"] <= 0:
            # 线性插值
            prev_m = results[i - 1]["cost_multiplier"]
            prev_r = results[i - 1]["total_return_pct"]
            curr_m = results[i]["cost_multiplier"]
            curr_r = results[i]["total_return_pct"]
            if prev_r != curr_r:
                breakeven = prev_m + (0 - prev_r) / (curr_r - prev_r) * (curr_m - prev_m)
            break

    return {
        "results": results,
        "breakeven_multiplier": round(breakeven, 2) if breakeven is not None else None,
    }


# ---------------------------------------------------------------------------
# P2: 基准指数对比
# ---------------------------------------------------------------------------

def _benchmark_return(db_path: str, benchmark_symbol: str, start: str, end: str,
                       source: str = "daily-bars") -> float | None:
    """计算基准标的(如 QQQ/SPY)的买入持有收益率。"""
    try:
        store = TradingStore(db_path)
        rows = store.load_daily_bars(
            symbols=[benchmark_symbol], start=start, end=end)
        store.close()
        if not rows or len(rows) < 2:
            return None
        quotes, _ = _quotes_from_daily_bars(rows), rows
        return _buy_hold_return(quotes, benchmark_symbol)
    except Exception:
        return None


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
        "# Strategy Backtest Report",
        "",
        "## Summary",
        f"- Source: `{summary['source']}`",
        f"- Symbols: {', '.join(summary.get('symbols') or [])}",
        f"- Strategies: {', '.join(summary.get('strategy_ids') or [])}",
        f"- Period: {summary.get('from')} to {summary.get('to')}",
        f"- Bars replayed: {summary['quotes_replayed']}"
        f"{' (+' + str(summary.get('warmup_bars_used', 0)) + ' warm-up bars)' if summary.get('warmup_bars_used') else ''}",
        f"- Execution: {'next-bar (realistic)' if summary.get('next_bar_execution') else 'same-bar'}"
        f"{', with costs' if summary.get('apply_costs') else ', no costs'}"
        f"{', intrabar OHLC' if summary.get('intrabar_execution') else ''}",
        f"- FX rates: {summary.get('fx_rates') or 'none (raw sum)'}",
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
    # P2: 基准对比
    bench = summary.get("benchmark")
    if bench:
        lines.extend([
            "",
            "## Benchmark Comparison",
            f"- Benchmark: `{bench['symbol']}` buy & hold return: {_fmt_pct(bench.get('buy_hold_return_pct'))}",
            f"- Strategy excess return: {_fmt_pct(summary.get('excess_return_vs_benchmark_pct'))}",
        ])
    # P2: 交易级统计
    ts = summary.get("trade_stats")
    if ts:
        lines.extend([
            "",
            "## Trade-Level Statistics",
            f"- Filled buys: {ts['filled_buys']}, sells: {ts['filled_sells']}",
            f"- Average holding bars: {_fmt_num(ts.get('avg_holding_bars'))}",
            f"- Trades per year: {ts.get('trades_per_year', 'N/A')}",
            f"- Max single win: {_fmt_num(ts.get('max_single_win'))}, max single loss: {_fmt_num(ts.get('max_single_loss'))}",
            f"- Max consecutive losses: {ts.get('max_consecutive_losses', 'N/A')}",
            f"- Loss concentration (largest / total): {_fmt_pct(ts.get('loss_concentration_pct'))}",
            f"- Time in market (exposure): {ts.get('exposure_pct', 'N/A')}%",
        ])
    # P2: 月度收益
    mr = summary.get("monthly_returns")
    if mr:
        lines.extend(["", "## Monthly Returns", "| Month | Return % | Start Equity | End Equity |", "|---|---:|---:|---:|"])
        for m in mr[-12:]:  # 最近12个月
            lines.append(f"| {m['month']} | {m['return_pct']:+.2f}% | {m['start_equity']:.2f} | {m['end_equity']:.2f} |")
    # P2: Bootstrap
    bs = summary.get("bootstrap")
    if bs:
        lines.extend([
            "",
            "## Bootstrap Confidence Intervals (daily returns)",
            f"- Iterations: {bs['bootstrap_iterations']}, CI: {bs['ci_level']*100:.0f}%",
            f"- Mean daily return: [{bs['mean_return_pct_ci_low']:.3f}%, {bs['mean_return_pct_ci_high']:.3f}%]",
            f"- Sharpe ratio: [{bs['sharpe_ci_low']:.3f}, {bs['sharpe_ci_high']:.3f}]",
        ])
    # P2: 小样本警告
    if summary.get("warnings"):
        lines.extend(["", "## Warnings"])
        for w in summary["warnings"]:
            lines.append(f"- ⚠ {w}")
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


def _month_windows(start: str, end: str, train_months: int, test_months: int) -> list[tuple[str, str, str, str]]:
    """生成滚动 walk-forward 窗口: (train_start, train_end, test_start, test_end)。
    每个窗口 train 段长 train_months,test 段长 test_months,每次前进 test_months。
    P1-8: test 段从 train_end+1 天开始,避免与 train 段重叠一天。"""
    from datetime import datetime as _dt, timedelta as _td
    from dateutil.relativedelta import relativedelta as _rd
    try:
        s = _dt.fromisoformat(start)
        e = _dt.fromisoformat(end)
    except (ValueError, TypeError):
        return []
    windows: list[tuple[str, str, str, str]] = []
    train_start = s
    while True:
        train_end = train_start + _rd(months=train_months) - _rd(days=1)
        test_start_dt = train_end + _td(days=1)
        test_end = train_end + _rd(months=test_months)
        if train_end >= e:
            break
        if test_end > e:
            test_end = e
        if test_start_dt >= e:
            break
        windows.append((
            train_start.strftime("%Y-%m-%d"),
            train_end.strftime("%Y-%m-%d"),
            test_start_dt.strftime("%Y-%m-%d"),
            test_end.strftime("%Y-%m-%d"),
        ))
        # 窗口前进 test_months
        train_start = train_start + _rd(months=test_months)
    return windows


# ---------------------------------------------------------------------------
# P1-10: 数据健康检查
# ---------------------------------------------------------------------------

def _validate_daily_bars(rows: list[dict]) -> list[str]:
    """检查 daily_bars 数据质量,返回警告列表(空=健康)。"""
    warnings: list[str] = []
    if not rows:
        return ["daily_bars 为空,无法回测"]

    # 按 symbol 分组
    by_symbol: dict[str, list[dict]] = {}
    for r in rows:
        by_symbol.setdefault(r["symbol"], []).append(r)

    for symbol, bars in by_symbol.items():
        if len(bars) < 2:
            warnings.append(f"{symbol}: 仅有 {len(bars)} 根日线,统计可能不可靠")
            continue

        bars_sorted = sorted(bars, key=lambda b: b["date"])
        from datetime import datetime as _dt, timedelta as _td

        # 检查重复日期
        dates = [b["date"] for b in bars_sorted]
        dupes = {d for d in dates if dates.count(d) > 1}
        if dupes:
            warnings.append(f"{symbol}: 重复日期 {sorted(dupes)[:5]}{'...' if len(dupes) > 5 else ''}")

        # 检查价格跳变(>30% 单日)
        for i in range(1, len(bars_sorted)):
            prev_c = float(bars_sorted[i - 1]["close"])
            curr_c = float(bars_sorted[i]["close"])
            if prev_c > 0 and abs(curr_c - prev_c) / prev_c > 0.30:
                warnings.append(
                    f"{symbol}: {bars_sorted[i]['date']} 价格跳变 >30% "
                    f"({prev_c:.2f} → {curr_c:.2f})")
                break  # 每 symbol 最多一条跳变警告

        # 检查 adj_close 与 close 差异(>20% 说明复权影响大)
        adj_close_bars = [b for b in bars_sorted
                          if float(b.get("adj_close", 0) or 0) > 0
                          and abs(float(b["adj_close"]) - float(b["close"])) / max(float(b["close"]), 0.01) > 0.20]
        if adj_close_bars:
            warnings.append(
                f"{symbol}: adj_close 与 close 差异 >20%,共 {len(adj_close_bars)} 日")

        # 检查零成交量
        zero_vol = [b for b in bars_sorted if float(b.get("volume", 0) or 0) <= 0]
        if len(zero_vol) > len(bars_sorted) * 0.1:
            warnings.append(
                f"{symbol}: {len(zero_vol)}/{len(bars_sorted)} 日成交量为零")

        # 检查日期间隙(>5 个自然日)
        for i in range(1, len(bars_sorted)):
            try:
                d1 = _dt.fromisoformat(bars_sorted[i - 1]["date"])
                d2 = _dt.fromisoformat(bars_sorted[i]["date"])
                gap = (d2 - d1).days
                if gap > 5:
                    warnings.append(
                        f"{symbol}: {bars_sorted[i-1]['date']} → {bars_sorted[i]['date']} 间隔 {gap} 日")
                    break
            except (ValueError, TypeError):
                pass

    return warnings


# ---------------------------------------------------------------------------
# P1-9: 参数敏感性分析
# ---------------------------------------------------------------------------

def _deep_set(d: dict, path: str, value):
    """Set a nested dict value by dot-path: 'a.b.c' → d['a']['b']['c'] = value."""
    keys = path.split(".")
    current = d
    for key in keys[:-1]:
        if key not in current:
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


def _deep_get(d: dict, path: str, default=None):
    """Get a nested dict value by dot-path."""
    keys = path.split(".")
    current = d
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def _param_path_strip_strat(path: str) -> str:
    """Strip the leading strategy-id segment from a param dot-path."""
    parts = path.split(".", 1)
    return parts[1] if len(parts) > 1 else path


def _copy_strategies(strategies: list[dict]) -> list[dict]:
    """Deep copy a list of strategy dicts (only dict level, values are primitives)."""
    import copy
    return copy.deepcopy(strategies)


def run_param_sensitivity(db_path: str, strategies: list[dict], accounts: dict[str, float],
                          start: str, end: str,
                          param_paths: list[str],
                          perturbations: list[float] | None = None,
                          source: str = "daily-bars", symbols: list[str] | None = None,
                          strategy_ids: list[str] | None = None,
                          enable_selected: bool = False,
                          next_bar_execution: bool = False,
                          apply_costs: bool = False,
                          warmup_days: int = 0,
                          fx_rates: dict[str, float] | None = None) -> dict:
    """参数敏感性分析: 对每个参数做 ±N% 扰动,看关键指标变化。

    param_paths: dot-path 列表,如
      ['soxl.leveraged_breakout_pullback.trailing_stop_pct',
       'soxl.leveraged_breakout_pullback.lookback_bars']
    perturbations: 扰动比例列表,默认 [-0.2, -0.1, 0.1, 0.2]

    Returns {param_path: [{perturbation, metrics_summary}, ...], baseline: {...}}
    """
    if perturbations is None:
        perturbations = [-0.20, -0.10, 0.10, 0.20]

    # 先跑 baseline
    baseline = run_backtest(
        db_path, strategies, accounts, start, end,
        source=source, symbols=symbols, strategy_ids=strategy_ids,
        enable_selected=enable_selected, next_bar_execution=next_bar_execution,
        apply_costs=apply_costs, warmup_days=warmup_days, fx_rates=fx_rates,
    )
    base_metrics = {
        "total_return_pct": baseline["total_return_pct"],
        "max_drawdown_pct": baseline["max_drawdown_pct"],
        "sharpe_ratio": baseline.get("sharpe_ratio"),
        "calmar_ratio": baseline.get("calmar_ratio"),
        "avg_r_multiple": baseline.get("avg_r_multiple"),
        "sell_win_rate_pct": baseline.get("sell_win_rate_pct"),
        "fills": baseline["fills"],
    }

    result: dict = {"baseline": base_metrics, "parameters": {}}
    for param_path in param_paths:
        param_results: list[dict] = []
        base_value = _deep_get(strategies[0], _param_path_strip_strat(param_path)) if strategies else None
        if base_value is None:
            continue
        if not isinstance(base_value, (int, float)):
            continue

        for pct in perturbations:
            new_value = float(base_value) * (1.0 + pct)
            if isinstance(base_value, int):
                new_value = round(new_value)
            new_value = max(new_value, 1)  # 不能为 0 或负

            variant_strategies = _copy_strategies(strategies)
            # Find strategy matching the param_path prefix (strategy_id)
            strat_id = param_path.split(".")[0]
            for s in variant_strategies:
                if s.get("id") == strat_id:
                    _deep_set(s, _param_path_strip_strat(param_path), new_value)
                    break

            s = run_backtest(
                db_path, variant_strategies, accounts, start, end,
                source=source, symbols=symbols, strategy_ids=strategy_ids,
                enable_selected=enable_selected, next_bar_execution=next_bar_execution,
                apply_costs=apply_costs, warmup_days=warmup_days, fx_rates=fx_rates,
            )
            param_results.append({
                "perturbation_pct": round(pct * 100, 1),
                "value": new_value,
                "total_return_pct": s["total_return_pct"],
                "max_drawdown_pct": s["max_drawdown_pct"],
                "sharpe_ratio": s.get("sharpe_ratio"),
                "calmar_ratio": s.get("calmar_ratio"),
                "avg_r_multiple": s.get("avg_r_multiple"),
                "fills": s["fills"],
            })
        result["parameters"][param_path] = {
            "base_value": base_value,
            "results": param_results,
        }
    return result


def _slice_quotes_by_date(quotes: list[Quote], start: str, end: str) -> list[Quote]:
    """Return quotes whose timestamp[:10] is in [start, end] (inclusive)."""
    return [q for q in quotes if start <= q.timestamp[:10] <= end]


def _walk_forward_objective(summary: dict, metric: str = "calmar") -> float:
    """Compute optimization objective from a backtest summary.

    Supported metrics:
    - "calmar": CAGR / |max DD|
    - "sharpe": Sharpe ratio
    - "return_over_maxdd": total_return / |max DD|
    - "total_return": total_return_pct
    - "avg_r": avg R-multiple per sell
    """
    if metric == "calmar":
        return summary.get("calmar_ratio") or -999.0
    if metric == "sharpe":
        return summary.get("sharpe_ratio") or -999.0
    if metric == "return_over_maxdd":
        ret = summary.get("total_return_pct") or 0.0
        dd = abs(summary.get("max_drawdown_pct") or 1.0)
        return ret / dd if dd > 0 else -999.0
    if metric == "total_return":
        return summary.get("total_return_pct") or -999.0
    if metric == "avg_r":
        return summary.get("avg_r_multiple") or -999.0
    return summary.get("total_return_pct") or 0.0


def _param_grid_search(db_path: str, strategies: list[dict], accounts: dict[str, float],
                       param_grid: dict[str, list],
                       train_start: str, train_end: str,
                       source: str, symbols: list[str] | None,
                       strategy_ids: list[str] | None,
                       enable_selected: bool,
                       next_bar_execution: bool, apply_costs: bool,
                       warmup_days: int, fx_rates: dict[str, float] | None,
                       objective: str = "calmar",
                       max_combinations: int = 100) -> tuple[dict, list[dict]]:
    """在 train 段做参数网格/随机搜索,返回 (最优策略配置, 各组合结果列表)。

    param_grid: {"strat_id.path.to.param": [val1, val2, ...], ...}
    max_combinations: 最大尝试组合数(超出时用随机抽样)。
    """
    import random

    if not param_grid:
        return strategies, []

    # 展开所有组合
    keys = list(param_grid.keys())
    values = list(param_grid.values())

    # 计算笛卡尔积大小
    import math as _math
    total = _math.prod(len(v) for v in values)

    all_combos: list[dict[str, object]] = []
    if total <= max_combinations:
        # Full grid
        def _cartesian(idx: int, current: dict):
            if idx == len(keys):
                all_combos.append(dict(current))
                return
            for val in values[idx]:
                current[keys[idx]] = val
                _cartesian(idx + 1, current)
        _cartesian(0, {})
    else:
        # Random sampling
        random.seed(42)
        for _ in range(max_combinations):
            combo = {k: random.choice(v) for k, v in param_grid.items()}
            all_combos.append(combo)

    best_score = float("-inf")
    best_params: dict[str, object] = {}
    combo_results: list[dict] = []

    for combo in all_combos:
        variant = _copy_strategies(strategies)
        for path, val in combo.items():
            strat_id = path.split(".")[0]
            for s in variant:
                if s.get("id") == strat_id:
                    _deep_set(s, _param_path_strip_strat(path), val)
                    break

        s = run_backtest(
            db_path, variant, accounts, train_start, train_end,
            source=source, symbols=symbols, strategy_ids=strategy_ids,
            enable_selected=enable_selected, next_bar_execution=next_bar_execution,
            apply_costs=apply_costs, warmup_days=warmup_days, fx_rates=fx_rates,
        )
        score = _walk_forward_objective(s, objective)
        combo_results.append({
            "params": {k: v for k, v in combo.items()},
            "score": score,
            "return_pct": s["total_return_pct"],
            "max_dd_pct": s["max_drawdown_pct"],
            "sharpe": s.get("sharpe_ratio"),
        })
        if score > best_score:
            best_score = score
            best_params = dict(combo)

    # Apply best params to strategies
    best_strategies = _copy_strategies(strategies)
    if best_params:
        for path, val in best_params.items():
            strat_id = path.split(".")[0]
            for s in best_strategies:
                if s.get("id") == strat_id:
                    _deep_set(s, _param_path_strip_strat(path), val)
                    break

    return best_strategies, combo_results


def run_walk_forward(db_path: str, strategies: list[dict], accounts: dict[str, float],
                     start: str, end: str,
                     train_months: int = 12, test_months: int = 3,
                     source: str = "daily-bars", symbols: list[str] | None = None,
                     strategy_ids: list[str] | None = None,
                     enable_selected: bool = False,
                     next_bar_execution: bool = False,
                     apply_costs: bool = False,
                     warmup_days: int = 0,
                     fx_rates: dict[str, float] | None = None,
                     param_grid: dict[str, list] | None = None,
                     param_objective: str = "calmar",
                     param_max_combos: int = 100) -> dict:
    """Walk-forward 验证。

    当提供 param_grid 时 (P1-6),对每个窗口在 train 段做参数搜索,
    用最优参数在 test 段评估,实现真正 walk-forward 防过拟合。

    param_grid: {"strat_id.path.to.param": [v1, v2, ...], ...}
    param_objective: 优化目标 (calmar/sharpe/return_over_maxdd/total_return/avg_r)
    param_max_combos: 最大搜索组合数
    """
    windows = _month_windows(start, end, train_months, test_months)
    results: list[dict] = []

    # P1-8: 一次性加载全部 quotes,按窗口日期切片(避免重复查库)
    source_store = TradingStore(db_path)
    all_quotes, _ = _load_quotes(source_store, source, symbols,
                                  start=None, end=end, warmup_days=0)
    source_store.close()

    for train_start, train_end, test_start, test_end in windows:
        best_strategies = strategies
        train_params = None

        # P1-6: 在 train 段做参数优化
        if param_grid:
            best_strategies, train_combos = _param_grid_search(
                db_path, strategies, accounts, param_grid,
                train_start, train_end,
                source, symbols, strategy_ids,
                enable_selected, next_bar_execution, apply_costs,
                warmup_days, fx_rates,
                objective=param_objective, max_combinations=param_max_combos,
            )
            # 从最佳策略中提取参数
            train_params = {}
            for path in param_grid:
                val = _deep_get(best_strategies[0], path) if best_strategies else None
                if val is not None:
                    train_params[path] = val

        # 在 test 段用最优参数回测
        test_summary = run_backtest(
            db_path, best_strategies, accounts,
            start=test_start, end=test_end,
            source=source, symbols=symbols,
            strategy_ids=strategy_ids, enable_selected=enable_selected,
            next_bar_execution=next_bar_execution, apply_costs=apply_costs,
            warmup_days=warmup_days, fx_rates=fx_rates,
        )
        total_ret = test_summary.get("total_return_pct")
        usd_ret = (test_summary.get("return_pct_by_currency") or {}).get("USD")
        results.append({
            "train_start": train_start, "train_end": train_end,
            "test_start": test_start, "test_end": test_end,
            "test_total_return_pct": total_ret,
            "test_usd_return_pct": usd_ret,
            "test_max_drawdown_pct": test_summary.get("usd_max_drawdown_pct"),
            "test_fills": test_summary.get("fills"),
            "test_sell_win_rate_pct": test_summary.get("sell_win_rate_pct"),
            "test_sharpe": test_summary.get("sharpe_ratio"),
            "test_avg_r": test_summary.get("avg_r_multiple"),
            "train_best_params": train_params,
        })

    # 汇总
    oos_returns = [r.get("test_total_return_pct") or r.get("test_usd_return_pct")
                   for r in results
                   if (r.get("test_total_return_pct") is not None
                       or r.get("test_usd_return_pct") is not None)]
    positive_windows = sum(1 for r in oos_returns if r > 0) if oos_returns else 0
    summary = {
        "method": ("walk-forward (优化)" if param_grid else "walk-forward (固定参数)"),
        "train_months": train_months,
        "test_months": test_months,
        "start": start, "end": end,
        "param_optimization": param_grid is not None,
        "param_objective": param_objective if param_grid else None,
        "windows": results,
        "window_count": len(results),
        "oos_positive_windows": positive_windows,
        "oos_negative_windows": len(oos_returns) - positive_windows if oos_returns else 0,
        "oos_avg_return_pct": (sum(oos_returns) / len(oos_returns)) if oos_returns else None,
        "oos_min_return_pct": min(oos_returns) if oos_returns else None,
        "oos_max_return_pct": max(oos_returns) if oos_returns else None,
    }
    return summary


def write_walk_forward_report(summary: dict, path: str) -> None:
    output = Path(_resolve_path(path))
    output.parent.mkdir(parents=True, exist_ok=True)
    method_label = summary.get("method", "walk-forward")
    optimized = summary.get("param_optimization", False)
    objective = summary.get("param_objective")
    lines = [
        "# Walk-Forward 验证报告",
        "",
        "## 概述",
        f"- 方法: {method_label} (train {summary['train_months']} 月 / test {summary['test_months']} 月)",
        f"- 全期: {summary['start']} 至 {summary['end']}",
    ]
    if optimized and objective:
        lines.append(f"- 参数优化: 每窗口 train 段搜索最优参数 (目标: {objective})")
    lines.extend([
        f"- 样本外窗口数: {summary['window_count']}",
        f"- 样本外正收益窗口: {summary['oos_positive_windows']} / 负收益: {summary['oos_negative_windows']}",
        f"- 样本外平均收益: {_fmt_pct(summary.get('oos_avg_return_pct'))}",
        f"- 样本外最差窗口: {_fmt_pct(summary.get('oos_min_return_pct'))}",
        f"- 样本外最佳窗口: {_fmt_pct(summary.get('oos_max_return_pct'))}",
        "",
    ])
    if optimized:
        lines.append("> 参数优化模式下,各窗口在 train 段独立搜索最优参数(无未来信息泄露)。")
    else:
        lines.append("> 固定参数模式下,各窗口使用同一套参数;若存在多个负收益窗口,可能过拟合。")
    lines.extend([
        "",
        "## 各窗口明细(样本外 test 段)",
        "| Train 起 | Train 止 | Test 起 | Test 止 | 样本外收益 | 最大回撤 | 成交 | 胜率 | Sharpe | 均R |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|",
    ])
    for w in summary.get("windows", []):
        lines.append(
            f"| {w['train_start']} | {w['train_end']} | {w.get('test_start', '')} | {w['test_end']} | "
            f"{_fmt_pct(w.get('test_total_return_pct') or w.get('test_usd_return_pct'))} | "
            f"{_fmt_pct(w.get('test_max_drawdown_pct'))} | "
            f"{w.get('test_fills', 0)} | {_fmt_pct(w.get('test_sell_win_rate_pct'))} | "
            f"{_fmt_num3(w.get('test_sharpe'))} | {_fmt_num3(w.get('test_avg_r'))} |"
        )
        if w.get("train_best_params"):
            params_str = ", ".join(
                f"{k.split('.')[-1]}={v}" for k, v in w["train_best_params"].items())
            lines.append(f"| > 优化参数: {params_str} ||||||||||")
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
    # P0: 预热 & 汇率
    parser.add_argument("--warmup-days", dest="warmup_days", type=int, default=0,
                        help="加载 start 之前 N 天日线预热(仅 daily-bars,建议 60+)")
    parser.add_argument("--fx-rates", dest="fx_rates", default=None,
                        help="多币种汇率 JSON: {\"USD\":7.2,\"HKD\":0.92,\"CNY\":1.0}")
    # P1: 参数优化 & 敏感性
    parser.add_argument("--param-grid", dest="param_grid", default=None,
                        help="walk-forward 参数搜索网格 JSON")
    parser.add_argument("--param-objective", dest="param_objective", default="calmar",
                        choices=["calmar", "sharpe", "return_over_maxdd", "total_return", "avg_r"],
                        help="参数优化目标(默认 calmar)")
    parser.add_argument("--param-max-combos", dest="param_max_combos", type=int, default=100,
                        help="参数搜索最大组合数(默认 100)")
    parser.add_argument("--sensitivity", dest="sensitivity", action="store_true",
                        help="运行参数敏感性分析")
    parser.add_argument("--sensitivity-params", dest="sensitivity_params", default=None,
                        help="敏感性分析参数路径,逗号分隔")
    parser.add_argument("--validate", dest="validate_only", action="store_true",
                        help="仅校验 daily_bars 数据质量")
    # P2: 成本敏感性 & 基准
    parser.add_argument("--cost-sensitivity", dest="cost_sensitivity", action="store_true",
                        help="运行成本敏感性分析")
    parser.add_argument("--benchmark", dest="benchmark_symbol", default=None,
                        help="基准对比标的(如 QQQ/SPY),计算买入持有收益")
    args = parser.parse_args()

    # Parse fx_rates JSON if provided
    fx_rates: dict[str, float] | None = None
    if args.fx_rates:
        try:
            fx_rates = json.loads(args.fx_rates)
            fx_rates = {k: float(v) for k, v in fx_rates.items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            parser.error("--fx-rates must be valid JSON like {\"USD\":7.2,\"HKD\":0.92,\"CNY\":1.0}")

    app_config = load_app_config(str(CONFIG_DIR / "config.yaml"))
    paper = app_config.get("paper_trading", {}) or {}
    db_path = _resolve_path(args.db_path or paper.get("db_path", "data/trading.sqlite3"))
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    strategies = load_strategies(str(CONFIG_DIR / "strategies.yaml"))

    if args.walk_forward:
        if not args.from_date or not args.to_date:
            parser.error("--walk-forward 需要同时指定 --from 和 --to")
        # Parse param_grid JSON
        param_grid = None
        if args.param_grid:
            try:
                param_grid = json.loads(args.param_grid)
            except (json.JSONDecodeError, ValueError, TypeError):
                parser.error("--param-grid must be valid JSON")
        summary = run_walk_forward(
            db_path, strategies, accounts, args.from_date, args.to_date,
            train_months=args.train_months, test_months=args.test_months,
            source=args.source, symbols=_split_values(args.symbol),
            strategy_ids=_split_values(args.strategy_id),
            enable_selected=args.enable_selected,
            next_bar_execution=args.next_bar, apply_costs=args.apply_costs,
            warmup_days=args.warmup_days, fx_rates=fx_rates,
            param_grid=param_grid,
            param_objective=args.param_objective,
            param_max_combos=args.param_max_combos,
        )
        if args.report:
            write_walk_forward_report(summary, args.report)
            summary["report_path"] = _resolve_path(args.report)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return

    # P1-10: 数据校验模式
    if args.validate_only:
        source_store = TradingStore(db_path)
        rows = source_store.load_daily_bars(symbols=_split_values(args.symbol) or None,
                                             start=args.from_date, end=args.to_date)
        source_store.close()
        warnings = _validate_daily_bars(rows)
        if warnings:
            print("数据质量问题:")
            for w in warnings:
                print(f"  ⚠ {w}")
        else:
            print("✓ 数据质量检查通过")
        return

    # P1-9: 参数敏感性分析
    if args.sensitivity:
        if not args.from_date or not args.to_date:
            parser.error("--sensitivity 需要同时指定 --from 和 --to")
        param_paths = _split_values(args.sensitivity_params.split(",") if args.sensitivity_params else [])
        if not param_paths:
            parser.error("--sensitivity 需要 --sensitivity-params")
        result = run_param_sensitivity(
            db_path, strategies, accounts, args.from_date, args.to_date,
            param_paths=param_paths,
            source=args.source, symbols=_split_values(args.symbol),
            strategy_ids=_split_values(args.strategy_id),
            enable_selected=args.enable_selected,
            next_bar_execution=args.next_bar, apply_costs=args.apply_costs,
            warmup_days=args.warmup_days, fx_rates=fx_rates,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    # P2: 成本敏感性分析
    if args.cost_sensitivity:
        if not args.from_date or not args.to_date:
            parser.error("--cost-sensitivity 需要同时指定 --from 和 --to")
        result = run_cost_sensitivity(
            db_path, strategies, accounts, args.from_date, args.to_date,
            source=args.source, symbols=_split_values(args.symbol),
            strategy_ids=_split_values(args.strategy_id),
            enable_selected=args.enable_selected,
            next_bar_execution=args.next_bar,
            warmup_days=args.warmup_days, fx_rates=fx_rates,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return

    # 普通回测 + 可选基准对比
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
        warmup_days=args.warmup_days,
        fx_rates=fx_rates,
    )
    if args.report:
        write_markdown_report(summary, args.report)
        summary["report_path"] = _resolve_path(args.report)
    if args.trades_csv:
        write_trades_csv(summary, args.trades_csv)
        summary["trades_csv_path"] = _resolve_path(args.trades_csv)
    # P2: 基准指数对比
    if args.benchmark_symbol and args.from_date and args.to_date:
        bench_ret = _benchmark_return(
            db_path, args.benchmark_symbol, args.from_date, args.to_date,
            source=args.source)
        if bench_ret is not None:
            summary["benchmark"] = {
                "symbol": args.benchmark_symbol,
                "buy_hold_return_pct": bench_ret,
            }
            excess = summary["total_return_pct"] - bench_ret
            summary["excess_return_vs_benchmark_pct"] = excess
    printable = dict(summary)
    printable.pop("equity_curve", None)
    printable.pop("trades", None)
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
