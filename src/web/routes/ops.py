from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash, BASE_DIR
from src.service import ops as svc

router = APIRouter(prefix="/ops", dependencies=[Depends(ensure_login)])


def _log_path():
    return BASE_DIR / "logs" / "stock_monitor.log"


@router.get("")
async def page(request: Request):
    log = _log_path()
    tail = ""
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        tail = "\n".join(lines[-200:])
    monitor = getattr(request.app.state, "monitor", None)
    today = date.today()
    return render(
        request, "ops.html", "运维",
        log_tail=tail, has_monitor=monitor is not None, flash=pop_flash(request),
        kline_start=(today - timedelta(days=365 * 3)).isoformat(),
        kline_end=today.isoformat(),
    )


@router.post("/update-snapshots")
async def update_snapshots(request: Request, target: str = Form(...)):
    store = get_store(request)
    result = svc.update_snapshots(
        store, app_config=request.app.state.app_config, target=target, ignore_hours=True,
    )
    set_flash(request, f"已更新{target}快照：抓取 {result['fetched']}，保存 {result['saved']}")
    return RedirectResponse("/ops", status_code=303)


@router.post("/fetch-kline")
async def fetch_kline(
    request: Request,
    symbol: str = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    provider: str = Form("akshare"),
    market: str = Form("美股"),
):
    store = get_store(request)
    source = svc.make_kline_source(provider, timeout=20)
    try:
        result = svc.fetch_kline(
            store, symbol=symbol.strip(), name=symbol.strip(), market=market,
            start=start or None, end=end or None, years=3, source=source,
            provider=provider,
        )
    except ValueError as exc:
        set_flash(request, f"抓取失败：{exc}")
        return RedirectResponse("/ops", status_code=303)
    set_flash(request, f"已抓取 {symbol} K线 {result['fetched']} 根")
    return RedirectResponse("/ops", status_code=303)


@router.post("/run-now")
async def run_now(request: Request):
    monitor = getattr(request.app.state, "monitor", None)
    if monitor is not None:
        monitor.run_once()
        set_flash(request, "已触发一次监控轮询")
    else:
        set_flash(request, "监控未运行（独立 Web 模式）")
    return RedirectResponse("/ops", status_code=303)