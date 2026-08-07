from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# akshare  source  (works inside mainland China  where Yahoo is blocked)
# ---------------------------------------------------------------------------

class AkshareDailyBarSource:
    """Fetch daily OHLCV bars via akshare (East Money / Sina backends).

    Works from mainland China where Yahoo Finance has been blocked since
    November 2021.  Covers US, HK and A-share markets.
    """

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    # -- public API ----------------------------------------------------------

    def fetch_daily_bars(self, symbol: str, name: str, market: str,
                         start: str, end: str) -> list[DailyBar]:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date < start_date:
            raise ValueError("end date must be >= start date")

        if market in ("美股", "US", "us"):
            return self._fetch_us(symbol, name, market, start_date, end_date)
        if market in ("港股", "HK", "hk"):
            return self._fetch_hk(symbol, name, market, start_date, end_date)
        if market in ("A股", "A-share", "a"):
            return self._fetch_a(symbol, name, market, start_date, end_date)

        # fallback: try US
        return self._fetch_us(symbol, name, market, start_date, end_date)

    # -- per-market helpers --------------------------------------------------

    def _fetch_us(self, symbol: str, name: str, market: str,
                  start: date, end: date) -> list[DailyBar]:
        import akshare as ak
        df = ak.stock_us_daily(symbol=symbol.upper(), adjust="qfq")
        return _parse_akshare_df(df, symbol, name, market, start, end,
                                 source="akshare")

    def _fetch_hk(self, symbol: str, name: str, market: str,
                  start: date, end: date) -> list[DailyBar]:
        import akshare as ak
        # akshare requires 5-digit HK symbols (e.g. "00700")
        hk_sym = symbol.strip().zfill(5)
        df = ak.stock_hk_daily(symbol=hk_sym, adjust="qfq")
        return _parse_akshare_df(df, symbol, name, market, start, end,
                                 source="akshare")

    def _fetch_a(self, symbol: str, name: str, market: str,
                 start: date, end: date) -> list[DailyBar]:
        import akshare as ak
        sym = symbol.strip()
        prefix = _a_share_exchange(sym)
        a_sym = prefix + sym
        if _is_a_share_fund(sym):
            # ETF / LOF / fund: stock_zh_a_daily only serves stocks and returns
            # empty/invalid data for fund codes.  fund_etf_hist_sina covers all
            # exchange-traded funds via the Sina backend (directly reachable).
            df = ak.fund_etf_hist_sina(symbol=a_sym)
        else:
            df = ak.stock_zh_a_daily(symbol=a_sym, adjust="qfq")
        return _parse_akshare_df(df, symbol, name, market, start, end,
                                 source="akshare")


# A-share code classification helpers.  A-share codes encode both the
# exchange and the instrument type in their leading digits:
#   6xxxxx  Shanghai stock          5xxxxx  Shanghai fund/ETF (e.g. 510300)
#   0/3xxx  Shenzhen stock           1xxxxx  Shenzhen fund/ETF (e.g. 159326)
def _a_share_exchange(symbol: str) -> str:
    """Return the sina/exchange prefix ('sh' or 'sz') for an A-share code."""
    s = symbol.strip().lower()
    # Shanghai: 6 (stocks), 5 (funds/ETFs), 9 (B-shares), 7/11/13 (bonds).
    if s and s[0] in ("6", "5", "9"):
        return "sh"
    return "sz"



def _is_a_share_fund(symbol: str) -> bool:
    """True for ETF / LOF / fund codes (routed to fund_etf_hist_sina)."""
    s = symbol.strip()
    return s[:1] in ("1", "5")



