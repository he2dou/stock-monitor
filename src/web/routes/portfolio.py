from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render
from src.web import queries

router = APIRouter(prefix="/portfolio", dependencies=[Depends(ensure_login)])


@router.get("")
async def portfolio_page(request: Request):
    store = get_store(request)
    return render(
        request, "portfolio.html", "持仓",
        balances=queries.account_balances(store),
        positions=queries.positions(store, include_zero=True),
        orders=queries.orders(store, limit=100),
        fills=queries.fills(store, limit=100),
        signals=queries.signals(store, limit=50),
        flash=pop_flash(request),
    )
