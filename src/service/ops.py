from __future__ import annotations

from datetime import date

from src.index_history import backfill_index_snapshots
from src.index_snapshots import load_market_indices
from src.kline_history import NasdaqDailyBarSource, YahooDailyBarSource
from src.market_hours import is_market_open
from src.sources.sinatx_source import SinaTxSource
from src.trading_store import snapshot_date_for


def _filter_items(items, symbols, markets):
    selected = []
    for item in items:
        if symbols and str(item.get("symbol")) not in symbols:
            continue
        if markets and str(item.get("market")) not in markets:
            continue
        selected.append(item)
    return selected


def _open_items(items, market_open_fn, ignore_hours):
    if ignore_hours:
        return items, []
    open_items = []
    skipped = []
    for item in items:
        if market_open_fn(str(item.get("market", ""))):
            open_items.append(item)
        else:
            skipped.append(item)
    return open_items, skipped


def _date_years_ago(end, years):
    try:
        return end.replace(year=end.year - years)
    except ValueError:
        return end.replace(month=2, day=28, year=end.year - years)


def kline_range(start, end, years):
    end_date = date.fromisoformat(end) if end else date.today()
    start_date = date.fromisoformat(start) if start else _date_years_ago(end_date, years)
    if end_date < start_date:
        raise ValueError("end must be greater than or equal to start")
    return start_date.isoformat(), end_date.isoformat()


def make_kline_source(provider, timeout=20):
    cls = NasdaqDailyBarSource if provider == "nasdaq" else YahooDailyBarSource
    return cls(timeout=timeout)


def update_snapshots(store, *, app_config, target, symbols=None, markets=None,
                     ignore_hours=False, include_disabled=False,
                     source=None, market_open_fn=None, load_indices_fn=None):
    """Fetch realtime quotes and persist stock or index snapshots.

    Returns an operation summary dict so CLI and web share the same logic.
    Dependencies are injectable so tests can patch the caller's namespace.
    """
    if source is None:
        source = SinaTxSource()
    if market_open_fn is None:
        market_open_fn = is_market_open
    if load_indices_fn is None:
        load_indices_fn = load_market_indices

    if target == "stock":
        items = store.load_watchlist(include_disabled=include_disabled)
    else:
        items = load_indices_fn(app_config)

    selected = _filter_items(items, set(symbols or []), set(markets or []))
    to_fetch, skipped = _open_items(selected, market_open_fn, ignore_hours)
    quotes = source.fetch_quotes(to_fetch) if to_fetch else []

    saved = 0
    if quotes and target == "stock":
        store.save_quote_snapshots(quotes)
        saved = len(quotes)
    elif quotes:
        snapshot_dates = {q.symbol: snapshot_date_for(q.market, q.timestamp) for q in quotes}
        saved = store.save_index_snapshots(quotes, snapshot_dates)

    return {
        "target": target,
        "selected": len(selected),
        "requested": len(to_fetch),
        "fetched": len(quotes),
        "saved": saved,
        "skipped_closed": [item.get("symbol") for item in skipped],
        "updated_symbols": [q.symbol for q in quotes],
    }


def fetch_kline(store, *, symbol, name, market, start, end, years, source, provider="nasdaq"):
    """Fetch historical daily OHLCV bars into SQLite daily_bars via an injected source."""
    start_iso, end_iso = kline_range(start, end, years)
    bars = source.fetch_daily_bars(symbol=symbol, name=name, market=market, start=start_iso, end=end_iso)
    saved = store.save_daily_bars([bar.to_dict() for bar in bars])
    return {
        "ok": True,
        "symbol": symbol,
        "market": market,
        "from": start_iso,
        "to": end_iso,
        "fetched": len(bars),
        "provider": provider,
        "saved": saved,
        "first_date": bars[0].date if bars else None,
        "last_date": bars[-1].date if bars else None,
    }


def backfill_indices(store, *, app_config, start, end):
    """Fetch missing historical daily market index snapshots into SQLite."""
    return backfill_index_snapshots(store, app_config, start, end)
