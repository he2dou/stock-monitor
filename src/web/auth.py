from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from src.web.deps import render_plain

router = APIRouter()

# --- Rate limiting state (in-memory, per-IP) ---
_login_attempts: dict[str, list[float]] = {}
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 300  # 5 min


def _web_cfg(request: Request) -> dict:
    return request.app.state.app_config.get("web", {}) or {}


# --- Password hashing (PBKDF2-HMAC-SHA256, no external deps) ---

def hash_password(password: str) -> str:
    """Return 'pbkdf2$<iters>$<salt_hex>$<hash_hex>'."""
    salt = os.urandom(16)
    iters = 200_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verify a password against a stored hash string.

    Supports plain-text (legacy config upgrade) and pbkdf2 format.
    """
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        parts = stored.split("$")
        if len(parts) != 4:
            return False
        iters = int(parts[1])
        salt = bytes.fromhex(parts[2])
        expected = bytes.fromhex(parts[3])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iters)
        return hmac.compare_digest(dk, expected)
    # Legacy: plain text comparison (for backward compat)
    return hmac.compare_digest(password, stored)


def _get_users(request: Request) -> dict[str, dict]:
    """Return dict of username -> user config from web config.

    Supports two config styles:
    1. web.users: list of {username, password, password_hash}
    2. web.admin_password: legacy single-password (username 'admin')
    """
    cfg = _web_cfg(request)
    users: dict[str, dict] = {}
    user_list = cfg.get("users")
    if user_list and isinstance(user_list, list):
        for u in user_list:
            uname = u.get("username", "")
            if not uname:
                continue
            users[uname] = {
                "username": uname,
                "password": u.get("password", ""),
                "password_hash": u.get("password_hash", ""),
                "display_name": u.get("display_name", uname),
            }
    # Legacy single-password mode
    pwd = cfg.get("admin_password", "")
    if pwd and "admin" not in users:
        users["admin"] = {
            "username": "admin",
            "password": pwd,
            "password_hash": "",
            "display_name": "admin",
        }
    return users


def _is_auth_enabled(request: Request) -> bool:
    """Auth is enabled when there are users with passwords configured."""
    return bool(_get_users(request))


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds)."""
    now = time.time()
    attempts = _login_attempts.get(ip, [])
    # Prune old attempts outside lockout window
    attempts = [t for t in attempts if now - t < _LOCKOUT_SECONDS]
    _login_attempts[ip] = attempts
    if len(attempts) >= _MAX_ATTEMPTS:
        retry = int(_LOCKOUT_SECONDS - (now - attempts[0]))
        return False, max(retry, 0)
    return True, 0


def _record_failed_attempt(ip: str) -> None:
    _login_attempts.setdefault(ip, []).append(time.time())


def _clear_attempts(ip: str) -> None:
    _login_attempts.pop(ip, None)


# --- CSRF token ---

def _get_csrf_token(request: Request) -> str:
    token = request.session.get("_csrf")
    if not token:
        import secrets as _s
        token = _s.token_hex(16)
        request.session["_csrf"] = token
    return token


