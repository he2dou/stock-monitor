from __future__ import annotations

from pathlib import Path

from fastapi import Request

from src.config_loader import load_app_config
from src.trading_store import TradingStore

WEB_DIR = Path(__file__).parent
BASE_DIR = WEB_DIR.parent.parent
CONFIG_DIR = BASE_DIR / "config"

NAV = [
    ("概览", "/"),
    ("股票池", "/watchlist"),
    ("预警", "/alerts"),
    ("行情", "/markets"),
    ("持仓", "/portfolio"),
    ("策略", "/strategies"),
    ("回测", "/backtest"),
    ("运维", "/ops"),
]


def resolve_path(path_value: str) -> str:
    p = Path(path_value)
    if not p.is_absolute():
        p = BASE_DIR / p
    return str(p)


def build_store(app_config: dict) -> TradingStore:
    paper = app_config.get("paper_trading", {}) or {}
    return TradingStore(resolve_path(paper.get("db_path", "data/trading.sqlite3")))


def load_web_app_config(config_dir: Path | None = None) -> dict:
    return load_app_config(str((config_dir or CONFIG_DIR) / "config.yaml"))


def get_store(request: Request) -> TradingStore:
    return request.app.state.store


def get_app_config(request: Request) -> dict:
    return request.app.state.app_config


def render(request: Request, name: str, active: str, status_code: int = 200, **extra):
    ctx = {"nav": NAV, "active": active}
    ctx.update(extra)
    return request.app.state.templates.TemplateResponse(request, name, ctx, status_code=status_code)


def render_plain(request: Request, name: str, status_code: int = 200, **extra):
    return request.app.state.templates.TemplateResponse(request, name, extra, status_code=status_code)


def pop_flash(request: Request) -> str | None:
    try:
        return request.session.pop("flash", None)
    except Exception:
        return None


def set_flash(request: Request, message: str) -> None:
    try:
        request.session["flash"] = message
    except Exception:
        pass
