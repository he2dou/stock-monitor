from __future__ import annotations

import json
import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import get_store, pop_flash, render, set_flash
from src.strategy_engine import parse_strategies, StrategyConfigError

router = APIRouter(prefix="/strategies", dependencies=[Depends(ensure_login)])


def _path(request: Request):
    return request.app.state.config_dir / "strategies.yaml"


def _page_data(request: Request):
    store = get_store(request)
    strategies = store.load_strategies()
    bindings_by_strategy = {}
    for binding in store.load_strategy_bindings():
        bindings_by_strategy.setdefault(binding["strategy_id"], []).append(binding)
    for strategy in strategies:
        strategy["bindings"] = bindings_by_strategy.get(strategy.get("id"), [])
    return strategies, store.load_watchlist(include_disabled=True)


@router.get("")
async def page(request: Request):
    strategies, stocks = _page_data(request)
    export_strategies = []
    for strategy in strategies:
        item = dict(strategy)
        item.pop("bindings", None)
        export_strategies.append(item)
    content = yaml.safe_dump({"strategies": export_strategies}, allow_unicode=True, sort_keys=False)
    return render(request, "strategies.html", "策略", content=content,
                  strategies=strategies, stocks=stocks, flash=pop_flash(request))


async def _save_item(request: Request, item: dict, message: str):
    try:
        if not isinstance(item, dict):
            raise ValueError("策略必须是 JSON 对象")
        item.pop("bindings", None)
        parse_strategies([item])
        get_store(request).upsert_strategy(item)
    except Exception as exc:
        set_flash(request, f"策略保存失败：{exc}")
        return RedirectResponse("/strategies", status_code=303)
    set_flash(request, message)
    return RedirectResponse("/strategies", status_code=303)


@router.post("/create")
async def create(
    request: Request,
    strategy_id: str = Form(...),
    strategy_type: str = Form("threshold"),
    action: str = Form("buy"),
    enabled: str = Form("1"),
    trigger_field: str = Form(""),
    trigger_op: str = Form(""),
    trigger_value: str = Form(""),
    sizing_amount: str = Form("1000"),
    sizing_currency: str = Form("USD"),
    sizing_lot_size: str = Form("1"),
    extra_config: str = Form(""),
):
    item = {
        "id": strategy_id.strip(),
        "type": strategy_type,
        "action": action,
        "enabled": enabled in {"1", "on", "true", "yes"},
        "sizing": {
            "type": "fixed_amount",
            "amount": float(sizing_amount),
            "currency": sizing_currency.strip() or "USD",
            "lot_size": int(float(sizing_lot_size or 1)),
        },
        "constraints": {"cooldown_minutes": 0},
    }
    if strategy_type == "threshold":
        if trigger_field and trigger_op and trigger_value:
            item["trigger"] = {
                "field": trigger_field,
                "op": trigger_op,
                "value": float(trigger_value),
            }
    extra = extra_config.strip()
    if extra:
        parsed = json.loads(extra)
        if isinstance(parsed, dict):
            item.update(parsed)
    return await _save_item(request, item, "策略已新增")


@router.post("/edit")
async def edit(request: Request, content: str = Form(...)):
    try:
        item = json.loads(content)
    except json.JSONDecodeError as exc:
        set_flash(request, f"策略保存失败：{exc}")
        return RedirectResponse("/strategies", status_code=303)
    return await _save_item(request, item, "策略已保存")


@router.post("/bind")
async def bind(request: Request, strategy_id: str = Form(...), symbol: str = Form(...)):
    try:
        store = get_store(request)
        if not any(s["id"] == strategy_id for s in store.load_strategies()):
            raise ValueError("策略不存在")
        if not any(s["symbol"] == symbol for s in store.load_watchlist(include_disabled=True)):
            raise ValueError("股票不在股票池中")
        store.upsert_strategy_binding(strategy_id, symbol, True)
        set_flash(request, "股票已绑定到策略")
    except Exception as exc:
        set_flash(request, f"绑定失败：{exc}")
    return RedirectResponse("/strategies", status_code=303)


@router.post("/unbind")
async def unbind(request: Request, strategy_id: str = Form(...), symbol: str = Form(...)):
    get_store(request).delete_strategy_binding(strategy_id, symbol)
    set_flash(request, "股票已解除绑定")
    return RedirectResponse("/strategies", status_code=303)


@router.post("/delete")
async def delete(request: Request, strategy_id: str = Form(...)):
    get_store(request).delete_strategy(strategy_id.strip())
    set_flash(request, "策略已删除")
    return RedirectResponse("/strategies", status_code=303)


@router.post("/save")
async def save(request: Request, content: str = Form(...)):
    path = _path(request)
    try:
        data = yaml.safe_load(content) or {}
        raw = data.get("strategies", []) if isinstance(data, dict) else []
        parse_strategies(raw if isinstance(raw, list) else [])
    except (yaml.YAMLError, StrategyConfigError, ValueError, TypeError) as exc:
        return render(request, "strategies.html", "策略", content=content,
                      strategies=[], stocks=[], error=f"策略解析失败：{exc}", status_code=400)
    path.write_text(content, encoding="utf-8")
    store = get_store(request)
    imported_ids = {item.get("id") for item in raw}
    existing_ids = {item.get("id") for item in store.load_strategies()}
    for item in raw:
        store.upsert_strategy(item)
    for strategy_id in existing_ids - imported_ids:
        store.delete_strategy(strategy_id)
    set_flash(request, "策略已保存，下一轮自动生效")
    return RedirectResponse("/strategies", status_code=303)