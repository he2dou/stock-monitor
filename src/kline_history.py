from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import requests


@dataclass
class DailyBar:
    symbol: str
    name: str
    market: str
    date: str
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: float
    source: str = "yahoo"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "market": self.market,
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "adj_close": self.adj_close,
            "volume": self.volume,
            "source": self.source,
        }


class YahooDailyBarSource:
    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def fetch_daily_bars(self, symbol: str, name: str, market: str,
                         start: str, end: str) -> list[DailyBar]:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date < start_date:
            raise ValueError("end date must be greater than or equal to start date")

        params = {
            "period1": _unix_utc(start_date),
            "period2": _unix_utc(end_date + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        response = requests.get(
            self.BASE_URL.format(symbol=symbol),
            params=params,
            timeout=self.timeout,
            headers={"User-Agent": "Mozilla/5.0 stock-monitor/1.0"},
        )
        response.raise_for_status()
        return parse_yahoo_chart(symbol, name, market, response.json(), start_date, end_date)


class NasdaqDailyBarSource:
    BASE_URL = "https://api.nasdaq.com/api/quote/{symbol}/historical"

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def fetch_daily_bars(self, symbol: str, name: str, market: str,
                         start: str, end: str) -> list[DailyBar]:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date < start_date:
            raise ValueError("end date must be greater than or equal to start date")
        response = requests.get(
            self.BASE_URL.format(symbol=symbol),
            params={
                "assetclass": "etf",
                "fromdate": start,
                "todate": end,
                "limit": "9999",
            },
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 stock-monitor/1.0",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
        )
        response.raise_for_status()
        return parse_nasdaq_historical(symbol, name, market, response.json(), start_date, end_date)


def parse_yahoo_chart(symbol: str, name: str, market: str, payload: dict[str, Any],
                      start: date, end: date) -> list[DailyBar]:
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise ValueError(f"Yahoo chart error: {error}")
    results = chart.get("result") or []
    if not results:
        return []

    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0]
    adj = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []

    bars: list[DailyBar] = []
    for idx, ts in enumerate(timestamps):
        bar_date = datetime.fromtimestamp(int(ts), tz=timezone.utc).date()
        if bar_date < start or bar_date > end:
            continue
        open_price = _item(quote.get("open"), idx)
        high = _item(quote.get("high"), idx)
        low = _item(quote.get("low"), idx)
        close = _item(quote.get("close"), idx)
        volume = _item(quote.get("volume"), idx, default=0.0)
        adj_close = _item(adj, idx, default=close)
        if None in {open_price, high, low, close}:
            continue
        bars.append(DailyBar(
            symbol=symbol,
            name=name,
            market=market,
            date=bar_date.isoformat(),
            open=float(open_price),
            high=float(high),
            low=float(low),
            close=float(close),
            adj_close=float(adj_close if adj_close is not None else close),
            volume=float(volume or 0.0),
        ))
    return bars


def parse_nasdaq_historical(symbol: str, name: str, market: str, payload: dict[str, Any],
                            start: date, end: date) -> list[DailyBar]:
    rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or [])
    bars: list[DailyBar] = []
    for row in rows:
        raw_date = row.get("date")
        if not raw_date:
            continue
        bar_date = datetime.strptime(raw_date, "%m/%d/%Y").date()
        if bar_date < start or bar_date > end:
            continue
        open_price = _parse_number(row.get("open"))
        high = _parse_number(row.get("high"))
        low = _parse_number(row.get("low"))
        close = _parse_number(row.get("close"))
        volume = _parse_number(row.get("volume"), default=0.0)
        if None in {open_price, high, low, close}:
            continue
        bars.append(DailyBar(
            symbol=symbol,
            name=name,
            market=market,
            date=bar_date.isoformat(),
            open=float(open_price),
            high=float(high),
            low=float(low),
            close=float(close),
            adj_close=float(close),
            volume=float(volume or 0.0),
            source="nasdaq",
        ))
    bars.sort(key=lambda bar: bar.date)
    return bars


def _parse_number(value, default=None):
    if value in (None, "", "N/A"):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if not cleaned:
        return default
    return float(cleaned)

def _item(values, idx: int, default=None):
    if values is None or idx >= len(values):
        return default
    value = values[idx]
    return default if value is None else value


def _unix_utc(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())
