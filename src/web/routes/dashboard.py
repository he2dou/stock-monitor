from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.web.auth import ensure_login, ensure_csrf
from src.web.deps import get_store, pop_flash, render
from src.web import queries

router = APIRouter(dependencies=[Depends(ensure_login)])


@router.get("/")
async def overview(request: Request):
    store = get_store(request)
    return render(
        request, "dashboard.html", "概览",
        counts=queries.counts(store),
        balances=queries.account_balances(store),
        positions=queries.positions(store),
        quotes=queries.latest_quotes(store, limit=20),
        indices=queries.latest_indices(store),
        flash=pop_flash(request),
    )
