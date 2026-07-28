from __future__ import annotations

import contextlib
import io
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd

from src.index_snapshots import DEFAULT_MARKET_INDICES, load_market_indices
from src.market_hours import CST


def backfill_index_snapshots(store, config: dict | None, start: str, end: str) -> dict:
    """Fetch historical daily index bars and upsert them into index_snapshots."""
    indices = load_market_indices(config)
    if not indices:
        return {"saved": 0, "rows": 0, "indices": 0, "errors": []}

    source = TencentIndexHistorySource()
    rows: list[dict] = []
    errors: list[dict] = []
    for item in indices:
        try:
            rows.extend(source.fetch_index_rows(item, start, end))
        except Exception as e:
            errors.append({"symbol": item.get("symbol"), "market": item.get("market"), "error": str(e)})
    saved = store.save_index_snapshot_rows(rows) if rows else 0
    return {"saved": saved, "rows": len(rows), "indices": len(indices), "errors": errors}


class TencentIndexHistorySource:
    """Historical daily bars for A/HK/US indices via Tencent-backed AkShare API."""

    def fetch_index_rows(self, index: dict, start: str, end: str) -> list[dict]:
        start_date = _parse_date(start)
        end_date = _parse_date(end)
        provider_symbol = index.get("tencent_symbol") or index.get("provider_symbol")
        if not provider_symbol:
            provider_symbol = _default_tencent_symbol(index)

        # Pull a little earlier so the first requested day can compute change_pct
        # from the prior trading day's close.
        fetch_start = (start_date - timedelta(days=14)).strftime("%Y%m%d")
        fetch_end = end_date.strftime("%Y%m%d")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            df = ak.stock_zh_index_daily_tx(
                symbol=str(provider_symbol),
                start_date=fetch_start,
                end_date=fetch_end,
            )
        if df is None or df.empty:
            return []
        return _rows_from_dataframe(df, index, start_date, end_date)


def _rows_from_dataframe(df: pd.DataFrame, index: dict, start: date, end: date) -> list[dict]:
    data = df.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.date
    data = data.sort_values("date")
    data["prev_close"] = data["close"].shift(1)

    rows: list[dict] = []
    for _, row in data.iterrows():
        snapshot_date = row["date"]
        if snapshot_date < start or snapshot_date > end:
            continue
        close = float(row["close"])
        prev_close = row.get("prev_close")
        if pd.notna(prev_close) and float(prev_close) > 0:
            change_pct = (close - float(prev_close)) / float(prev_close) * 100.0
        else:
            change_pct = 0.0
        rows.append({
            "symbol": str(index["symbol"]),
            "name": index["name"],
            "market": index["market"],
            "price": close,
            "change_pct": change_pct,
            "volume": float(row.get("amount", 0) or 0),
            "snapshot_date": snapshot_date.isoformat(),
            "timestamp": _snapshot_timestamp(index["market"], snapshot_date),
        })
    return rows


def _snapshot_timestamp(market: str, snapshot_date: date) -> str:
    if market == "美股":
        # US regular close is next Beijing calendar day, around 04:00/05:00.
        return datetime.combine(snapshot_date + timedelta(days=1), datetime.min.time(), tzinfo=CST).replace(hour=5).isoformat()
    if market == "港股":
        return datetime.combine(snapshot_date, datetime.min.time(), tzinfo=CST).replace(hour=16).isoformat()
    return datetime.combine(snapshot_date, datetime.min.time(), tzinfo=CST).replace(hour=15).isoformat()


def _default_tencent_symbol(index: dict) -> str:
    symbol = str(index["symbol"])
    market = index["market"]
    if market == "A股":
        return f"sh{symbol}" if symbol.startswith(("5", "6", "9", "11", "13", "000")) else f"sz{symbol}"
    if market == "港股":
        return f"hk{symbol}"
    if market == "美股":
        mapping = {item["symbol"]: item["tencent_symbol"] for item in DEFAULT_MARKET_INDICES}
        return mapping.get(symbol, f"us{symbol.upper().lstrip('.')}")
    raise ValueError(f"Unsupported market for index history: {market}")


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()