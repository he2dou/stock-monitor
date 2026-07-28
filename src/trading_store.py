from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from datetime import datetime, timezone
from src.models import Quote


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradingStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.initialize_schema()

    def close(self) -> None:
        self.conn.close()

    def initialize_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                price REAL NOT NULL,
                change_pct REAL NOT NULL,
                volume REAL NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_signals (
                signal_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                action TEXT NOT NULL,
                trigger_field TEXT NOT NULL,
                trigger_value REAL NOT NULL,
                current_value REAL NOT NULL,
                quote_price REAL NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                signal_id TEXT,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                currency TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS fills (
                fill_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                fill_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                amount REAL NOT NULL,
                fee REAL NOT NULL,
                filled_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                currency TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                avg_cost REAL NOT NULL,
                realized_pnl REAL NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, symbol)
            );

            CREATE TABLE IF NOT EXISTS account_balances (
                currency TEXT PRIMARY KEY,
                cash REAL NOT NULL,
                reserved_cash REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def ensure_accounts(self, accounts: dict[str, float]) -> None:
        now = utc_now()
        with self.conn:
            for currency, cash in accounts.items():
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO account_balances
                    (currency, cash, reserved_cash, updated_at)
                    VALUES (?, ?, 0, ?)
                    """,
                    (currency, float(cash), now),
                )

    def save_quote_snapshots(self, quotes: list[Quote]) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO quote_snapshots
                (symbol, name, market, price, change_pct, volume, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (q.symbol, q.name, q.market, q.price, q.change_pct, q.volume, q.timestamp)
                    for q in quotes
                ],
            )

    def load_quote_snapshots(self, start: str | None = None, end: str | None = None) -> list[Quote]:
        sql = "SELECT symbol, name, market, price, change_pct, volume, timestamp FROM quote_snapshots"
        params: list[str] = []
        clauses: list[str] = []
        if start:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end:
            clauses.append("timestamp <= ?")
            params.append(end)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY timestamp ASC, id ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            Quote(
                symbol=row["symbol"], name=row["name"], market=row["market"],
                price=row["price"], change_pct=row["change_pct"], volume=row["volume"],
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    def get_balance(self, currency: str) -> float:
        row = self.conn.execute(
            "SELECT cash FROM account_balances WHERE currency = ?", (currency,)
        ).fetchone()
        return float(row["cash"]) if row else 0.0

    def update_balance(self, currency: str, cash: float) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO account_balances (currency, cash, reserved_cash, updated_at)
                VALUES (?, ?, 0, ?)
                ON CONFLICT(currency) DO UPDATE SET cash=excluded.cash, updated_at=excluded.updated_at
                """,
                (currency, float(cash), utc_now()),
            )

    def get_position(self, market: str, symbol: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM positions WHERE market = ? AND symbol = ?",
            (market, symbol),
        ).fetchone()
        if row:
            return dict(row)
        return {
            "market": market, "symbol": symbol, "name": symbol, "currency": "",
            "quantity": 0, "avg_cost": 0.0, "realized_pnl": 0.0,
        }

    def upsert_position(self, market: str, symbol: str, name: str, currency: str,
                        quantity: int, avg_cost: float, realized_pnl: float) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO positions
                (market, symbol, name, currency, quantity, avg_cost, realized_pnl, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET
                    name=excluded.name,
                    currency=excluded.currency,
                    quantity=excluded.quantity,
                    avg_cost=excluded.avg_cost,
                    realized_pnl=excluded.realized_pnl,
                    updated_at=excluded.updated_at
                """,
                (market, symbol, name, currency, int(quantity), float(avg_cost),
                 float(realized_pnl), utc_now()),
            )

    def record_signal(self, signal) -> str:
        signal_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO strategy_signals
                (signal_id, strategy_id, symbol, market, action, trigger_field,
                 trigger_value, current_value, quote_price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id, signal.strategy.id, signal.symbol, signal.market, signal.action,
                    signal.trigger_field, signal.trigger_value, signal.current_value,
                    signal.quote_price, utc_now(),
                ),
            )
        return signal_id

    def record_order(self, signal_id: str, strategy_id: str, symbol: str, market: str,
                     side: str, quantity: int, price: float, currency: str,
                     status: str, reason: str = "") -> str:
        order_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO orders
                (order_id, signal_id, strategy_id, symbol, market, side, quantity,
                 price, currency, status, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, signal_id, strategy_id, symbol, market, side, int(quantity),
                 float(price), currency, status, reason, utc_now()),
            )
        return order_id

    def record_fill(self, order_id: str, price: float, quantity: int, fee: float = 0.0) -> str:
        fill_id = str(uuid.uuid4())
        amount = float(price) * int(quantity)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO fills (fill_id, order_id, fill_price, quantity, amount, fee, filled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (fill_id, order_id, float(price), int(quantity), amount, float(fee), utc_now()),
            )
        return fill_id

    def order_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM orders").fetchone()["c"])

    def fill_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS c FROM fills").fetchone()["c"])

    def realized_pnl(self) -> float:
        row = self.conn.execute("SELECT COALESCE(SUM(realized_pnl), 0) AS pnl FROM positions").fetchone()
        return float(row["pnl"])
