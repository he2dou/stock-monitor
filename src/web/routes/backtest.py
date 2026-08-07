from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, resolve_path, set_flash
from src.backtest import run_backtest

router = APIRouter(prefix="/backtest", dependencies=[Depends(ensure_login)])

_TRUTHY = {"1", "on", "true", "yes"}


def _default_date_range():
    today = date.today()
    return {
        "bt_start": (today - timedelta(days=365 * 3)).isoformat(),
        "bt_end": today.isoformat(),
    }


def _strategy_ids(request: Request) -> list[str]:
    """Unique strategy template IDs for the dropdown."""
    strategies = get_store(request).load_strategies()
    return sorted({s.get("id") for s in strategies if s.get("id")})


def _watchlist_symbols(request: Request) -> list[str]:
    """Watchlist stock codes for the symbol dropdown (same source as alerts)."""
    return [s["symbol"] for s in get_store(request).load_watchlist(include_disabled=False)]


def _history(request: Request, limit: int = 50):
    return get_store(request).load_backtest_runs(limit)


@router.get("")
async def page(request: Request):
    return render(
        request, "backtest.html", "回测",
        strategy_ids=_strategy_ids(request), symbols=_watchlist_symbols(request),
        result=None, trades=None, detail=None, history=_history(request),
        flash=pop_flash(request), **_default_date_range(),
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
        strategy_ids=_strategy_ids(request), symbols=_watchlist_symbols(request),
        result=None, trades=None, detail={"run": run, "summary": parsed},
        history=_history(request), flash=pop_flash(request),
        **_default_date_range(),
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
    source: str = Form("daily-bars"),
    next_bar: str = Form("0"),
    apply_costs: str = Form("0"),
    warmup_days: str = Form("0"),
    fx_rates_json: str = Form(""),
):
    store = get_store(request)
    if not strategy_id.strip() or not symbol.strip():
        set_flash(request, "请选择策略和代码后再运行回测")
        return RedirectResponse("/backtest", status_code=303)
    strategies = store.load_runtime_strategies()
    app_config = request.app.state.app_config
    paper = app_config.get("paper_trading", {}) or {}
    accounts = paper.get("accounts") or {"CNY": 100000, "HKD": 100000, "USD": 50000}
    db_path = resolve_path(paper.get("db_path", "data/trading.sqlite3"))
    strategy_ids = [strategy_id] if strategy_id else None
    symbols = [symbol] if symbol else None

    warmup = int(warmup_days) if warmup_days.strip().lstrip("-").isdigit() else 0
    fx_rates = None
    if fx_rates_json.strip():
        try:
            fx_rates = json.loads(fx_rates_json)
            fx_rates = {k: float(v) for k, v in fx_rates.items()}
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    summary = run_backtest(
        db_path, strategies, accounts, start or None, end or None,
        source=source, symbols=symbols, strategy_ids=strategy_ids,
        next_bar_execution=next_bar.lower() in _TRUTHY,
        apply_costs=apply_costs.lower() in _TRUTHY,
        warmup_days=warmup, fx_rates=fx_rates,
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
    defaults = _default_date_range()
    defaults["bt_start"] = start or defaults["bt_start"]
    defaults["bt_end"] = end or defaults["bt_end"]
    return render(
        request, "backtest.html", "回测",
        strategy_ids=_strategy_ids(request), symbols=_watchlist_symbols(request),
        result=summary, trades=trades, detail=None, history=_history(request),
        flash=None, **defaults,
    )


@router.post("/delete")
async def delete_run(request: Request, run_id: int = Form(...)):
    get_store(request).delete_backtest_run(run_id)
    set_flash(request, "回测记录已删除")
    return RedirectResponse("/backtest", status_code=303)
