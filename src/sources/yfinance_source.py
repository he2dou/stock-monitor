import logging
import yfinance as yf
from src.models import Quote
from src.sources.base import DataSource

logger = logging.getLogger(__name__)

class YfinanceSource(DataSource):
    def fetch_quotes(self, stocks: list[dict]) -> list[Quote]:
        quotes: list[Quote] = []
        for s in stocks:
            if s.get("market") != "美股":
                continue
            try:
                ticker = yf.Ticker(s["symbol"])
                info = ticker.info
                price = info.get("regularMarketPrice")
                if price is None:
                    continue
                quotes.append(Quote(
                    symbol=s["symbol"],
                    name=info.get("shortName", s.get("name", s["symbol"])),
                    market="美股",
                    price=float(price),
                    change_pct=float(info.get("regularMarketChangePercent", 0)),
                    volume=float(info.get("regularMarketVolume", 0)),
                ))
            except Exception as e:
                logger.error(f"yfinance error for {s['symbol']}: {e}")
        return quotes
