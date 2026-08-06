from __future__ import annotations

import sqlite3
import json
import threading
import uuid
from pathlib import Path
from datetime import datetime, timezone
from src.models import Quote
from src.index_snapshots import market_snapshot_date


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_date_for(market: str, timestamp: str) -> str:
    try:
        normalized = (timestamp or "").replace("Z", "+00:00")
        when = datetime.fromisoformat(normalized)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        when = datetime.now(timezone.utc)
    return market_snapshot_date(market, when)


class LockedConnection:
    """Serialize access to a SQLite connection shared with APScheduler threads."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._lock:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self):
        self._lock.acquire()
        return self._conn.__enter__()

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._conn.__exit__(exc_type, exc, tb)
        finally:
            self._lock.release()


class TradingStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        raw_conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        raw_conn.row_factory = sqlite3.Row
        raw_conn.execute("PRAGMA busy_timeout = 30000")
        if db_path != ":memory:":
            raw_conn.execute("PRAGMA journal_mode = WAL")
        self.conn = LockedConnection(raw_conn, self._lock)
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
                snapshot_date TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS index_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                price REAL NOT NULL,
                change_pct REAL NOT NULL,
                volume REAL NOT NULL,
                snapshot_date TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                UNIQUE(symbol, snapshot_date)
            );

            CREATE TABLE IF NOT EXISTS daily_bars (
                symbol TEXT NOT NULL,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                adj_close REAL NOT NULL,
                volume REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'yahoo',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            );

            CREATE TABLE IF NOT EXISTS strategy_signals (
                signal_id TEXT PRIMARY KEY,
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                market TEXT NOT NULL,
                action TEXT NOT NULL,
                trigger_field TEXT NOT NULL,
                trigger_op TEXT NOT NULL DEFAULT '',
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
                initial_cash REAL NOT NULL,
                reserved_cash REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_items (
                strategy_id TEXT PRIMARY KEY,
                config_json TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS strategy_bindings (
                strategy_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (strategy_id, symbol)
            );
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TEXT NOT NULL,
                strategy_id TEXT,
                symbol TEXT,
                start_date TEXT,
                end_date TEXT,
                source TEXT,
                next_bar INTEGER NOT NULL DEFAULT 0,
                apply_costs INTEGER NOT NULL DEFAULT 0,
                total_return_pct REAL,
                max_drawdown_pct REAL,
                sell_win_rate_pct REAL,
                sharpe_ratio REAL,
                fills INTEGER NOT NULL DEFAULT 0,
                avg_r_multiple REAL,
                realized_pnl REAL,
                starting_equity REAL,
                ending_equity REAL,
                summary_json TEXT
            );            CREATE TABLE IF NOT EXISTS watchlist_items (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                market TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                rule_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                field TEXT NOT NULL,
                op TEXT NOT NULL,
                value REAL NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                cooldown_seconds INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            """
        )
        self._migrate_schema()
        self.conn.commit()

    def _migrate_schema(self) -> None:
        quote_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(quote_snapshots)").fetchall()
        }
        if "snapshot_date" not in quote_columns:
            self.conn.execute("ALTER TABLE quote_snapshots ADD COLUMN snapshot_date TEXT")
        self._populate_quote_snapshot_dates()
        self._dedupe_daily_snapshots("quote_snapshots")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_quote_snapshots_symbol_day
            ON quote_snapshots(symbol, snapshot_date)
            """
        )
        self._dedupe_daily_snapshots("index_snapshots")
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_index_snapshots_symbol_day
            ON index_snapshots(symbol, snapshot_date)
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_bars_symbol_date
            ON daily_bars(symbol, date)
            """
        )

        strategy_tables = {
            row["name"] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "strategy_configs" in strategy_tables and "strategy_items" not in strategy_tables:
            self.conn.execute("ALTER TABLE strategy_configs RENAME TO strategy_items")
        elif "strategy_configs" in strategy_tables and "strategy_items" in strategy_tables:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO strategy_items
                (strategy_id, config_json, enabled, updated_at)
                SELECT strategy_id, config_json, enabled, updated_at
                FROM strategy_configs
                """
            )
            self.conn.execute("DROP TABLE strategy_configs")
        balance_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(account_balances)").fetchall()
        }
        if "initial_cash" not in balance_columns:
            self.conn.execute("ALTER TABLE account_balances ADD COLUMN initial_cash REAL")
            self.conn.execute("UPDATE account_balances SET initial_cash = cash WHERE initial_cash IS NULL")

        backtest_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(backtest_runs)").fetchall()
        }
        if "summary_json" not in backtest_columns:
            self.conn.execute("ALTER TABLE backtest_runs ADD COLUMN summary_json TEXT")

        # Convert legacy per-strategy symbols into explicit bindings once.
        for row in self.conn.execute(
            "SELECT strategy_id, config_json FROM strategy_items"
        ).fetchall():
            try:
                config = json.loads(row["config_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            symbol = str(config.get("symbol", "") or "").strip()
            if symbol:
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO strategy_bindings
                    (strategy_id, symbol, enabled, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (row["strategy_id"], symbol, int(bool(config.get("enabled", True))), utc_now()),
                )
        signal_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(strategy_signals)").fetchall()
        }
        if "trigger_op" not in signal_columns:
            self.conn.execute("ALTER TABLE strategy_signals ADD COLUMN trigger_op TEXT NOT NULL DEFAULT ''")

        order_columns = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        if "trading_day" not in order_columns:
            self.conn.execute("ALTER TABLE orders ADD COLUMN trading_day TEXT")
        if "signal_key" not in order_columns:
            self.conn.execute("ALTER TABLE orders ADD COLUMN signal_key TEXT NOT NULL DEFAULT ''")
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_daily_signal
            ON orders(strategy_id, symbol, side, trading_day, signal_key)
            """
        )

    def _populate_quote_snapshot_dates(self) -> None:
        rows = self.conn.execute(
            """
            SELECT id, market, timestamp FROM quote_snapshots
            WHERE snapshot_date IS NULL OR snapshot_date = ''
            """
        ).fetchall()
        for row in rows:
            self.conn.execute(
                "UPDATE quote_snapshots SET snapshot_date = ? WHERE id = ?",
                (snapshot_date_for(row["market"], row["timestamp"]), row["id"]),
            )

    def _dedupe_daily_snapshots(self, table_name: str) -> None:
        if table_name not in {"quote_snapshots", "index_snapshots"}:
            raise ValueError(f"Unsupported snapshot table: {table_name}")
        self.conn.execute(
            f"""
            DELETE FROM {table_name}
            WHERE id NOT IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol, snapshot_date
                               ORDER BY timestamp DESC, id DESC
                           ) AS rn
                    FROM {table_name}
                )
                WHERE rn = 1
            )
            """
        )

    def ensure_accounts(self, accounts: dict[str, float]) -> None:
        now = utc_now()
        with self.conn:
            for currency, cash in accounts.items():
                self.conn.execute(
                    """
                    INSERT OR IGNORE INTO account_balances
                    (currency, cash, initial_cash, reserved_cash, updated_at)
                    VALUES (?, ?, ?, 0, ?)
                    """,
                    (currency, float(cash), float(cash), now),
                )

    def load_strategies(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT config_json FROM strategy_items ORDER BY strategy_id"
        ).fetchall()
        return [json.loads(row["config_json"]) for row in rows]

    def upsert_strategy(self, config: dict) -> None:
        strategy_id = str(config.get("id", "")).strip()
        if not strategy_id:
            raise ValueError("Strategy missing id")
        now = utc_now()
        stored_config = dict(config)
        legacy_symbol = str(stored_config.pop("symbol", "") or "").strip()
        with self.conn:
            self.conn.execute(
                """INSERT INTO strategy_items
                (strategy_id, config_json, enabled, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                config_json=excluded.config_json, enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (strategy_id, json.dumps(stored_config, ensure_ascii=False), int(bool(config.get("enabled", True))), now),
            )
            if legacy_symbol:
                self.conn.execute(
                    """INSERT INTO strategy_bindings (strategy_id, symbol, enabled, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(strategy_id, symbol) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
                    (strategy_id, legacy_symbol, int(bool(config.get("enabled", True))), now),
                )
    def delete_strategy(self, strategy_id: str) -> bool:
        with self.conn:
            cursor = self.conn.execute("DELETE FROM strategy_items WHERE strategy_id = ?", (strategy_id,))
            self.conn.execute("DELETE FROM strategy_bindings WHERE strategy_id = ?", (strategy_id,))
        return cursor.rowcount > 0

    def load_strategy_bindings(self, strategy_id: str | None = None, include_disabled: bool = True) -> list[dict]:
        sql = "SELECT strategy_id, symbol, enabled, updated_at FROM strategy_bindings"
        params: list = []
        clauses = []
        if strategy_id is not None:
            clauses.append("strategy_id = ?")
            params.append(strategy_id)
        if not include_disabled:
            clauses.append("enabled = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY strategy_id, symbol"
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def upsert_strategy_binding(self, strategy_id: str, symbol: str, enabled: bool = True) -> None:
        strategy_id, symbol = str(strategy_id).strip(), str(symbol).strip()
        if not strategy_id or not symbol:
            raise ValueError("Strategy binding requires strategy_id and symbol")
        with self.conn:
            self.conn.execute(
                """INSERT INTO strategy_bindings (strategy_id, symbol, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(strategy_id, symbol) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (strategy_id, symbol, int(bool(enabled)), utc_now()),
            )

    def delete_strategy_binding(self, strategy_id: str, symbol: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM strategy_bindings WHERE strategy_id = ? AND symbol = ?",
                (strategy_id, symbol),
            )
        return cursor.rowcount > 0

    def load_runtime_strategies(self) -> list[dict]:
        configs = {item.get("id"): item for item in self.load_strategies() if item.get("id")}
        result = []
        for binding in self.load_strategy_bindings(include_disabled=False):
            config = configs.get(binding["strategy_id"])
            if not config or not config.get("enabled", True):
                continue
            item = dict(config)
            item["symbol"] = binding["symbol"]
            result.append(item)
        return result
    def seed_strategies(self, strategies: list[dict]) -> int:
        if self.conn.execute("SELECT 1 FROM strategy_items LIMIT 1").fetchone():
            return 0
        for config in strategies:
            self.upsert_strategy(config)
        return len(strategies)

    def save_backtest_run(self, params: dict, summary: dict) -> int:
        full = dict(summary)
        full.pop("equity_curve", None)
        summary_text = json.dumps(full, ensure_ascii=False, default=str)
        cursor = self.conn.execute(
            """
            INSERT INTO backtest_runs
            (run_at, strategy_id, symbol, start_date, end_date, source,
             next_bar, apply_costs, total_return_pct, max_drawdown_pct,
             sell_win_rate_pct, sharpe_ratio, fills, avg_r_multiple,
             realized_pnl, starting_equity, ending_equity, summary_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                utc_now(),
                params.get("strategy_id") or "",
                params.get("symbol") or "",
                params.get("start") or "",
                params.get("end") or "",
                params.get("source") or "",
                int(bool(params.get("next_bar"))),
                int(bool(params.get("apply_costs"))),
                summary.get("total_return_pct"),
                summary.get("max_drawdown_pct"),
                summary.get("sell_win_rate_pct"),
                summary.get("sharpe_ratio"),
                summary.get("fills", 0),
                summary.get("avg_r_multiple"),
                summary.get("realized_pnl"),
                summary.get("starting_equity"),
                summary.get("ending_total_equity"),
                summary_text,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_backtest_run(self, run_id: int) -> dict | None:
        row = self.conn.execute(
            """
            SELECT id, run_at, strategy_id, symbol, start_date, end_date,
                   source, next_bar, apply_costs, total_return_pct,
                   max_drawdown_pct, sell_win_rate_pct, sharpe_ratio,
                   fills, avg_r_multiple, realized_pnl, starting_equity,
                   ending_equity, summary_json
            FROM backtest_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def load_backtest_runs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, run_at, strategy_id, symbol, start_date, end_date,
                   source, next_bar, apply_costs, total_return_pct,
                   max_drawdown_pct, sell_win_rate_pct, sharpe_ratio,
                   fills, avg_r_multiple, realized_pnl, starting_equity,
                   ending_equity
            FROM backtest_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_backtest_run(self, run_id: int) -> None:
        self.conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
        self.conn.commit()
    def save_quote_snapshots(self, quotes: list[Quote]) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT INTO quote_snapshots
                (symbol, name, market, price, change_pct, volume, snapshot_date, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, snapshot_date) DO UPDATE SET
                    name=excluded.name,
                    market=excluded.market,
                    price=excluded.price,
                    change_pct=excluded.change_pct,
                    volume=excluded.volume,
                    timestamp=excluded.timestamp
                """,
                [
                    (
                        q.symbol, q.name, q.market, q.price, q.change_pct, q.volume,
                        snapshot_date_for(q.market, q.timestamp), q.timestamp,
                    )
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

    def save_daily_bars(self, bars: list[dict]) -> int:
        saved = 0
        now = utc_now()
        with self.conn:
            for bar in bars:
                cursor = self.conn.execute(
                    """
                    INSERT INTO daily_bars
                    (symbol, name, market, date, open, high, low, close, adj_close,
                     volume, source, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, date) DO UPDATE SET
                        name=excluded.name,
                        market=excluded.market,
                        open=excluded.open,
                        high=excluded.high,
                        low=excluded.low,
                        close=excluded.close,
                        adj_close=excluded.adj_close,
                        volume=excluded.volume,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (
                        bar["symbol"], bar["name"], bar["market"], bar["date"],
                        float(bar["open"]), float(bar["high"]), float(bar["low"]),
                        float(bar["close"]), float(bar.get("adj_close", bar["close"])),
                        float(bar.get("volume", 0) or 0), bar.get("source", "yahoo"), now,
                    ),
                )
                saved += cursor.rowcount
        return saved

    def load_daily_bars(self, symbols: list[str] | None = None,
                        start: str | None = None, end: str | None = None) -> list[dict]:
        sql = "SELECT * FROM daily_bars"
        params: list[str] = []
        clauses: list[str] = []
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            clauses.append(f"symbol IN ({placeholders})")
            params.extend(symbols)
        if start:
            clauses.append("date >= ?")
            params.append(start)
        if end:
            clauses.append("date <= ?")
            params.append(end)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY date ASC, symbol ASC"
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]
    def index_snapshot_exists(self, symbol: str, snapshot_date: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM index_snapshots
            WHERE symbol = ? AND snapshot_date = ?
            LIMIT 1
            """,
            (symbol, snapshot_date),
        ).fetchone()
        return row is not None

    def save_index_snapshots(self, quotes: list[Quote], snapshot_dates: dict[str, str]) -> int:
        """Insert or update one daily row per index symbol."""
        rows = [
            {
                "symbol": q.symbol,
                "name": q.name,
                "market": q.market,
                "price": q.price,
                "change_pct": q.change_pct,
                "volume": q.volume,
                "snapshot_date": snapshot_dates[q.symbol],
                "timestamp": q.timestamp,
            }
            for q in quotes
        ]
        return self.save_index_snapshot_rows(rows)

    def save_index_snapshot_rows(self, rows: list[dict]) -> int:
        saved = 0
        with self.conn:
            for row in rows:
                cursor = self.conn.execute(
                    """
                    INSERT INTO index_snapshots
                    (symbol, name, market, price, change_pct, volume, snapshot_date, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, snapshot_date) DO UPDATE SET
                        name=excluded.name,
                        market=excluded.market,
                        price=excluded.price,
                        change_pct=excluded.change_pct,
                        volume=excluded.volume,
                        timestamp=excluded.timestamp
                    """,
                    (
                        row["symbol"], row["name"], row["market"], float(row["price"]),
                        float(row["change_pct"]), float(row.get("volume", 0) or 0),
                        row["snapshot_date"], row["timestamp"],
                    ),
                )
                saved += cursor.rowcount
        return saved

    def load_index_snapshots(self, start: str | None = None, end: str | None = None) -> list[dict]:
        sql = "SELECT * FROM index_snapshots"
        params: list[str] = []
        clauses: list[str] = []
        if start:
            clauses.append("snapshot_date >= ?")
            params.append(start)
        if end:
            clauses.append("snapshot_date <= ?")
            params.append(end)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY snapshot_date ASC, market ASC, symbol ASC"
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_balance(self, currency: str) -> float:
        row = self.conn.execute(
            "SELECT cash FROM account_balances WHERE currency = ?", (currency,)
        ).fetchone()
        return float(row["cash"]) if row else 0.0

    def update_balance(self, currency: str, cash: float) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO account_balances (currency, cash, initial_cash, reserved_cash, updated_at)
                    VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(currency) DO UPDATE SET cash=excluded.cash, updated_at=excluded.updated_at
                """,
                (currency, float(cash), float(cash), utc_now()),
            )

    def upsert_account_balance(self, currency: str, initial_cash: float, cash: float, reserved_cash: float = 0.0) -> None:
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO account_balances
                (currency, cash, initial_cash, reserved_cash, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(currency) DO UPDATE SET
                    cash=excluded.cash, initial_cash=excluded.initial_cash,
                    reserved_cash=excluded.reserved_cash, updated_at=excluded.updated_at
                """,
                (currency, float(cash), float(initial_cash), float(reserved_cash), now),
            )

    def delete_account_balance(self, currency: str) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM account_balances WHERE currency = ?", (currency,)
            )
        return cursor.rowcount > 0

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

    def delete_position(self, market: str, symbol: str) -> int:
        """Delete a position row. Returns rows affected (0/1)."""
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM positions WHERE market = ? AND symbol = ?",
                (market, symbol),
            )
            return int(cursor.rowcount)

    def record_signal(self, signal) -> str:
        signal_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO strategy_signals
                (signal_id, strategy_id, symbol, market, action, trigger_field,
                 trigger_op, trigger_value, current_value, quote_price, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal_id, signal.strategy.id, signal.symbol, signal.market, signal.action,
                    signal.trigger_field, signal.trigger_op, signal.trigger_value, signal.current_value,
                    signal.quote_price, signal.quote_timestamp or utc_now(),
                ),
            )
        return signal_id

    def record_order(self, signal_id: str, strategy_id: str, symbol: str, market: str,
                     side: str, quantity: int, price: float, currency: str,
                     status: str, reason: str = "", trading_day: str | None = None,
                     signal_key: str = "") -> str:
        order_id = str(uuid.uuid4())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO orders
                (order_id, signal_id, strategy_id, symbol, market, side, quantity,
                 price, currency, status, reason, trading_day, signal_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (order_id, signal_id, strategy_id, symbol, market, side, int(quantity),
                 float(price), currency, status, reason, trading_day, signal_key, utc_now()),
            )
        return order_id

    def has_order_for_signal_on_day(self, strategy_id: str, symbol: str, side: str,
                                    trading_day: str, signal_key: str = "") -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM orders
            WHERE strategy_id = ? AND symbol = ? AND side = ? AND trading_day = ?
              AND signal_key = ?
            LIMIT 1
            """,
            (strategy_id, symbol, side, trading_day, signal_key),
        ).fetchone()
        return row is not None

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
    # -- runtime config stored in SQLite ------------------------------------
    def watchlist_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM watchlist_items").fetchone()
        return int(row["c"])

    def alert_rule_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM alert_rules").fetchone()
        return int(row["c"])

    def seed_watchlist(self, stocks: list[dict]) -> int:
        """Insert YAML seed stocks when the DB watchlist is empty."""
        if self.watchlist_count() > 0:
            return 0
        inserted = 0
        now = utc_now()
        with self.conn:
            for idx, stock in enumerate(stocks):
                self.conn.execute(
                    """
                    INSERT INTO watchlist_items
                    (symbol, name, market, enabled, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?)
                    """,
                    (str(stock["symbol"]), stock["name"], stock["market"], idx, now, now),
                )
                inserted += 1
        return inserted

    def import_watchlist(self, stocks: list[dict], replace: bool = False) -> int:
        """Import stocks into DB. With replace=True, clear existing rows first."""
        now = utc_now()
        with self.conn:
            if replace:
                self.conn.execute("DELETE FROM watchlist_items")
            for idx, stock in enumerate(stocks):
                self.conn.execute(
                    """
                    INSERT INTO watchlist_items
                    (symbol, name, market, enabled, sort_order, created_at, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        name=excluded.name,
                        market=excluded.market,
                        enabled=1,
                        sort_order=excluded.sort_order,
                        updated_at=excluded.updated_at
                    """,
                    (str(stock["symbol"]), stock["name"], stock["market"], idx, now, now),
                )
        return len(stocks)

    def load_watchlist(self, include_disabled: bool = False) -> list[dict]:
        sql = "SELECT symbol, name, market, enabled FROM watchlist_items"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY sort_order ASC, symbol ASC"
        return [dict(row) for row in self.conn.execute(sql).fetchall()]

    def add_stock(self, symbol: str, name: str, market: str, enabled: bool = True) -> None:
        now = utc_now()
        next_order = self.watchlist_count()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO watchlist_items
                (symbol, name, market, enabled, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name=excluded.name,
                    market=excluded.market,
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at
                """,
                (symbol, name, market, 1 if enabled else 0, next_order, now, now),
            )

    def set_stock_enabled(self, symbol: str, enabled: bool) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE watchlist_items SET enabled = ?, updated_at = ? WHERE symbol = ?",
                (1 if enabled else 0, utc_now(), symbol),
            )

    def delete_stock(self, symbol: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM watchlist_items WHERE symbol = ?",
                (symbol,),
            )
            return int(cursor.rowcount)

    def seed_alert_rules(self, rules: list[dict]) -> int:
        """Insert YAML seed alert rules when the DB alert table is empty."""
        if self.alert_rule_count() > 0:
            return 0
        return self.import_alert_rules(rules, replace=False)

    def import_alert_rules(self, rules: list[dict], replace: bool = False) -> int:
        now = utc_now()
        with self.conn:
            if replace:
                self.conn.execute("DELETE FROM alert_rules")
            for rule in rules:
                self.conn.execute(
                    """
                    INSERT INTO alert_rules
                    (rule_id, symbol, field, op, value, enabled, cooldown_seconds, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), str(rule["symbol"]), rule["field"], rule["op"],
                        float(rule["value"]), 1, rule.get("cooldown_seconds"), now, now,
                    ),
                )
        return len(rules)

    def load_alert_rules(self, include_disabled: bool = False) -> list[dict]:
        sql = "SELECT rule_id, symbol, field, op, value, enabled, cooldown_seconds FROM alert_rules"
        if not include_disabled:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at ASC, rule_id ASC"
        rules: list[dict] = []
        for row in self.conn.execute(sql).fetchall():
            item = dict(row)
            if item.get("cooldown_seconds") is None:
                item.pop("cooldown_seconds", None)
            rules.append(item)
        return rules

    def add_alert_rule(self, symbol: str, field: str, op: str, value: float,
                       enabled: bool = True, cooldown_seconds: int | None = None) -> str:
        rule_id = str(uuid.uuid4())
        now = utc_now()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO alert_rules
                (rule_id, symbol, field, op, value, enabled, cooldown_seconds, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (rule_id, symbol, field, op, float(value), 1 if enabled else 0,
                 cooldown_seconds, now, now),
            )
        return rule_id

    def set_alert_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE alert_rules SET enabled = ?, updated_at = ? WHERE rule_id = ?",
                (1 if enabled else 0, utc_now(), rule_id),
            )

    def delete_alert_rule(self, rule_id: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM alert_rules WHERE rule_id = ?",
                (rule_id,),
            )
            return int(cursor.rowcount)
