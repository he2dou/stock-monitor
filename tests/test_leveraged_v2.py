"""Phase 2/3/7/8 新行为测试: 仓位 sizing、回测真实性、风险指标、walk-forward。"""
import math

import pytest

from src.backtest import (
    _compute_risk_metrics,
    _month_windows,
    run_backtest,
)
from src.models import Quote
from src.paper_trading import PaperBroker
from src.strategy_engine import StrategyEngine, parse_strategy
from src.trading_models import TradingCosts
from src.trading_store import TradingStore


# ---------------------------------------------------------------------------
# Phase 2: 仓位 sizing (equity basis + min floor)
# ---------------------------------------------------------------------------

def _leveraged_raw(**sizing_overrides):
    sizing = {"type": "risk_percent", "amount": 2.0, "currency": "USD", "lot_size": 1}
    sizing.update(sizing_overrides)
    return {
        "id": "s", "type": "leveraged_breakout_pullback", "enabled": True,
        "symbol": "SOXL", "action": "buy",
        "leveraged_breakout_pullback": {
            "lookback_bars": 3, "trend_short_bars": 2, "trend_long_bars": 4,
        },
        "sizing": sizing,
        "constraints": {"cooldown_minutes": 0, "max_position_amount": 1000000},
    }


def _drive_entry(engine):
    for p in [10, 12, 14, 16, 17, 16.2]:
        engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", p, 0, 1000)])
    return engine.generate_signals([Quote("SOXL", "半导体ETF", "美股", 17.0, 0, 1000)])[0]


def test_equity_basis_sizes_on_total_equity_not_cash():
    """equity 基数 = 现金 + 持仓市值,有持仓时仓位应大于 cash 基数。"""
    strat_raw = _leveraged_raw(equity_basis="equity")
    engine = StrategyEngine([strat_raw])
    store = TradingStore(":memory:")
    store.ensure_accounts({"USD": 50000})
    # 预置一个价值 30000 的持仓(1764 股 @ 17)
    store.upsert_position("美股", "SOXL", "半导体", "USD", 1764, 17.0, 0.0)
    store.update_balance("USD", 50000 - 1764 * 17.0)
    broker = PaperBroker(store)
    sig = _drive_entry(engine)
    qty_equity = broker._quantity_from_signal(sig, "USD", 1)

    # cash 基数对比
    strat_raw2 = _leveraged_raw(equity_basis="cash")
    engine2 = StrategyEngine([strat_raw2])
    store2 = TradingStore(":memory:")
    store2.ensure_accounts({"USD": 50000})
    store2.upsert_position("美股", "SOXL", "半导体", "USD", 1764, 17.0, 0.0)
    store2.update_balance("USD", 50000 - 1764 * 17.0)
    broker2 = PaperBroker(store2)
    sig2 = _drive_entry(engine2)
    qty_cash = broker2._quantity_from_signal(sig2, "USD", 1)

    assert qty_equity > qty_cash


def test_min_position_value_pct_raises_floor():
    """min_position_value_pct 应让宽止损时仓位不低于下限。"""
    # 极宽止损(risk_per_share 很大) -> risk_quantity 很小
    strat_raw = _leveraged_raw(equity_basis="equity", min_position_value_pct=5.0)
    engine = StrategyEngine([strat_raw])
    store = TradingStore(":memory:")
    store.ensure_accounts({"USD": 50000})
    broker = PaperBroker(store)
    sig = _drive_entry(engine)
    qty = broker._quantity_from_signal(sig, "USD", 1)
    # 下限 = 50000 * 5% / 17 ≈ 147 股
    assert qty >= 147


# ---------------------------------------------------------------------------
# Phase 3: 成本(佣金/滑点) + 次根成交
# ---------------------------------------------------------------------------

