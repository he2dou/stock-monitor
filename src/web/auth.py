from __future__ import annotations

import hmac

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.web.deps import render_plain

router = APIRouter()


def _web_cfg(request: Request) -> dict:
    return request.app.state.app_config.get("web", {}) or {}


async def ensure_login(request: Request) -> None:
    """Reject unauthenticated requests when an admin password is configured.

    With no password set the backend runs open (local dev convenience).
    """
    password = _web_cfg(request).get("admin_password", "")
    if not password:
        return
    if not request.session.get("user"):
        raise HTTPException(status_code=302, headers={"Location": "/login"})


@router.get("/login")
async def login_page(request: Request):
    if _web_cfg(request).get("admin_password", ""):
        return render_plain(request, "login.html")
    return RedirectResponse("/", status_code=303)


@router.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    expected = _web_cfg(request).get("admin_password", "")
    if expected and hmac.compare_digest(password, expected):
        request.session["user"] = "admin"
        return RedirectResponse("/", status_code=303)
    return render_plain(request, "login.html", error="密码错误", status_code=401)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