def _parse_akshare_df(df: pd.DataFrame, symbol: str, name: str, market: str,
                      start: date, end: date, source: str) -> list[DailyBar]:
    """Convert an akshare DataFrame to a list of DailyBar objects."""
    bars: list[DailyBar] = []
    for _, row in df.iterrows():
        raw_date = row["date"]
        if isinstance(raw_date, pd.Timestamp):
            bar_date = raw_date.date()
        elif isinstance(raw_date, datetime):
            bar_date = raw_date.date()
        elif isinstance(raw_date, date):
            bar_date = raw_date
        else:
            bar_date = datetime.strptime(str(raw_date), "%Y-%m-%d").date()

        if bar_date < start or bar_date > end:
            continue

        open_price = row.get("open")
        high = row.get("high")
        low = row.get("low")
        close = row.get("close")
        volume = row.get("volume", 0.0)
        # adj_close: prefer "adj close" column, fall back to close
        adj_close = row.get("adj close", close)

        if _any_none(open_price, high, low, close):
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
            adj_close=float(adj_close if not _is_none(adj_close) else close),
            volume=float(volume or 0.0),
            source=source,
        ))
    return bars


# ---------------------------------------------------------------------------
# Yahoo  source  (blocked in mainland China since Nov 2021)
# ---------------------------------------------------------------------------

class YahooDailyBarSource:
    """Fetch daily OHLCV bars from the Yahoo Finance chart API.

    Yahoo is blocked in mainland China, so requests fail by default.  When a
    direct request fails, the source transparently retries through a local
    HTTP proxy (Clash/V2Ray at 127.0.0.1:7890 by default).  The proxy can be
    customised via the *proxy* constructor argument or the YAHOO_PROXY
    environment variable; set either to an empty string to disable the
    fallback.
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    DEFAULT_PROXY = "http://127.0.0.1:7890"

    def __init__(self, timeout: int = 20, proxy: str | None = None):
        self.timeout = timeout
        if proxy is None:
            proxy = os.environ.get("YAHOO_PROXY", self.DEFAULT_PROXY)
        self.proxy = proxy.strip() if proxy else None

    def fetch_daily_bars(self, symbol: str, name: str, market: str,
                         start: str, end: str) -> list[DailyBar]:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
        if end_date < start_date:
            raise ValueError("end date must be greater than or equal to start date")

        yahoo_symbol = _normalize_yahoo_symbol(symbol, market)
        params = {
            "period1": _unix_utc(start_date),
            "period2": _unix_utc(end_date + timedelta(days=1)),
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        payload = self._fetch_json(yahoo_symbol, params)
        return parse_yahoo_chart(symbol, name, market, payload, start_date, end_date)

    def _fetch_json(self, yahoo_symbol: str, params: dict[str, Any]) -> dict[str, Any]:
        """Download the chart JSON, falling back to a local proxy on failure."""
        url = self.BASE_URL.format(symbol=yahoo_symbol)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        # Attempt 1: direct connection.  main.py sets NO_PROXY=* process-wide so
        # this stays proxy-free even when a local proxy is running.
        try:
            response = requests.get(url, params=params, timeout=self.timeout,
                                    headers=headers)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if not self.proxy:
                raise
            logger.warning(
                "Yahoo direct request failed (%s); retrying via proxy %s",
                exc, self.proxy,
            )
        # Attempt 2: route through the local proxy.  Use a dedicated session
        # with trust_env disabled so the process-wide NO_PROXY=* does not
        # cancel the explicit proxies dict.
        session = requests.Session()
        session.trust_env = False
        session.proxies = {"http": self.proxy, "https": self.proxy}
        response = session.get(url, params=params, timeout=self.timeout,
                               headers=headers)
        response.raise_for_status()
        return response.json()

# ---------------------------------------------------------------------------
# Nasdaq  source
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _normalize_yahoo_symbol(symbol: str, market: str) -> str:
    """Convert a raw symbol to Yahoo Finance format based on market."""
    symbol = symbol.strip().upper()
    if "." in symbol:
        return symbol
    if market in ("港股", "HK", "hk"):
        return symbol.lstrip("0") + ".HK"
    if market in ("A股", "A-share", "a"):
        if symbol.startswith("6"):
            return symbol + ".SS"
        return symbol + ".SZ"
    return symbol


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


def _is_none(value) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return False


def _any_none(*values) -> bool:
    return any(_is_none(v) for v in values)