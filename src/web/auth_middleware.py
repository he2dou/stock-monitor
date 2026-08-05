from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse

from src.web.auth import _is_auth_enabled

# Paths that do not require authentication
_PUBLIC_PATHS = {"/login", "/static"}
_API_PREFIX = "/api/"


class AuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated users to the login page when auth is enabled."""

    async def dispatch(self, request: Request, call_next):
        try:
            auth_enabled = _is_auth_enabled(request)
        except Exception:
            auth_enabled = False

        if auth_enabled:
            path = request.url.path
            # Allow public paths (login, static files)
            if path in _PUBLIC_PATHS or path.startswith("/static/"):
                return await call_next(request)
            # Allow API paths (they return 401/403 on their own)
            if path.startswith(_API_PREFIX):
                return await call_next(request)
            # Allow login POST submission
            if path == "/login" and request.method == "POST":
                return await call_next(request)
            # Check if user is logged in
            if not request.session.get("user"):
                return RedirectResponse(url="/login", status_code=303)

        return await call_next(request)