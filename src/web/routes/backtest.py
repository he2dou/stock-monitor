from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, resolve_path, set_flash
from src.backtest import run_backtest

router = APIRouter(prefix="/backtest", dependencies=[Depends(ensure_login)])

_TRUTHY = {"1", "on", "true", "yes"}


def _strategy_ids(request: Request) -> list[str]:
    """Unique strategy template IDs for the dropdown."""
    strategies = get_store(request).load_strategies()
    return sorted({s.get("id") for s in strategies if s.get("id")})


def _history(request: Request, limit: int = 50):
    return get_store(request).load_backtest_runs(limit)


@router.get("")
async def page(request: Request):
    return render(
        request, "backtest.html", "回测",
        strategy_ids=_strategy_ids(request), result=None, trades=None,
        detail=None, history=_history(request), flash=pop_flash(request),
    )


@router.get("/detail/{run_id}")
async def detail(request: Request, run_id: int):
    store = get_store(request)
    run = store.get_backtest_run(run_id)
    if not run:
        set_flash(request, "回测记录不存在")
        return RedirectResponse("/backtest", status_code=303)
    parsed = None
    if run.get("summary_json"):
        try:
            parsed = json.loads(run["summary_json"])
        except (TypeError, json.JSONDecodeError):
            parsed = None
    return render(
        request, "backtest.html", "回测",
        strategy_ids=_strategy_ids(request), result=None, trades=None,
        detail={"run": run, "summary": parsed},
        history=_history(request), flash=pop_flash(request),
    )



@router.get("/api/detail/{run_id}")
async def api_detail(request: Request, run_id: int):
    store = get_store(request)
    run = store.get_backtest_run(run_id)
    if not run:
        return JSONResponse({"error": "记录不存在"}, status_code=404)
    parsed = None
    if run.get("summary_json"):
        try:
            parsed = json.loads(run["summary_json"])
        except (TypeError, json.JSONDecodeError):
            parsed = None
    return JSONResponse({"run": run, "summary": parsed})


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
    store = get_store(request)
    strategies = store.load_runtime_strategies()
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
    store.save_backtest_run(
        {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "start": summary.get("from") or start,
            "end": summary.get("to") or end,
            "source": source,
            "next_bar": next_bar.lower() in _TRUTHY,
            "apply_costs": apply_costs.lower() in _TRUTHY,
        },
        summary,
    )
    trades = (summary.get("trades") or [])[:50]
    return render(
        request, "backtest.html", "回测",
        strategy_ids=_strategy_ids(request), result=summary, trades=trades,
        detail=None, history=_history(request), flash=None,
    )


@router.post("/delete")
async def delete_run(request: Request, run_id: int = Form(...)):
    get_store(request).delete_backtest_run(run_id)
    set_flash(request, "回测记录已删除")
    return RedirectResponse("/backtest", status_code=303)
