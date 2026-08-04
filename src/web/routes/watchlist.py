from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash

router = APIRouter(prefix="/watchlist", dependencies=[Depends(ensure_login)])

_MARKETS = ["A股", "港股", "美股"]


def _truthy(value: str) -> bool:
    return str(value).lower() in ("1", "on", "true", "yes")


@router.get("")
async def list_page(request: Request):
    store = get_store(request)
    stocks = store.load_watchlist(include_disabled=True)
    return render(
        request, "watchlist.html", "股票池",
        stocks=stocks, markets=_MARKETS, flash=pop_flash(request),
    )


@router.post("/add")
async def add(
    request: Request,
    symbol: str = Form(...),
    name: str = Form(...),
    market: str = Form(...),
    enabled: str = Form("0"),
):
    store = get_store(request)
    symbol = symbol.strip()
    if symbol and market in _MARKETS:
        store.add_stock(symbol, name.strip(), market, enabled=_truthy(enabled))
        set_flash(request, f"已添加 {symbol}")
    return RedirectResponse("/watchlist", status_code=303)


@router.post("/{symbol}/toggle")
async def toggle(request: Request, symbol: str):
    store = get_store(request)
    stocks = {s["symbol"]: s for s in store.load_watchlist(include_disabled=True)}
    stock = stocks.get(symbol)
    if stock:
        new_state = not bool(stock["enabled"])
        store.set_stock_enabled(symbol, new_state)
        set_flash(request, f"{symbol} 已{'启用' if new_state else '停用'}")
    return RedirectResponse("/watchlist", status_code=303)


@router.post("/{symbol}/delete")
async def delete(request: Request, symbol: str):
    store = get_store(request)
    deleted = store.delete_stock(symbol)
    set_flash(request, f"已删除 {symbol}" if deleted else f"{symbol} 不存在")
    return RedirectResponse("/watchlist", status_code=303)
