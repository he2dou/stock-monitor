from src.models import Quote
from src.paper_trading import PaperTradingService
from src.strategy_engine import StrategyEngine
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


def test_cooldown_generates_rejected_order_on_next_trading_day(tmp_path):
    store = make_store(tmp_path)
    service = PaperTradingService(store, StrategyEngine([buy_strategy()]))
    first = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-07-29T14:00:00+08:00")
    next_day = Quote("SOXL", "半导体ETF", "美股", 100, -11, 1000, timestamp="2026-07-30T14:00:00+08:00")
    service.process([first])
    messages = service.process([next_day])
    assert "strategy cooldown" in messages[0].message
    assert store.order_count() == 2


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