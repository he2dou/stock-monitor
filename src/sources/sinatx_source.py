import logging
import time
import requests

from src.models import Quote
from src.sources.base import DataSource

logger = logging.getLogger(__name__)

# Sina/Tencent use GBK encoding for Chinese names.
SINA_ENDPOINT = "https://hq.sinajs.cn/list="
TENCENT_ENDPOINT = "https://qt.gtimg.cn/q="
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
# A Referer is REQUIRED by Sina since 2020; without it it returns HTTP 403.
SINA_REFERER = "https://finance.sina.com.cn/"


class SinaTxSource(DataSource):
    """Reliable realtime quotes via Tencent (primary) + Sina (fallback).

    Supports A-share, HK and US markets in a single batched request per
    provider. Resists local-proxy hijacking by setting trust_env=False on its
    session (requests then ignores HTTP_PROXY/HTTPS_PROXY entirely).
    """

    def __init__(
        self,
        timeout: int = 10,
        max_retries: int = 2,
        backoff: float = 2.0,
        session: requests.Session | None = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.session = session or requests.Session()
        # Crucial: do NOT inherit proxy settings from the environment / registry
        # (e.g. a running Clash/V2Ray). All domestic quote hosts are directly
        # reachable, so forcing direct connections keeps us stable.
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": BROWSER_UA})

    # -- public API ---------------------------------------------------------
    def fetch_quotes(self, stocks: list[dict]) -> list[Quote]:
        if not stocks:
            return []
        try:
            quotes = self._fetch_via_tencent(stocks)
        except Exception as e:
            # Isolate any provider failure so one bad source never aborts a cycle.
            logger.warning(f"Tencent provider raised: {e}")
            quotes = []
        if quotes and len(quotes) == len(stocks):
            return quotes

        got_symbols = {q.symbol for q in quotes}
        missing = [s for s in stocks if str(s["symbol"]) not in got_symbols]
        if missing:
            logger.info(f"Tencent missed {len(missing)} symbol(s); falling back to Sina")
            try:
                quotes.extend(self._fetch_via_sina(missing))
            except Exception as e:
                logger.warning(f"Sina provider raised: {e}")
        return quotes

    # -- Tencent (primary) --------------------------------------------------
    def _fetch_via_tencent(self, stocks: list[dict]) -> list[Quote]:
        indexed = [(s, self._tencent_symbol(s)) for s in stocks]
        indexed = [(s, ps) for s, ps in indexed if ps]
        if not indexed:
            return []
        syms = ",".join(ps for _, ps in indexed)
        try:
            text = self._http_text(f"{TENCENT_ENDPOINT}{syms}")
        except Exception as e:
            logger.warning(f"Tencent provider failed entirely: {e}")
            return []
        if not text:
            return []
        quotes: list[Quote] = []
        for s, ps in indexed:
            q = self._parse_tencent(text, ps, s)
            if q is not None:
                quotes.append(q)
        return quotes

    @staticmethod
    def _parse_tencent(text: str, provider_sym: str, stock: dict) -> Quote | None:
        var = f"v_{provider_sym}=\""
        i = text.find(var)
        if i < 0:
            return None
        start = i + len(var)
        end = text.find("\"", start)
        if end < 0:
            return None
        fields = text[start:end].split("~")
        if len(fields) < 4:
            return None
        try:
            price = float(fields[3])
        except (ValueError, IndexError):
            return None
        if price <= 0:
            return None
        name = fields[1] or stock.get("name", stock["symbol"])
        change_pct = _safe_float(fields, 32)
        if change_pct is None:
            prev = _safe_float(fields, 4)
            change_pct = (price - prev) / prev * 100.0 if prev else 0.0
        volume = _safe_float(fields, 6) or 0.0
        return Quote(
            symbol=str(stock["symbol"]),
            name=name,
            market=stock.get("market", ""),
            price=price,
            change_pct=change_pct,
            volume=volume,
        )

    # -- Sina (fallback) ----------------------------------------------------
    def _fetch_via_sina(self, stocks: list[dict]) -> list[Quote]:
        indexed = [(s, self._sina_symbol(s)) for s in stocks]
        indexed = [(s, ps) for s, ps in indexed if ps]
        if not indexed:
            return []
        syms = ",".join(ps for _, ps in indexed)
        try:
            text = self._http_text(
                f"{SINA_ENDPOINT}{syms}", extra_headers={"Referer": SINA_REFERER}
            )
        except Exception as e:
            logger.warning(f"Sina provider failed entirely: {e}")
            return []
        if not text:
            return []
        quotes: list[Quote] = []
        for s, ps in indexed:
            q = self._parse_sina(text, ps, s)
            if q is not None:
                quotes.append(q)
        return quotes

    @staticmethod
    def _parse_sina(text: str, provider_sym: str, stock: dict) -> Quote | None:
        market = stock.get("market", "")
        var = f"hq_str_{provider_sym}=\""
        i = text.find(var)
        if i < 0:
            return None
        start = i + len(var)
        end = text.find("\";", start)
        if end < 0:
            end = text.find("\"", start)
        if end < 0:
            return None
        fields = text[start:end].split(",")
        if len(fields) < 2:
            return None
        name = stock.get("name", stock["symbol"])
        try:
            if market == "美股":
                price = float(fields[1])
                change_pct = float(fields[2])
                volume = _safe_float(fields, 10) or 0.0
                name = fields[0] or name
            elif market == "港股":
                price = float(fields[6])
                change_pct = float(fields[8])
                volume = _safe_float(fields, 12) or 0.0
                name = fields[1] or name
            else:  # A股: name[0] open[1] prevclose[2] price[3] high[4] low[5] vol[8]
                price = float(fields[3])
                prev = _safe_float(fields, 2)
                change_pct = (price - prev) / prev * 100.0 if prev else 0.0
                volume = _safe_float(fields, 8) or 0.0
                name = fields[0] or name
        except (ValueError, IndexError):
            return None
        if price <= 0:
            return None
        return Quote(
            symbol=str(stock["symbol"]),
            name=name,
            market=market,
            price=price,
            change_pct=change_pct,
            volume=volume,
        )

    # -- HTTP helper --------------------------------------------------------
    def _http_text(self, url: str, extra_headers: dict | None = None) -> str:
        headers = {}
        if extra_headers:
            headers.update(extra_headers)
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
                resp.raise_for_status()
                return resp.content.decode("gbk", errors="replace")
            except (requests.ConnectionError, requests.HTTPError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    wait = self.backoff * (attempt + 1)
                    logger.warning(
                        f"quote fetch error ({url}) attempt {attempt + 1}/"
                        f"{self.max_retries + 1}, retry in {wait}s: {e}"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"quote fetch failed for {url}: {e}")
        assert last_exc is not None
        return ""

    # -- symbol mapping -----------------------------------------------------
    # A-share + HK prefixes coincide for both providers; US stocks differ:
    #   Tencent uses "usAAPL"  while  Sina uses "gb_aapl".
    @staticmethod
    def _tencent_symbol(stock: dict) -> str | None:
        sym = str(stock["symbol"]).strip()
        market = stock.get("market", "")
        if market == "A股":
            return f"sh{sym}" if sym.startswith(("5", "6", "9", "11", "13")) else f"sz{sym}"
        if market == "港股":
            return f"hk{sym}"
        if market == "美股":
            return f"us{sym.upper()}"
        return None

    @staticmethod
    def _sina_symbol(stock: dict) -> str | None:
        sym = str(stock["symbol"]).strip()
        market = stock.get("market", "")
        if market == "A股":
            return f"sh{sym}" if sym.startswith(("5", "6", "9", "11", "13")) else f"sz{sym}"
        if market == "港股":
            return f"hk{sym}"
        if market == "美股":
            return f"gb_{sym.lower()}"
        return None


def _safe_float(fields: list[str], idx: int) -> float | None:
    if idx >= len(fields):
        return None
    try:
        return float(fields[idx])
    except (TypeError, ValueError):
        return None
