from __future__ import annotations

from pathlib import Path

from fastapi import Request

from src.config_loader import load_app_config
from src.trading_store import TradingStore

WEB_DIR = Path(__file__).parent
BASE_DIR = WEB_DIR.parent.parent
CONFIG_DIR = BASE_DIR / "config"

NAV = [
    ("监控", [
        ("概览", "/", "dashboard"),
        ("股票池", "/watchlist", "eye"),
        ("预警", "/alerts", "bell"),
        ("行情", "/markets", "trending"),
    ]),
    ("交易", [
        ("持仓", "/portfolio", "briefcase"),
        ("委托", "/orders", "list"),
        ("策略", "/strategies", "target"),
        ("回测", "/backtest", "history"),
    ]),
    ("系统", [
        ("运维", "/ops", "terminal"),
    ]),
]




# Lucide-style SVG icon paths keyed by name (used in base.html nav)
ICONS = {
    "dashboard": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    "eye": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
    "bell": '<path d="M10.268 21a2 2 0 0 0 3.464 0"/><path d="M3.262 15.326A1 1 0 0 0 4 17h16a1 1 0 0 0 .74-1.673C19.41 13.956 18 12.499 18 8A6 6 0 0 0 6 8c0 4.499-1.411 5.956-2.738 7.326"/>',
    "trending": '<polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/>',
    "briefcase": '<rect width="20" height="14" x="2" y="7" rx="2" ry="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "target": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
    "list": '<line x1="8" x2="21" y1="6" y2="6"/><line x1="8" x2="21" y1="12" y2="12"/><line x1="8" x2="21" y1="18" y2="18"/><line x1="3" x2="3.01" y1="6" y2="6"/><line x1="3" x2="3.01" y1="12" y2="12"/><line x1="3" x2="3.01" y1="18" y2="18"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/>',
}




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
    from src.web.auth import _get_csrf_token, _is_auth_enabled
    ctx = {"nav": NAV, "icons": ICONS, "active": active}
    ctx["csrf_token"] = _get_csrf_token(request)
    ctx["auth_enabled"] = _is_auth_enabled(request)
    ctx["current_user"] = request.session.get("display_name") or request.session.get("user") or ""
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
