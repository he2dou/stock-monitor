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
    # 每个窗口 test_end > train_end
    for ts, te, test_e in windows:
        assert test_e > te


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