def csrf_protect(request: Request) -> None:
    """Dependency to validate CSRF token on POST forms."""
    token = _get_csrf_token(request)
    form_token = None
    # Try form data first, then header
    if request.method == "POST":
        try:
            form_data = yield  # noqa
        except Exception:
            form_token = request.headers.get("X-CSRF-Token", "")
    if form_token and not hmac.compare_digest(form_token, token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


# --- Auth dependency ---

async def ensure_login(request: Request) -> None:
    """Reject unauthenticated requests when auth is enabled.

    With no password set the backend runs open (local dev convenience).
    """
    users = _get_users(request)
    if not users:
        return
    if not request.session.get("user"):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    # Verify the user still exists (handles config changes while logged in)
    username = request.session.get("user")
    if username not in users:
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login"})


def verify_csrf_token(request: Request, token: str) -> bool:
    """Verify a CSRF token. Returns True if valid."""
    expected = _get_csrf_token(request)
    return bool(token) and hmac.compare_digest(token, expected)


async def ensure_csrf(request: Request) -> None:
    """No-op placeholder for router-level dependency.
    CSRF is validated per-handler via verify_csrf_token().
    """
    return


def require_auth_enabled(request: Request) -> bool:
    return _is_auth_enabled(request)


# --- CSRF helper for templates ---

def get_csrf_token_sync(request: Request) -> str:
    return _get_csrf_token(request)


# --- Routes ---

@router.get("/login")
async def login_page(request: Request):
    if not _is_auth_enabled(request):
        return RedirectResponse("/", status_code=303)
    csrf_token = _get_csrf_token(request)
    return render_plain(
        request, "login.html",
        csrf_token=csrf_token, error=None,
    )


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form("admin"),
    password: str = Form(...),
    remember: str = Form("0"),
    csrf_token: str = Form(...),
):
    if not _is_auth_enabled(request):
        return RedirectResponse("/", status_code=303)

    client_ip = request.client.host if request.client else "unknown"
    allowed, retry = _check_rate_limit(client_ip)
    if not allowed:
        msg = f"\u767b\u5f55\u5931\u8d25\u6b21\u6570\u8fc7\u591a\uff0c\u8bf7 {retry} \u79d2\u540e\u91cd\u8bd5"
        return render_plain(
            request, "login.html",
            error=msg, csrf_token=_get_csrf_token(request), status_code=429,
        )

    # CSRF check
    expected_csrf = _get_csrf_token(request)
    if not hmac.compare_digest(csrf_token, expected_csrf):
        _record_failed_attempt(client_ip)
        return render_plain(
            request, "login.html",
            error="\u5b89\u5168\u9a8c\u8bc1\u5931\u8d25\uff0c\u8bf7\u5237\u65b0\u9875\u9762",
            csrf_token=_get_csrf_token(request), status_code=403,
        )

    users = _get_users(request)
    user = users.get(username)
    if user is None:
        _record_failed_attempt(client_ip)
        return render_plain(
            request, "login.html",
            error="\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef",
            csrf_token=_get_csrf_token(request), status_code=401,
        )

    stored = user.get("password_hash") or user.get("password", "")
    if not verify_password(password, stored):
        _record_failed_attempt(client_ip)
        return render_plain(
            request, "login.html",
            error="\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef",
            csrf_token=_get_csrf_token(request), status_code=401,
        )

    # Success
    _clear_attempts(client_ip)
    request.session["user"] = username
    request.session["display_name"] = user.get("display_name", username)
    if remember == "1":
        request.session["remember"] = "1"
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request, csrf_token: str = Form("")):
    if _is_auth_enabled(request):
        expected_csrf = _get_csrf_token(request)
        if not hmac.compare_digest(csrf_token, expected_csrf):
            raise HTTPException(status_code=403, detail="CSRF token mismatch")
    request.session.clear()
    if _is_auth_enabled(request):
        return RedirectResponse("/login", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.get("/change-password")
async def change_password_page(request: Request):
    """Allow a logged-in user to change their password."""
    if not _is_auth_enabled(request):
        return RedirectResponse("/", status_code=303)
    if not request.session.get("user"):
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return render_plain(
        request, "change_password.html",
        csrf_token=_get_csrf_token(request), error=None, flash=None,
    )


@router.post("/change-password")
async def change_password_submit(
    request: Request,
    old_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
):
    if not _is_auth_enabled(request):
        return RedirectResponse("/", status_code=303)
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=302, headers={"Location": "/login"})

    expected_csrf = _get_csrf_token(request)
    if not hmac.compare_digest(csrf_token, expected_csrf):
        return render_plain(
            request, "change_password.html",
            error="\u5b89\u5168\u9a8c\u8bc1\u5931\u8d25\uff0c\u8bf7\u5237\u65b0\u9875\u9762",
            csrf_token=expected_csrf, flash=None, status_code=403,
        )

    users = _get_users(request)
    user = users.get(username)
    if user is None:
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login"})

    stored = user.get("password_hash") or user.get("password", "")
    if not verify_password(old_password, stored):
        return render_plain(
            request, "change_password.html",
            error="\u539f\u5bc6\u7801\u9519\u8bef",
            csrf_token=expected_csrf, flash=None, status_code=401,
        )

    if new_password != confirm_password:
        return render_plain(
            request, "change_password.html",
            error="\u4e24\u6b21\u8f93\u5165\u7684\u65b0\u5bc6\u7801\u4e0d\u4e00\u81f4",
            csrf_token=expected_csrf, flash=None, status_code=400,
        )

    if len(new_password) < 6:
        return render_plain(
            request, "change_password.html",
            error="\u5bc6\u7801\u81f3\u5c11\u9700 6 \u4e2a\u5b57\u7b26",
            csrf_token=expected_csrf, flash=None, status_code=400,
        )

    # Write new hash to config.yaml (persist the change)
    new_hash = hash_password(new_password)
    _persist_password(request, username, new_hash)
    _clear_attempts(request.client.host if request.client else "unknown")

    return render_plain(
        request, "change_password.html",
        error=None, flash="\u5bc6\u7801\u4fee\u6539\u6210\u529f",
        csrf_token=expected_csrf,
    )


def _persist_password(request: Request, username: str, password_hash: str) -> None:
    """Persist a new password hash to config.yaml on disk."""
    import yaml

    config_path = request.app.state.config_dir / "config.yaml"
    try:
        raw = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception:
        data = {}

    web_cfg = data.setdefault("web", {})
    if not web_cfg or not isinstance(web_cfg, dict):
        web_cfg = {}
        data["web"] = web_cfg

    users = web_cfg.get("users")
    if users is None:
        # Legacy mode: migrate admin_password to password_hash
        web_cfg["users"] = [
            {"username": "admin", "password_hash": password_hash}
        ]
    else:
        for u in users:
            if u.get("username") == username:
                u["password_hash"] = password_hash
                u.pop("password", None)
                break

    # Write back, preserving key order as much as possible
    try:
        config_path.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
    except Exception:
        pass  # Best-effort; don't crash on write failure
