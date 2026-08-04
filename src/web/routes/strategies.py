from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from src.web.auth import ensure_login
from src.web.deps import pop_flash, render, set_flash
from src.config_loader import load_strategies
from src.strategy_engine import parse_strategies, StrategyConfigError

router = APIRouter(prefix="/strategies", dependencies=[Depends(ensure_login)])


def _path(request: Request):
    return request.app.state.config_dir / "strategies.yaml"


@router.get("")
async def page(request: Request):
    path = _path(request)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    strategies = []
    if path.exists():
        try:
            strategies = load_strategies(str(path))
        except Exception:
            strategies = []
    return render(
        request, "strategies.html", "策略",
        content=content, strategies=strategies, flash=pop_flash(request),
    )


@router.post("/save")
async def save(request: Request, content: str = Form(...)):
    path = _path(request)
    try:
        data = yaml.safe_load(content) or {}
    except yaml.YAMLError as exc:
        return render(
            request, "strategies.html", "策略",
            content=content, strategies=[], error=f"YAML 解析失败：{exc}", status_code=400,
        )
    raw = data.get("strategies", []) if isinstance(data, dict) else []
    try:
        parse_strategies(raw if isinstance(raw, list) else [])
    except (StrategyConfigError, Exception) as exc:
        return render(
            request, "strategies.html", "策略",
            content=content, strategies=[], error=str(exc), status_code=400,
        )
    path.write_text(content, encoding="utf-8")
    set_flash(request, "策略已保存，下一轮自动生效")
    return RedirectResponse("/strategies", status_code=303)
