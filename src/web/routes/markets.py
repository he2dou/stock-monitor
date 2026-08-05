from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.web.auth import ensure_login, ensure_csrf
from src.web.deps import get_store, pop_flash, render
from src.web import queries

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


@router.get("/history/{symbol}")
async def history(request: Request, symbol: str):
    store = get_store(request)
    rows = list(reversed(queries.quote_history(store, symbol, limit=180)))
    prices = [r["price"] for r in rows]
    return render(
        request, "markets_history.html", "行情",
        symbol=symbol, history=rows, sparkline=_sparkline(prices), flash=None,
    )
