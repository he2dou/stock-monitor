from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash
from src.web import queries
from src.service import ops as svc

router = APIRouter(prefix="/markets", dependencies=[Depends(ensure_login)])


def _sparkline(prices, width: int = 240, height: int = 48) -> str:
    if len(prices) < 2:
        return ""
    lo, hi = min(prices), max(prices)
    span = hi - lo or 1.0
    n = len(prices)
    pts = []
    for i, p in enumerate(prices):
        x = i / (n - 1) * width
        y = height - (p - lo) / span * height
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


@router.get("")
async def markets_page(request: Request):
    store = get_store(request)
    return render(
        request, "markets.html", "行情",
        quotes=queries.latest_quotes(store, limit=200),
        indices=queries.latest_indices(store),
        flash=pop_flash(request),
    )


@router.post("/update-snapshots")
async def update_snapshots(request: Request, target: str = Form(...)):
    if target not in {"stock", "index"}:
        return RedirectResponse("/markets", status_code=303)

    store = get_store(request)
    result = svc.update_snapshots(
        store, app_config=request.app.state.app_config, target=target, ignore_hours=True,
    )
    set_flash(request, f"已更新{target}快照：抓取 {result['fetched']}，保存 {result['saved']}")
    return RedirectResponse("/markets", status_code=303)

@router.get("/history/{symbol}")
async def history(request: Request, symbol: str):
    store = get_store(request)
    rows = list(reversed(queries.quote_history(store, symbol, limit=180)))
    prices = [r["price"] for r in rows]
    return render(
        request, "markets_history.html", "行情",
        symbol=symbol, history=rows, sparkline=_sparkline(prices), flash=None,
    )
