from __future__ import annotations


def _rows(store, sql, params=()):
    return [dict(r) for r in store.conn.execute(sql, params).fetchall()]


def counts(store) -> dict:
    return {
        "watchlist": store.watchlist_count(),
        "alerts": store.alert_rule_count(),
        "orders": store.order_count(),
        "fills": store.fill_count(),
        "realized_pnl": store.realized_pnl(),
    }


def account_balances(store) -> list[dict]:
    return _rows(
        store,
        "SELECT currency, cash, reserved_cash, updated_at "
        "FROM account_balances ORDER BY currency",
    )


def positions(store, include_zero: bool = False) -> list[dict]:
    sql = (
        "SELECT market, symbol, name, currency, quantity, avg_cost, realized_pnl, updated_at "
        "FROM positions"
    )
    if not include_zero:
        sql += " WHERE quantity > 0"
    sql += " ORDER BY market, symbol"
    return _rows(store, sql)


def orders(store, limit: int = 200) -> list[dict]:
    return _rows(
        store,
        "SELECT order_id, strategy_id, symbol, market, side, quantity, price, "
        "currency, status, reason, trading_day, created_at "
        "FROM orders ORDER BY created_at DESC LIMIT ?",
        (limit,),
    )


def fills(store, limit: int = 300) -> list[dict]:
    return _rows(
        store,
        "SELECT f.fill_id, f.order_id, o.symbol, o.market, o.side, "
        "f.fill_price, f.quantity, f.amount, f.fee, f.filled_at "
        "FROM fills f JOIN orders o ON o.order_id = f.order_id "
        "ORDER BY f.filled_at DESC LIMIT ?",
        (limit,),
    )


def latest_quotes(store, limit: int = 100) -> list[dict]:
    return _rows(
        store,
        "SELECT q.symbol, q.name, q.market, q.price, q.change_pct, q.volume, "
        "q.snapshot_date, q.timestamp "
        "FROM quote_snapshots q INNER JOIN ("
        "SELECT symbol, MAX(timestamp) AS mx FROM quote_snapshots GROUP BY symbol"
        ") m ON q.symbol = m.symbol AND q.timestamp = m.mx "
        "ORDER BY q.market, q.symbol LIMIT ?",
        (limit,),
    )


def latest_indices(store, limit: int = 50) -> list[dict]:
    return _rows(
        store,
        "SELECT q.symbol, q.name, q.market, q.price, q.change_pct, q.volume, "
        "q.snapshot_date, q.timestamp "
        "FROM index_snapshots q INNER JOIN ("
        "SELECT symbol, MAX(timestamp) AS mx FROM index_snapshots GROUP BY symbol"
        ") m ON q.symbol = m.symbol AND q.timestamp = m.mx "
        "ORDER BY q.market, q.symbol LIMIT ?",
        (limit,),
    )


def signals(store, limit: int = 100) -> list[dict]:
    return _rows(
        store,
        "SELECT signal_id, strategy_id, symbol, market, action, trigger_field, "
        "trigger_op, trigger_value, current_value, quote_price, timestamp "
        "FROM strategy_signals ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    )


def quote_history(store, symbol: str, limit: int = 180) -> list[dict]:
    return _rows(
        store,
        "SELECT symbol, name, market, price, change_pct, volume, "
        "snapshot_date, timestamp "
        "FROM quote_snapshots WHERE symbol = ? "
        "ORDER BY snapshot_date DESC, timestamp DESC LIMIT ?",
        (symbol, limit),
    )
