from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash

router = APIRouter(prefix="/alerts", dependencies=[Depends(ensure_login)])

_FIELDS = ["price", "change_pct"]
_OPS = ["above", "below"]


@router.get("")
async def list_page(request: Request):
    store = get_store(request)
    rules = store.load_alert_rules(include_disabled=True)
    symbols = [s["symbol"] for s in store.load_watchlist(include_disabled=False)]
    return render(
        request, "alerts.html", "预警",
        rules=rules, fields=_FIELDS, ops=_OPS, symbols=symbols, flash=pop_flash(request),
    )


@router.post("/add")
async def add(
    request: Request,
    symbol: str = Form(...),
    field: str = Form(...),
    op: str = Form(...),
    value: float = Form(...),
    cooldown_seconds: int | None = Form(None),
):
    store = get_store(request)
    rule_id = store.add_alert_rule(
        symbol=symbol.strip(), field=field, op=op, value=value,
        enabled=True, cooldown_seconds=cooldown_seconds,
    )
    set_flash(request, f"已添加预警 {rule_id[:8]}")
    return RedirectResponse("/alerts", status_code=303)


@router.post("/{rule_id}/toggle")
async def toggle(request: Request, rule_id: str):
    store = get_store(request)
    rules = {r["rule_id"]: r for r in store.load_alert_rules(include_disabled=True)}
    rule = rules.get(rule_id)
    if rule:
        new_state = not bool(rule["enabled"])
        store.set_alert_rule_enabled(rule_id, new_state)
        set_flash(request, f"预警 {rule_id[:8]} 已{'启用' if new_state else '停用'}")
    return RedirectResponse("/alerts", status_code=303)


@router.post("/{rule_id}/delete")
async def delete(request: Request, rule_id: str):
    store = get_store(request)
    store.delete_alert_rule(rule_id)
    set_flash(request, "已删除预警")
    return RedirectResponse("/alerts", status_code=303)
