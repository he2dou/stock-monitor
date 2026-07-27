import yaml
from pathlib import Path

VALID_MARKETS = {"A股", "港股", "美股"}
VALID_FIELDS = {"price", "change_pct"}
VALID_OPS = {"above", "below"}

class ConfigError(Exception):
    pass

def load_watchlist(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Watchlist not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "stocks" not in data:
        raise ConfigError("watchlist.yaml missing 'stocks' key")
    stocks = data["stocks"]
    for s in stocks:
        if "symbol" not in s or "name" not in s or "market" not in s:
            raise ConfigError(f"Stock entry missing required fields: {s}")
        if s["market"] not in VALID_MARKETS:
            raise ConfigError(
                f"Invalid market '{s['market']}'. Must be one of {VALID_MARKETS}")
    return stocks

def load_alerts(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Alerts file not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data or "rules" not in data:
        return []
    rules = data["rules"]
    if rules is None:
        return []
    for r in rules:
        if r.get("field") not in VALID_FIELDS:
            raise ConfigError(f"Invalid field '{r.get('field')}'. Must be {VALID_FIELDS}")
        if r.get("op") not in VALID_OPS:
            raise ConfigError(f"Invalid op '{r.get('op')}'. Must be {VALID_OPS}")
        if "value" not in r or "symbol" not in r:
            raise ConfigError(f"Alert rule missing symbol/value: {r}")
    return rules
