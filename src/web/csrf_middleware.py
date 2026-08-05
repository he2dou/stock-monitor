from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.web.auth import _get_csrf_token, _is_auth_enabled, _get_users


class CSRFMiddleware(BaseHTTPMiddleware):
    """Validate CSRF tokens on POST/PUT/DELETE requests when auth is enabled.

    Reads the form body, checks the csrf_token field, then recreates the
    request body so downstream handlers can still use Form(...) parameters.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE"):
            # Only enforce CSRF when auth is enabled
            try:
                auth_enabled = _is_auth_enabled(request)
            except Exception:
                auth_enabled = False

            if auth_enabled:
                # Read body as bytes then reconstruct for downstream
                body = await request.body()
                # Temporarily reset the body
                async def receive():
                    return {"type": "http.request", "body": body, "more_body": False}
                request._receive = receive

                # Parse form to extract csrf_token
                form = await request.form()
                submitted = form.get("csrf_token", "")

                # Get token from session
                expected = _get_csrf_token(request)

                if not submitted or not hmac.compare_digest(submitted, expected):
                    return JSONResponse(
                        {"detail": "CSRF token mismatch"},
                        status_code=403,
                    )

                # Reset body again so handlers can re-read it
                async def receive2():
                    return {"type": "http.request", "body": body, "more_body": False}
                request._receive = receive2

        return await call_next(request)
