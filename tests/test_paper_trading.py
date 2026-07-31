from src.models import Quote
from src.paper_trading import PaperBroker, PaperTradingService
from src.strategy_engine import StrategyEngine, parse_strategy
from src.trading_models import StrategySignal
from src.trading_store import TradingStore


def make_store(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    store.ensure_accounts({"USD": 50000, "CNY": 100000, "HKD": 100000})
    return store


def buy_strategy(**overrides):
    s = {
        "id": "buy_soxl",
        "enabled": True,
        "symbol": "SOXL",
        "action": "buy",
        "trigger": {"field": "change_pct", "op": "below", "value": -10},
        "sizing": {"type": "fixed_amount", "amount": 1000, "currency": "USD", "lot_size": 1},
        "constraints": {"cooldown_minutes": 300, "max_position_amount": 5000},
    }
    s.update(overrides)
    return s


def test_buy_fills_and_updates_cash_position(tmp_path):
    store = make_store(tmp_path)
    service = PaperTradingService(store, StrategyEngine([buy_strategy()]))
    quote = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000)
    messages = service.process([quote])
    assert len(messages) == 1
    assert "FILLED" in messages[0].message
    assert store.get_balance("USD") == 49000
    pos = store.get_position("美股", "SOXL")
    assert pos["quantity"] == 10
    assert pos["avg_cost"] == 100
    assert store.order_count() == 1
    assert store.fill_count() == 1


def test_buy_rejects_when_cash_insufficient(tmp_path):
    store = TradingStore(str(tmp_path / "trading.sqlite3"))
    store.ensure_accounts({"USD": 100})
    service = PaperTradingService(store, StrategyEngine([buy_strategy()]))
    messages = service.process([Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000)])
    assert "REJECTED" in messages[0].message
    assert "insufficient cash" in messages[0].message
    assert store.fill_count() == 0


def test_lot_size_rounding_for_a_share(tmp_path):
    store = make_store(tmp_path)
    strategy = buy_strategy(
        id="buy_a", symbol="159995",
        sizing={"type": "fixed_amount", "amount": 1000, "currency": "CNY", "lot_size": 100},
        constraints={"cooldown_minutes": 0},
    )
    service = PaperTradingService(store, StrategyEngine([strategy]))
    service.process([Quote("159995", "芯片ETF", "A股", 1.27, -11, 1000)])
    assert store.get_position("A股", "159995")["quantity"] == 700


def test_sell_fills_and_realizes_pnl(tmp_path):
    store = make_store(tmp_path)
    store.upsert_position("美股", "SOXL", "半导体ETF", "USD", 100, 8, 0)
    strategy = buy_strategy(
        id="sell_soxl", action="sell",
        trigger={"field": "price", "op": "above", "value": 10},
        sizing={"type": "fixed_amount", "amount": 500, "currency": "USD", "lot_size": 1},
        constraints={"cooldown_minutes": 0},
    )
    service = PaperTradingService(store, StrategyEngine([strategy]))
    service.process([Quote("SOXL", "半导体ETF", "美股", 10, 1, 1000)])
    pos = store.get_position("美股", "SOXL")
    assert pos["quantity"] == 50
    assert pos["realized_pnl"] == 100
    assert store.get_balance("USD") == 50500


def test_sell_rejects_when_position_insufficient(tmp_path):
    store = make_store(tmp_path)
    strategy = buy_strategy(
        id="sell_soxl", action="sell",
        trigger={"field": "price", "op": "above", "value": 10},
        sizing={"type": "fixed_amount", "amount": 500, "currency": "USD", "lot_size": 1},
    )
    service = PaperTradingService(store, StrategyEngine([strategy]))
    messages = service.process([Quote("SOXL", "半导体ETF", "美股", 10, 1, 1000)])
    assert "insufficient position quantity" in messages[0].message
    assert store.fill_count() == 0


def test_cooldown_allows_order_on_next_trading_day(tmp_path):
    store = make_store(tmp_path)
    service = PaperTradingService(store, StrategyEngine([buy_strategy()]))
    first = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-07-29T14:00:00+08:00")
    next_day = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-07-30T14:00:00+08:00")
    service.process([first])
    messages = service.process([next_day])
    assert "FILLED" in messages[0].message
    assert store.order_count() == 2
    assert store.fill_count() == 2


def test_same_signal_only_creates_one_order_per_trading_day(tmp_path):
    store = make_store(tmp_path)
    service = PaperTradingService(store, StrategyEngine([buy_strategy(constraints={"cooldown_minutes": 0})]))
    quote = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-07-29T14:00:00+08:00")

    first = service.process([quote])
    duplicate = service.process([quote])

    assert "FILLED" in first[0].message
    assert "daily signal already ordered" in duplicate[0].message
    assert store.order_count() == 1
    assert store.fill_count() == 1
    assert store.get_position("美股", "SOXL")["quantity"] == 10


