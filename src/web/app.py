from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.web.deps import WEB_DIR, CONFIG_DIR, build_store, load_web_app_config
from src.web.auth import router as auth_router, _is_auth_enabled
from src.web.csrf_middleware import CSRFMiddleware
from src.web.auth_middleware import AuthMiddleware
from src.web.routes import (
    alerts, backtest, dashboard, markets, ops, portfolio, strategies, watchlist,
)


def create_app(store=None, config_dir: Path | None = None):
    config_dir = config_dir or CONFIG_DIR
    app_config = load_web_app_config(config_dir)
    if store is None:
        store = build_store(app_config)

    app = FastAPI(title="Stock Monitor Admin")
    app.state.store = store
    app.state.app_config = app_config
    app.state.config_dir = config_dir
    app.state.templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    app.state.monitor = None  # populated by main.py in embedded mode

    web_cfg = app_config.get("web", {}) or {}
    secret = web_cfg.get("secret_key") or secrets.token_hex(32)
    app.add_middleware(AuthMiddleware)
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=secret, same_site="lax", https_only=False, max_age=2592000)

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")
    app.include_router(auth_router)
    app.include_router(dashboard.router)
    app.include_router(watchlist.router)
    app.include_router(alerts.router)
    app.include_router(markets.router)
    app.include_router(portfolio.router)
    app.include_router(strategies.router)
    app.include_router(backtest.router)
    app.include_router(ops.router)
    return app
