from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from src.web.auth import ensure_login
from src.web.deps import pop_flash, render, resolve_path
from src.config_loader import load_strategies
from src.backtest import run_backtest

router = APIRouter(prefix="/backtest", dependencies=[Depends(ensure_login)])

_TRUTHY = {"1", "on", "true", "yes"}


def _strategies(request: Request) -> list[dict]:
    path = request.app.state.config_dir / "strategies.yaml"
    return load_strategies(str(path)) if path.exists() else []


@router.get("")
async def page(request: Request):
    strategies = _strategies(request)
    ids = [s.get("id") for s in strategies if s.get("id")]
    return render(
        request, "backtest.html", "回测",
        strategy_ids=ids, result=None, trades=None, flash=pop_flash(request),
    )


@router.post("/run")
async def run(
    request: Request,
    strategy_id: str = Form(""),
    symbol: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
    source: str = Form("quote-snapshots"),
    next_bar: str = Form("0"),
    apply_costs: str = Form("0"),
):
    strategies = _strategies(request)
    app_config = request.app.state.app_config
    paper = app_config.get("paper_trading", {}) or {}
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    db_path = resolve_path(paper.get("db_path", "data/trading.sqlite3"))
    strategy_ids = [strategy_id] if strategy_id else None
    symbols = [symbol] if symbol else None
    summary = run_backtest(
        db_path, strategies, accounts, start or None, end or None,
        source=source, symbols=symbols, strategy_ids=strategy_ids,
        next_bar_execution=next_bar.lower() in _TRUTHY,
        apply_costs=apply_costs.lower() in _TRUTHY,
    )
    summary.pop("equity_curve", None)
    ids = [s.get("id") for s in strategies if s.get("id")]
    trades = (summary.get("trades") or [])[:50]
    return render(
        request, "backtest.html", "回测",
        strategy_ids=ids, result=summary, trades=trades, flash=None,
    )
