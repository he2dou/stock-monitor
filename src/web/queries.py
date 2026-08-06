from __future__ import annotations


def _rows(store, sql, params=()):
    return [dict(r) for r in store.conn.execute(sql, params).fetchall()]


def counts(store) -> dict:
    return {
        "watchlist": store.watchlist_count(),
        "alerts": store.alert_rule_count(),
        "orders": store.order_count(),
        "fills": store.fill_count(),
        "unrealized_pnl": _unrealized_pnl(store),
    }


def _unrealized_pnl(store) -> float:
    sql = (
        "SELECT COALESCE(SUM((q.price - p.avg_cost) * p.quantity), 0) AS pnl " 
        "FROM positions p " 
        "INNER JOIN (SELECT symbol, price, change_pct FROM quote_snapshots qs " 
        "  INNER JOIN (SELECT symbol AS s2, MAX(timestamp) AS mx FROM quote_snapshots GROUP BY symbol) m " 
        "  ON qs.symbol = m.s2 AND qs.timestamp = m.mx) q ON q.symbol = p.symbol " 
        "WHERE p.quantity > 0"
    )
    row = store.conn.execute(sql).fetchone()
    return float(row["pnl"]) if row else 0.0


def account_balances(store) -> list[dict]:
    rows = _rows(
        store,
        "SELECT currency, cash, initial_cash, reserved_cash, updated_at "
        "FROM account_balances ORDER BY currency",
    )
    position_rows = positions(store)
    values_by_currency: dict[str, dict[str, float]] = {}
    for position in position_rows:
        currency = position["currency"]
        values = values_by_currency.setdefault(currency, {"market_value": 0.0, "unrealized_pnl": 0.0})
        if position.get("current_price") is not None:
            values["market_value"] += position["current_price"] * position["quantity"]
        if position.get("unrealized_pnl") is not None:
            values["unrealized_pnl"] += position["unrealized_pnl"]
    for row in rows:
        values = values_by_currency.get(row["currency"], {"market_value": 0.0, "unrealized_pnl": 0.0})
        row["available"] = row["cash"] - row["reserved_cash"]
        row["market_value"] = values["market_value"]
        row["unrealized_pnl"] = values["unrealized_pnl"]
        row["total_assets"] = row["cash"] + row["market_value"]
    return rows

def positions(store, include_zero: bool = False) -> list[dict]:
    sql = (
        "SELECT p.market, p.symbol, p.name, p.currency, p.quantity, " 
        "p.avg_cost, p.realized_pnl, p.updated_at, " 
        "q.price AS current_price, q.change_pct AS change_pct " 
        "FROM positions p " 
        "LEFT JOIN (" 
        "  SELECT symbol, price, change_pct FROM quote_snapshots qs " 
        "  INNER JOIN (" 
        "    SELECT symbol AS s2, MAX(timestamp) AS mx " 
        "    FROM quote_snapshots GROUP BY symbol" 
        "  ) m ON qs.symbol = m.s2 AND qs.timestamp = m.mx " 
        ") q ON q.symbol = p.symbol"
    )
    if not include_zero:
        sql += " WHERE p.quantity > 0"
    sql += " ORDER BY p.market, p.symbol"
    rows = _rows(store, sql)
    # Compute unrealized PnL from current price
    for r in rows:
        cp = r.get("current_price")
        if cp and r["quantity"] and r["avg_cost"]:
            r["unrealized_pnl"] = round((cp - r["avg_cost"]) * r["quantity"], 2)
            r["pnl_pct"] = round((cp / r["avg_cost"] - 1) * 100, 2) if r["avg_cost"] else 0.0
        else:
            r["unrealized_pnl"] = None
            r["pnl_pct"] = None
    return rows


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