def test_us_signal_after_midnight_beijing_uses_previous_trading_day(tmp_path):
    store = make_store(tmp_path)
    service = PaperTradingService(store, StrategyEngine([buy_strategy(constraints={"cooldown_minutes": 0})]))
    evening = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-07-29T22:00:00+08:00")
    after_midnight = Quote("SOXL", "半导体ETF", "美股", 101, -12, 1000, timestamp="2026-07-30T02:00:00+08:00")

    service.process([evening])
    duplicate = service.process([after_midnight])

    assert "daily signal already ordered for 2026-07-29" in duplicate[0].message
    assert store.order_count() == 1

def test_risk_percent_buy_sizing_uses_signal_stop_price(tmp_path):
    store = make_store(tmp_path)
    strategy = parse_strategy(buy_strategy(
        id="risk_buy_soxl",
        sizing={"type": "risk_percent", "amount": 1.0, "currency": "USD", "lot_size": 1},
        constraints={"cooldown_minutes": 0, "max_position_amount": 20000},
    ))
    signal = StrategySignal(
        strategy=strategy,
        symbol="SOXL",
        market="美股",
        name="半导体ETF",
        action="buy",
        trigger_field="price",
        trigger_op="leveraged_breakout_pullback_confirmed",
        trigger_value=13.0,
        current_value=13.3,
        quote_price=13.3,
        quote_timestamp="2026-07-29T22:00:00+08:00",
        metadata={"stop_price": 11.96},
    )

    execution = PaperBroker(store).execute(signal)

    assert execution.status == "FILLED"
    assert execution.quantity == 373
    assert round(execution.amount, 2) == 4960.9
    assert store.get_position("美股", "SOXL")["quantity"] == 373


def test_partial_sell_uses_position_fraction_quantity(tmp_path):
    store = make_store(tmp_path)
    store.upsert_position("美股", "SOXL", "半导体ETF", "USD", 101, 10.0, 0.0)
    strategy = parse_strategy(buy_strategy(
        id="partial_sell_soxl",
        action="sell",
        trigger={"field": "price", "op": "above", "value": 18.0},
        sizing={"type": "fixed_amount", "amount": 100000, "currency": "USD", "lot_size": 1},
        constraints={"cooldown_minutes": 0},
    ))
    signal = StrategySignal(
        strategy=strategy,
        symbol="SOXL",
        market="美股",
        name="半导体ETF",
        action="sell",
        trigger_field="price",
        trigger_op="partial_take_profit",
        trigger_value=18.0,
        current_value=18.0,
        quote_price=18.0,
        quote_timestamp="2026-07-29T22:00:00+08:00",
        metadata={"position_fraction": 0.5, "exit_kind": "partial"},
    )

    execution = PaperBroker(store).execute(signal)

    assert execution.status == "FILLED"
    assert execution.quantity == 50
    assert store.get_position("美股", "SOXL")["quantity"] == 51


def test_daily_signal_key_allows_partial_and_final_exit_same_day(tmp_path):
    store = make_store(tmp_path)
    store.upsert_position("美股", "SOXL", "半导体ETF", "USD", 100, 10.0, 0.0)
    strategy = parse_strategy(buy_strategy(
        id="exit_soxl",
        action="sell",
        trigger={"field": "price", "op": "above", "value": 18.0},
        sizing={"type": "fixed_amount", "amount": 100000, "currency": "USD", "lot_size": 1},
        constraints={"cooldown_minutes": 0},
    ))
    partial = StrategySignal(
        strategy=strategy, symbol="SOXL", market="美股", name="半导体ETF", action="sell",
        trigger_field="price", trigger_op="partial_take_profit", trigger_value=18.0,
        current_value=18.0, quote_price=18.0, quote_timestamp="2026-07-29T22:00:00+08:00",
        metadata={"position_fraction": 0.5, "exit_kind": "partial"},
    )
    final = StrategySignal(
        strategy=strategy, symbol="SOXL", market="美股", name="半导体ETF", action="sell",
        trigger_field="price", trigger_op="trailing_or_initial_stop", trigger_value=15.8,
        current_value=15.5, quote_price=15.5, quote_timestamp="2026-07-29T23:00:00+08:00",
        metadata={"position_fraction": 1.0, "exit_kind": "final"},
    )

    assert PaperBroker(store).execute(partial).status == "FILLED"
    assert PaperBroker(store).execute(final).status == "FILLED"
    assert store.order_count() == 2
    assert store.fill_count() == 2
    assert store.get_position("美股", "SOXL")["quantity"] == 0
