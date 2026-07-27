import logging
import time
import requests
from src.models import Quote
from src.sources.base import DataSource

logger = logging.getLogger(__name__)

# Eastmoney "qt/stock/get" field codes (well-documented, fltt=2 -> human floats):
#   f57=代码, f58=名称, f43=最新价, f170=涨跌幅, f47=成交量(手)
FIELDS = "f57,f58,f43,f170,f47"
ENDPOINT = "https://push2.eastmoney.com/api/qt/stock/get"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


class EastmoneySource(DataSource):
    """Fetches quotes per-symbol from Eastmoney's individual-quote endpoint.

    This replaces the akshare bulk approach (``stock_zh_a_spot_em`` etc.), which
    downloads the entire market (~58 paginated requests) and reliably trips
    Eastmoney's aggressive IP rate-limit. Querying only the watched symbols keeps
    each cycle to a handful of spaced requests, well under the limit. See the
    ``eastmoney-rate-limiting`` project note.
    """

    def __init__(
        self,
        timeout: int = 10,
        delay: float = 1.0,
        max_retries: int = 2,
        backoff: float = 15.0,
        session: requests.Session | None = None,
    ):
        self.timeout = timeout
        # Seconds between symbols. Eastmoney blocks rapid bursts, so space them.
        self.delay = delay
        self.max_retries = max_retries
        # Rate-limit blocks last ~10-15s; back off by this much per attempt.
        self.backoff = backoff
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": BROWSER_UA})

    def fetch_quotes(self, stocks: list[dict]) -> list[Quote]:
        quotes: list[Quote] = []
        for i, s in enumerate(stocks):
            market = s.get("market", "A股")
            symbol = str(s["symbol"])
            fallback_name = s.get("name", symbol)

            quote = self._fetch_symbol(market, symbol, fallback_name)
            if quote is not None:
                quotes.append(quote)
            else:
                logger.warning(f"No data returned for {market} {symbol}")

            if self.delay and i != len(stocks) - 1:
                time.sleep(self.delay)
        return quotes

    def _fetch_symbol(self, market: str, symbol: str, fallback_name: str) -> Quote | None:
        for secid in self._secid_candidates(market, symbol):
            try:
                data = self._get(secid)
            except Exception as e:
                logger.error(f"eastmoney error for {market} {symbol} (secid={secid}): {e}")
                continue
            quote = self._parse(data, symbol, market, fallback_name)
            if quote is not None:
                return quote
        return None

    def _get(self, secid: str) -> dict:
        """GET one quote, retrying transient connection errors with backoff."""
        params = {"secid": secid, "fields": FIELDS, "fltt": 2, "invt": 2}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
                resp.raise_for_status()
                return (resp.json() or {}).get("data") or {}
            except (requests.ConnectionError, requests.HTTPError) as e:
                last_exc = e
                if attempt < self.max_retries:
                    wait = self.backoff * (attempt + 1)
                    logger.warning(
                        f"eastmoney transient error for {secid} "
                        f"(attempt {attempt + 1}/{self.max_retries + 1}), retry in {wait}s: {e}"
                    )
                    time.sleep(wait)
                else:
                    raise
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _parse(data: dict, symbol: str, market: str, fallback_name: str) -> Quote | None:
        price = data.get("f43")
        if price in (None, "", "-"):
            return None
        try:
            price_f = float(price)
            change_f = float(data.get("f170") or 0)
            volume_f = float(data.get("f47") or 0)
        except (TypeError, ValueError):
            logger.warning(f"Bad data for {symbol}: {data}")
            return None
        return Quote(
            symbol=symbol,
            name=data.get("f58") or fallback_name,
            market=market,
            price=price_f,
            change_pct=change_f,
            volume=volume_f,
        )

    @staticmethod
    def _secid_candidates(market: str, symbol: str) -> list[str]:
        """Eastmoney secid prefix by market. Returns candidates; first with data wins.

        A股: Shanghai (1.) codes start with 5/6/9 or 11/13; everything else is
             Shenzhen (0.) — e.g. 159xxx/300xxx/000xxx.
        港股: 116.
        美股: 105. = NASDAQ, 106. = NYSE; try NASDAQ first, fall back to NYSE.
        """
        sym = symbol.strip()
        if market == "A股":
            if sym.startswith(("5", "6", "9", "11", "13")):
                return [f"1.{sym}"]
            return [f"0.{sym}"]
        if market == "港股":
            return [f"116.{sym}"]
        if market == "美股":
            return [f"105.{sym}", f"106.{sym}"]
        # Unknown market: best-effort Shenzhen prefix.
        return [f"0.{sym}"]
