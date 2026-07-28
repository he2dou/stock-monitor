from __future__ import annotations

from datetime import datetime, time, timedelta

from src.market_hours import CST

DEFAULT_MARKET_INDICES: list[dict] = [
    {
        "symbol": "000001",
        "name": "上证指数",
        "market": "A股",
        "tencent_symbol": "sh000001",
        "sina_symbol": "sh000001",
    },
    {
        "symbol": "399001",
        "name": "深证成指",
        "market": "A股",
        "tencent_symbol": "sz399001",
        "sina_symbol": "sz399001",
    },
    {
        "symbol": "399006",
        "name": "创业板指",
        "market": "A股",
        "tencent_symbol": "sz399006",
        "sina_symbol": "sz399006",
    },
    {
        "symbol": "HSI",
        "name": "恒生指数",
        "market": "港股",
        "tencent_symbol": "hkHSI",
        "sina_symbol": "hkHSI",
    },
    {
        "symbol": "HSCEI",
        "name": "恒生中国企业指数",
        "market": "港股",
        "tencent_symbol": "hkHSCEI",
        "sina_symbol": "hkHSCEI",
    },
    {
        "symbol": "HSTECH",
        "name": "恒生科技指数",
        "market": "港股",
        "tencent_symbol": "hkHSTECH",
        "sina_symbol": "hkHSTECH",
    },
    {
        "symbol": ".DJI",
        "name": "道琼斯工业平均指数",
        "market": "美股",
        "tencent_symbol": "usDJI",
        "sina_symbol": "gb_dji",
    },
    {
        "symbol": ".IXIC",
        "name": "纳斯达克综合指数",
        "market": "美股",
        "tencent_symbol": "usIXIC",
        "sina_symbol": "gb_ixic",
    },
    {
        "symbol": ".INX",
        "name": "标普500指数",
        "market": "美股",
        "tencent_symbol": "usINX",
        "sina_symbol": "gb_inx",
    },
]


def market_indices_enabled(config: dict | None) -> bool:
    cfg = (config or {}).get("market_indices", {}) or {}
    return bool(cfg.get("enabled", True))


def load_market_indices(config: dict | None) -> list[dict]:
    cfg = (config or {}).get("market_indices", {}) or {}
    if not market_indices_enabled(config):
        return []
    indices = cfg.get("indices") or DEFAULT_MARKET_INDICES
    return [_validate_index(item) for item in indices]


def _validate_index(item: dict) -> dict:
    required = ("symbol", "name", "market")
    missing = [key for key in required if key not in item]
    if missing:
        raise ValueError(f"market index missing required fields {missing}: {item}")
    return dict(item)


def market_snapshot_date(market: str, when: datetime | None = None) -> str:
    """Return the market-local trading date used for one snapshot per day."""
    current = (when or datetime.now(CST)).astimezone(CST)
    if market == "美股" and time(0, 0) <= current.time() <= time(12, 0):
        return (current.date() - timedelta(days=1)).isoformat()
    return current.date().isoformat()