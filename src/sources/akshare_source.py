import logging
import akshare as ak
import pandas as pd
from src.models import Quote
from src.sources.base import DataSource

logger = logging.getLogger(__name__)

class AkshareSource(DataSource):
    COLUMN_MAP = {
        "代码": "symbol", "名称": "name",
        "最新价": "price", "涨跌幅": "change_pct", "成交量": "volume",
    }

    def fetch_quotes(self, stocks: list[dict]) -> list[Quote]:
        results: list[Quote] = []
        by_market: dict[str, list[dict]] = {"A股": [], "港股": [], "美股": []}
        for s in stocks:
            market = s.get("market", "A股")
            if market in by_market:
                by_market[market].append(s)
        for market, stock_list in by_market.items():
            if not stock_list:
                continue
            try:
                df = self._fetch_market(market)
                if df is None or df.empty:
                    logger.warning(f"No data returned for {market}")
                    continue
                quotes = self._match_stocks(df, stock_list, market)
                results.extend(quotes)
            except Exception as e:
                logger.error(f"Failed to fetch {market}: {e}", exc_info=True)
        return results

    def _fetch_market(self, market: str) -> pd.DataFrame | None:
        try:
            if market == "A股":
                return ak.stock_zh_a_spot_em()
            elif market == "港股":
                return ak.stock_hk_spot_em()
            elif market == "美股":
                return ak.stock_us_spot_em()
        except Exception as e:
            logger.error(f"akshare API error for {market}: {e}")
            return None

    def _match_stocks(self, df: pd.DataFrame, stocks: list[dict], market: str) -> list[Quote]:
        quotes: list[Quote] = []
        for s in stocks:
            symbol = str(s["symbol"])
            row = df[df["代码"].astype(str) == symbol]
            if row.empty:
                logger.warning(f"Symbol {symbol} not found in {market} data")
                continue
            row = row.iloc[0]
            try:
                price = float(row.get("最新价", 0))
                change_pct = float(row.get("涨跌幅", 0))
                volume = float(row.get("成交量", 0))
            except (ValueError, TypeError):
                logger.warning(f"Bad data for {symbol}: {row.to_dict()}")
                continue
            quotes.append(Quote(
                symbol=symbol, name=s.get("name", symbol),
                market=market, price=price,
                change_pct=change_pct, volume=volume,
            ))
        return quotes
