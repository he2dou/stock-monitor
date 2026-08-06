from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash
from src.web import queries

router = APIRouter(prefix="/portfolio", dependencies=[Depends(ensure_login)])

_MARKETS = ["A股", "港股", "美股"]
_CURRENCIES = ["CNY", "HKD", "USD"]


@router.get("")
async def portfolio_page(request: Request):
    store = get_store(request)
    return render(
        request, "portfolio.html", "持仓",
        balances=queries.account_balances(store),
        positions=queries.positions(store, include_zero=True),
        flash=pop_flash(request),
    )


@router.post("/add")
async def add_position(
    request: Request,
    market: str = Form(...),
    symbol: str = Form(...),
    name: str = Form(...),
    currency: str = Form(...),
    quantity: int = Form(...),
    avg_cost: float = Form(...),
    realized_pnl: float = Form(0.0),
):
    store = get_store(request)
    symbol = symbol.strip()
    if symbol and market in _MARKETS and currency in _CURRENCIES:
        store.upsert_position(market, symbol, name.strip(), currency, quantity, avg_cost, realized_pnl)
        set_flash(request, f"已新增 {symbol}")
    return RedirectResponse("/portfolio", status_code=303)


@router.post("/edit")
async def edit_position(
    request: Request,
    market: str = Form(...),
    old_symbol: str = Form(...),
    symbol: str = Form(...),
    name: str = Form(...),
    currency: str = Form(...),
    quantity: int = Form(...),
    avg_cost: float = Form(...),
    realized_pnl: float = Form(0.0),
):
    store = get_store(request)
    symbol = symbol.strip()
    old_symbol = old_symbol.strip()
    if market in _MARKETS and currency in _CURRENCIES:
        if symbol != old_symbol:
            store.delete_position(market, old_symbol)
        store.upsert_position(market, symbol, name.strip(), currency, quantity, avg_cost, realized_pnl)
        set_flash(request, f"已保存 {symbol}")
    return RedirectResponse("/portfolio", status_code=303)


@router.post("/delete")
async def delete_position(
    request: Request,
    market: str = Form(...),
    symbol: str = Form(...),
):
    store = get_store(request)
    deleted = store.delete_position(market, symbol.strip())
    set_flash(request, f"已删除 {symbol}" if deleted else f"{symbol} 不存在")
    return RedirectResponse("/portfolio", status_code=303)