def test_costs_apply_slippage_and_commission_to_buy():
    costs = TradingCosts(commission_bps=10.0, slippage_bps=20.0)
    store = TradingStore(":memory:")
    store.ensure_accounts({"USD": 100000})
    broker = PaperBroker(store, costs=costs)
    strat = parse_strategy({
        "id": "t", "enabled": True, "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    })
    from src.trading_models import StrategySignal
    sig = StrategySignal(
        strategy=strat, symbol="SOXL", market="美股", name="半导体", action="buy",
        trigger_field="price", trigger_op="above", trigger_value=100,
        current_value=100, quote_price=100.0,
        quote_timestamp="2024-01-01T00:00:00+00:00", metadata={})
    exe = broker.execute(sig)
    assert exe.price == pytest.approx(100.2)  # 100 * (1 + 0.002)
    assert exe.commission == pytest.approx(1.002, rel=1e-3)
    assert exe.slippage == pytest.approx(0.2, rel=1e-3)
    # 现金扣除 = amount + commission
    assert store.get_balance("USD") == pytest.approx(100000 - 1002.0 - 1.002, rel=1e-3)


def test_no_costs_when_costs_none():
    store = TradingStore(":memory:")
    store.ensure_accounts({"USD": 100000})
    broker = PaperBroker(store)  # costs=None
    assert broker._fill_price.__self__ is not None  # 方法存在
    from src.trading_models import StrategySignal
    strat = parse_strategy({
        "id": "t", "enabled": True, "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    })
    sig = StrategySignal(
        strategy=strat, symbol="SOXL", market="美股", name="半导体", action="buy",
        trigger_field="price", trigger_op="above", trigger_value=100,
        current_value=100, quote_price=100.0,
        quote_timestamp="2024-01-01T00:00:00+00:00", metadata={})
    exe = broker.execute(sig)
    assert exe.price == 100.0
    assert exe.commission == 0.0


def test_next_bar_execution_delays_entry_to_next_bar(tmp_path):
    """次根成交: 入场信号在下一根 bar 的价格成交。"""
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    store.ensure_accounts({"USD": 50000})
    # 与 drive_lbp_entry 一致的价格序列,确保能产生入场信号
    prices = [10, 12, 14, 16, 17, 16.2, 17.0, 18.0]
    for i, p in enumerate(prices):
        store.save_quote_snapshots([Quote("SOXL", "半导体", "美股", p, 0, 1000,
                                          timestamp=f"2024-01-{i+1:02d}T22:00:00+08:00")])
    store.close()
    strategies = [_leveraged_raw()]
    s_same = run_backtest(str(db), strategies, {"USD": 50000},
                          source="quote-snapshots", symbols=["SOXL"],
                          strategy_ids=["s"], enable_selected=True,
                          next_bar_execution=False)
    s_next = run_backtest(str(db), strategies, {"USD": 50000},
                          source="quote-snapshots", symbols=["SOXL"],
                          strategy_ids=["s"], enable_selected=True,
                          next_bar_execution=True)
    # 两种模式都应产生交易
    assert s_same["fills"] >= 1
    assert s_next["fills"] >= 1
    # next-bar 模式标记
    assert s_next["next_bar_execution"] is True
    assert s_same["next_bar_execution"] is False
    # 次根成交的首次买入日期应晚于等于 same-bar(延迟一拍)
    same_buy_dates = [t["date"] for t in s_same["trades"] if t["side"] == "buy" and t["status"] == "FILLED"]
    next_buy_dates = [t["date"] for t in s_next["trades"] if t["side"] == "buy" and t["status"] == "FILLED"]
    assert same_buy_dates and next_buy_dates
    assert next_buy_dates[0] >= same_buy_dates[0]


# ---------------------------------------------------------------------------
# Phase 7: 风险指标
# ---------------------------------------------------------------------------

def test_compute_risk_metrics_sharpe_and_profit_factor():
    equity_curve = [
        {"date": "2024-01-01", "total_equity": 100000, "equity_by_currency": {"USD": 100000}},
        {"date": "2024-01-02", "total_equity": 101000, "equity_by_currency": {"USD": 101000}},
        {"date": "2024-01-03", "total_equity": 100500, "equity_by_currency": {"USD": 100500}},
        {"date": "2024-01-04", "total_equity": 102000, "equity_by_currency": {"USD": 102000}},
    ]
    trades = [
        {"status": "FILLED", "side": "buy", "symbol": "SOXL", "quantity": 100,
         "risk_per_share": 2.0},
        {"status": "FILLED", "side": "sell", "symbol": "SOXL", "quantity": 100,
         "realized_pnl": 600.0, "trigger": "trailing_or_initial_stop"},
    ]
    m = _compute_risk_metrics(equity_curve, trades, "daily-bars", 100000, 102000)
    assert m["sharpe_ratio"] is not None
    assert m["profit_factor"] is None  # 无亏损 -> None
    assert m["avg_r_multiple"] == pytest.approx(3.0, rel=1e-3)  # 600 / (2.0*100)


def test_buy_hold_fraction_returns_half():
    quotes = [Quote("SOXL", "x", "美股", 100, 0, 1, timestamp=f"2024-01-0{i}T00:00:00+00:00") for i in range(1, 4)]
    quotes.append(Quote("SOXL", "x", "美股", 200, 0, 1, timestamp="2024-01-04T00:00:00+00:00"))
    from src.backtest import _buy_hold_return
    full = _buy_hold_return(quotes, "SOXL")
    half = _buy_hold_return(quotes, "SOXL", fraction=0.5)
    assert full == pytest.approx(100.0)
    assert half == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Phase 8: walk-forward
# ---------------------------------------------------------------------------

def test_month_windows_generates_rolling_periods():
    windows = _month_windows("2023-01-01", "2024-06-30", train_months=6, test_months=3)
    assert len(windows) >= 2
    # 每个窗口 test_end > train_end, test_start > train_end
    for ts, te, test_s, test_e in windows:
        assert test_e > te
        assert test_s > te  # P1-8: test 从 train_end+1 开始


def test_walk_forward_runs_on_real_data():
    strategies = [_leveraged_raw()]
    summary = run_backtest.__wrapped__ if hasattr(run_backtest, "__wrapped__") else None
    from src.backtest import run_walk_forward
    wf = run_walk_forward("data/trading.sqlite3", strategies, {"USD": 50000},
                          "2023-07-31", "2025-07-30", train_months=12, test_months=6,
                          source="daily-bars", symbols=["SOXL"],
                          strategy_ids=["s"], enable_selected=True)
    assert wf["window_count"] >= 1
    assert "oos_avg_return_pct" in wf
    assert wf["oos_positive_windows"] + wf["oos_negative_windows"] == wf["window_count"]


# ---------------------------------------------------------------------------
# P0-1: Intrabar OHLC execution (daily-bars stop / target at low/high)
# ---------------------------------------------------------------------------

def test_intrabar_flag_and_ohlc_on_daily_bars(tmp_path):
    """daily-bars 回测启用 OHLC intrabar,Quote 应包含 open/high/low。"""
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    store.save_daily_bars([{
        "symbol": "SOXL", "name": "半导体", "market": "美股", "date": "2024-01-01",
        "open": 10, "high": 11, "low": 9, "close": 10, "adj_close": 10,
        "volume": 1000, "source": "test",
    }])
    store.close()
    strategies = [{
        "id": "buy_soxl", "enabled": False,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        start="2024-01-01", end="2024-01-01", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["buy_soxl"], enable_selected=True,
    )
    assert summary["intrabar_execution"] is True
    # 验证 _intrabar_fill_price 对 OHLC 数据正确处理(单元测试已覆盖逻辑)


def test_intrabar_partial_take_profit_executes_at_target():
    """_intrabar_fill_price 单元测试: 验证 stop/target 盘中触发逻辑。"""
    from src.backtest import _intrabar_fill_price
    quote = Quote("SOXL", "半导体", "美股", 18, 0, 1000,
                  timestamp="2024-01-08T22:00:00+08:00",
                  open=17, high=30, low=16)
    # 部分止盈目标价 25, high=30 远超目标 → 应触发
    fill = _intrabar_fill_price("sell", "partial_take_profit", 25.0, quote)
    assert fill == 25.0
    # 止损: trigger_value=15, low=16 > 15 → 不应盘中触发
    assert _intrabar_fill_price("sell", "trailing_or_initial_stop", 15.0, quote) is None
    # 止损: trigger_value=17, low=16 <= 17 → 应触发,fill at max(16,17)=17
    assert _intrabar_fill_price("sell", "trailing_or_initial_stop", 17.0, quote) == 17.0
    # 非 OHLC 数据: 无 open/high/low → 返回 None
    quote_no_ohlc = Quote("SOXL", "半导体", "美股", 18, 0, 1000)
    assert _intrabar_fill_price("sell", "trailing_or_initial_stop", 17.0, quote_no_ohlc) is None


def test_adj_close_used_in_daily_bars_backtest(tmp_path):
    """daily-bars 回测应使用 adj_close 而非 raw close。"""
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    # raw close=10, adj_close=12 (复权后)
    store.save_daily_bars([{
        "symbol": "SOXL", "name": "半导体", "market": "美股", "date": "2024-01-01",
        "open": 10, "high": 11, "low": 9, "close": 10, "adj_close": 12,
        "volume": 1000, "source": "test",
    }])
    store.close()

    from src.backtest import _load_quotes
    store2 = TradingStore(str(db))
    quotes, _ = _load_quotes(store2, "daily-bars", ["SOXL"], None, None)
    store2.close()
    assert len(quotes) == 1
    assert quotes[0].price == 12.0  # adj_close, not raw close
    assert quotes[0].open == 10.0
    assert quotes[0].high == 11.0
    assert quotes[0].low == 9.0


# ---------------------------------------------------------------------------
# P0-5: warmup bars
# ---------------------------------------------------------------------------

def test_warmup_bars_are_fed_to_engine_but_not_traded(tmp_path):
    """预热 bar 只用来积累指标历史,不产生交易,不计入权益。"""
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    # 60 根预热 + 2 根活跃
    import datetime as _dt
    base = _dt.date.fromisoformat("2024-01-01")
    for i in range(65):
        d = (base + _dt.timedelta(days=i)).isoformat()
        p = 10 + i * 0.1  # 缓慢上升
        store.save_daily_bars([{
            "symbol": "SOXL", "name": "半导体", "market": "美股", "date": d,
            "open": p, "high": p + 0.5, "low": p - 0.5, "close": p,
            "adj_close": p, "volume": 1000, "source": "test",
        }])
    store.close()

    strategies = [_leveraged_raw()]
    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        start="2024-02-20", end="2024-03-05", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["s"], enable_selected=True,
        warmup_days=60,
    )
    assert summary["warmup_bars_used"] > 0
    # 活跃 bar 数量正确
    assert summary["quotes_replayed"] >= 1


# ---------------------------------------------------------------------------
# P0-4: multi-currency FX rate conversion
# ---------------------------------------------------------------------------

def test_fx_rates_convert_equity_to_base_currency(tmp_path):
    """提供 fx_rates 时,total_equity 应按汇率折算。"""
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    store.save_daily_bars([{
        "symbol": "SOXL", "name": "半导体", "market": "美股", "date": "2024-01-01",
        "open": 10, "high": 10, "low": 10, "close": 10, "adj_close": 10,
        "volume": 1000, "source": "test",
    }])
    store.close()

    strategies = [{
        "id": "buy_soxl", "enabled": False,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    # 无汇率: USD + CNY 直接相加 = 50000 + 100000 = 150000
    s_raw = run_backtest(
        str(db), strategies, {"USD": 50000, "CNY": 100000},
        start="2024-01-01", end="2024-01-01", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["buy_soxl"], enable_selected=True,
    )
    assert s_raw["starting_equity"] == 150000.0

    # 有汇率: USD→CNY 7.2, CNY=1
    s_fx = run_backtest(
        str(db), strategies, {"USD": 50000, "CNY": 100000},
        start="2024-01-01", end="2024-01-01", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["buy_soxl"], enable_selected=True,
        fx_rates={"USD": 7.2, "CNY": 1.0},
    )
    # 50000*7.2 + 100000*1 = 360000 + 100000 = 460000
    assert s_fx["starting_equity"] == 460000.0


# ---------------------------------------------------------------------------
# P1-10: 数据健康检查
# ---------------------------------------------------------------------------

def test_validate_daily_bars_detects_duplicates():
    from src.backtest import _validate_daily_bars
    rows = [
        {"symbol": "SOXL", "date": "2024-01-01", "close": 10, "adj_close": 10,
         "open": 10, "high": 10, "low": 10, "volume": 1000, "name": "x", "market": "美股", "source": "t"},
        {"symbol": "SOXL", "date": "2024-01-01", "close": 10, "adj_close": 10,
         "open": 10, "high": 10, "low": 10, "volume": 1000, "name": "x", "market": "美股", "source": "t"},
    ]
    warnings = _validate_daily_bars(rows)
    assert any("重复日期" in w for w in warnings)


def test_validate_daily_bars_detects_price_jump():
    from src.backtest import _validate_daily_bars
    rows = [
        {"symbol": "SOXL", "date": "2024-01-01", "close": 10, "adj_close": 10,
         "open": 10, "high": 10, "low": 10, "volume": 1000, "name": "x", "market": "美股", "source": "t"},
        {"symbol": "SOXL", "date": "2024-01-02", "close": 20, "adj_close": 20,
         "open": 20, "high": 20, "low": 20, "volume": 1000, "name": "x", "market": "美股", "source": "t"},
    ]
    warnings = _validate_daily_bars(rows)
    assert any("跳变" in w for w in warnings)


def test_validate_daily_bars_passes_clean_data():
    from src.backtest import _validate_daily_bars
    rows = [
        {"symbol": "SOXL", "date": f"2024-01-{i+1:02d}", "close": 10 + i * 0.5,
         "adj_close": 10 + i * 0.5,
         "open": 10 + i * 0.5, "high": 10 + i * 0.5, "low": 10 + i * 0.5,
         "volume": 1000, "name": "x", "market": "美股", "source": "t"}
        for i in range(30)
    ]
    warnings = _validate_daily_bars(rows)
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# P1-9: 参数敏感性分析
# ---------------------------------------------------------------------------

def test_param_sensitivity_runs_on_daily_bars(tmp_path):
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    for i in range(30):
        store.save_daily_bars([{
            "symbol": "SOXL", "name": "SOXL", "market": "美股",
            "date": f"2024-01-{i+1:02d}",
            "open": 10 + i, "high": 11 + i, "low": 9 + i, "close": 10 + i,
            "adj_close": 10 + i, "volume": 1000, "source": "test",
        }])
    store.close()

    from src.backtest import run_param_sensitivity
    strategies = [{
        "id": "buy_soxl", "enabled": True,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    result = run_param_sensitivity(
        str(db), strategies, {"USD": 50000},
        "2024-01-01", "2024-01-20",
        param_paths=["buy_soxl.trigger.value"],
        perturbations=[-0.5, 0.5],
        source="daily-bars", symbols=["SOXL"],
        strategy_ids=["buy_soxl"], enable_selected=True,
    )
    assert "baseline" in result
    assert "parameters" in result
    assert "buy_soxl.trigger.value" in result["parameters"]
    assert len(result["parameters"]["buy_soxl.trigger.value"]["results"]) == 2


# ---------------------------------------------------------------------------
# P1-6/8: walk-forward with param optimization & window fix
# ---------------------------------------------------------------------------

def test_walk_forward_with_param_grid_runs(tmp_path):
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    for i in range(60):
        store.save_daily_bars([{
            "symbol": "SOXL", "name": "SOXL", "market": "美股",
            "date": f"2024-01-{i+1:02d}",
            "open": 10 + i * 0.2, "high": 11 + i * 0.2, "low": 9 + i * 0.2,
            "close": 10 + i * 0.2, "adj_close": 10 + i * 0.2,
            "volume": 1000, "source": "test",
        }])
    store.close()

    from src.backtest import run_walk_forward
    strategies = [{
        "id": "buy_soxl", "enabled": False,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    param_grid = {
        "buy_soxl.trigger.value": [-5, -10, -15],
    }
    wf = run_walk_forward(
        str(db), strategies, {"USD": 50000},
        "2024-01-01", "2024-02-10", train_months=1, test_months=1,
        source="daily-bars", symbols=["SOXL"],
        strategy_ids=["buy_soxl"], enable_selected=True,
        param_grid=param_grid, param_objective="sharpe", param_max_combos=5,
    )
    assert wf["param_optimization"] is True
    assert wf["window_count"] >= 1
    assert "oos_avg_return_pct" in wf
    # 验证每窗口有 train_best_params
    for w in wf["windows"]:
        assert w.get("train_best_params") is not None


# ---------------------------------------------------------------------------
# P2: 成本敏感性
# ---------------------------------------------------------------------------

def test_cost_sensitivity_runs(tmp_path):
    from src.backtest import run_cost_sensitivity
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    for i in range(30):
        store.save_daily_bars([{
            "symbol": "SOXL", "name": "SOXL", "market": "美股",
            "date": f"2024-01-{i+1:02d}",
            "open": 10 + i, "high": 11 + i, "low": 9 + i, "close": 10 + i,
            "adj_close": 10 + i, "volume": 1000, "source": "test",
        }])
    store.close()
    strategies = [{
        "id": "buy_soxl", "enabled": True,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
        "costs": {"commission_bps": 5.0, "slippage_bps": 2.0},
    }]
    result = run_cost_sensitivity(
        str(db), strategies, {"USD": 50000},
        "2024-01-01", "2024-01-20",
        source="daily-bars", symbols=["SOXL"],
        strategy_ids=["buy_soxl"], enable_selected=True,
    )
    assert "results" in result
    assert len(result["results"]) == 5  # 0, 1, 2, 3, 5x
    # 成本越高收益越低
    returns = [r["total_return_pct"] for r in result["results"]]
    assert returns[0] >= returns[-1]  # 0x >= 5x


# ---------------------------------------------------------------------------
# P2: 交易级统计
# ---------------------------------------------------------------------------

def test_backtest_includes_trade_stats(tmp_path):
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    store.save_daily_bars([
        {"symbol": "SOXL", "name": "SOXL", "market": "美股", "date": "2024-01-01",
         "open": 10, "high": 10, "low": 10, "close": 10, "adj_close": 10,
         "volume": 1000, "source": "test"},
    ])
    store.close()
    strategies = [{
        "id": "buy_soxl", "enabled": False,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 0},
    }]
    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        start="2024-01-01", end="2024-01-01", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["buy_soxl"], enable_selected=True,
    )
    assert "trade_stats" in summary
    assert "monthly_returns" in summary
    assert isinstance(summary["trade_stats"], dict)
    assert "filled_buys" in summary["trade_stats"]
    assert "trades_per_year" in summary["trade_stats"]


def test_backtest_warns_on_low_sample(tmp_path):
    """卖出成交 < 30 笔时应包含警告。"""
    db = tmp_path / "t.sqlite3"
    store = TradingStore(str(db))
    # 价格从 10→8 (跌 20%),触发买入; 后续持平,无卖出
    prices = [10, 8, 8, 8, 8]
    for i, p in enumerate(prices):
        store.save_daily_bars([{
            "symbol": "SOXL", "name": "SOXL", "market": "美股",
            "date": f"2024-01-{i+1:02d}",
            "open": p, "high": p, "low": p, "close": p, "adj_close": p,
            "volume": 1000, "source": "test",
        }])
    store.close()
    strategies = [{
        "id": "buy_soxl", "enabled": True,
        "symbol": "SOXL", "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 100000},
    }]
    summary = run_backtest(
        str(db), strategies, {"USD": 50000},
        start="2024-01-01", end="2024-01-05", source="daily-bars",
        symbols=["SOXL"], strategy_ids=["buy_soxl"], enable_selected=True,
    )
    # 有买入但没卖出,fill >= 1 确认触发成功;<=30 sells 应有警告
    assert summary["fills"] >= 1, f"Expected at least 1 fill, got {summary['fills']}"
    warnings = summary.get("warnings", [])
    assert len(warnings) >= 1, f"Expected low-sample warning, got warnings={warnings}"


def test_monthly_returns_computes_from_equity_curve():
    from src.backtest import _monthly_returns
    equity_curve = [
        {"date": "2024-01-01", "total_equity": 100000},
        {"date": "2024-01-15", "total_equity": 101000},
        {"date": "2024-01-31", "total_equity": 102000},
        {"date": "2024-02-01", "total_equity": 102000},
        {"date": "2024-02-28", "total_equity": 99000},
    ]
    mr = _monthly_returns(equity_curve)
    assert len(mr) == 2
    assert mr[0]["return_pct"] == pytest.approx(2.0)
    assert mr[1]["return_pct"] == pytest.approx(-2.94, rel=0.1)


def test_bootstrap_confidence_intervals():
    from src.backtest import _bootstrap_confidence
    # 正收益序列,CI 应都为正
    returns = [0.001] * 50 + [-0.0005] * 20 + [0.002] * 30
    result = _bootstrap_confidence(returns, n_iter=200, ci=0.95)
    assert result["bootstrap_iterations"] == 200
    assert result["mean_return_pct_ci_low"] < result["mean_return_pct_ci_high"]
    assert result["sharpe_ci_low"] < result["sharpe_ci_high"]
