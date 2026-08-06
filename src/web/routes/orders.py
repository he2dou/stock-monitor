from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render
from src.web import queries

router = APIRouter(prefix="/orders", dependencies=[Depends(ensure_login)])


@router.get("")
async def orders_page(request: Request):
    store = get_store(request)
    return render(
        request, "orders.html", "订单",
        orders=queries.orders(store, limit=200),
        fills=queries.fills(store, limit=200),
        signals=queries.signals(store, limit=100),
        flash=pop_flash(request),
    )
