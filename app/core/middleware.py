from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import (
    CSRF_COOKIE,
    FLASH_COOKIE,
    IS_PRODUCTION,
    RATE_LIMIT_ENABLED,
    SESSION_COOKIE,
    SESSION_MAX_AGE_SECONDS,
)
from app.core.security import serializer

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
RATE_LIMITS: list[tuple[re.Pattern[str], int, int]] = [
    (re.compile(r"^/login$"), 5, 60),
    (re.compile(r"^/forgot-password$"), 3, 300),
    (re.compile(r"^/reset-password/"), 5, 300),
    (re.compile(r"^/allocations/\d+/send-otp$"), 3, 300),
    (re.compile(r"^/allocations/\d+/sign$"), 5, 300),
    (re.compile(r"^/.+/(download|pdf)$"), 20, 60),
    (re.compile(r"^/.+/(export|csv|excel|xlsx|pdf)$"), 30, 60),
    (re.compile(r"^/reports/.+/(csv|excel|pdf)$"), 30, 60),
]

_BUCKETS: dict[tuple[str, str], list[float]] = {}


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    host = request.client.host if request.client else "unknown"
    return forwarded or host


def _flash_redirect(url: str, message: str, level: str = "error") -> RedirectResponse:
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(FLASH_COOKIE, serializer.dumps({"message": message, "level": level}), httponly=True, samesite="lax", secure=IS_PRODUCTION)
    return response


class ProductionSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if RATE_LIMIT_ENABLED:
            limited = self._rate_limit(request)
            if limited:
                return limited

        if request.method not in SAFE_METHODS:
            csrf_response = await self._validate_csrf(request)
            if csrf_response:
                return csrf_response

        session_payload = self._load_session(request)
        if session_payload:
            now = int(time.time())
            last_activity = int(session_payload.get("last_activity") or session_payload.get("issued_at") or now)
            if now - last_activity > SESSION_MAX_AGE_SECONDS:
                response = _flash_redirect("/login", "Your session expired due to inactivity. Please sign in again.", "warning")
                response.delete_cookie(SESSION_COOKIE)
                return response
            request.state.session_payload = {**session_payload, "last_activity": now}

        response = await call_next(request)
        self._ensure_csrf_cookie(request, response)
        if getattr(request.state, "session_payload", None) and response.status_code < 400:
            response.set_cookie(
                SESSION_COOKIE,
                serializer.dumps(request.state.session_payload),
                max_age=SESSION_MAX_AGE_SECONDS,
                expires=SESSION_MAX_AGE_SECONDS,
                httponly=True,
                samesite="lax",
                secure=IS_PRODUCTION,
            )
        return response

    def _load_session(self, request: Request) -> dict[str, Any] | None:
        raw = request.cookies.get(SESSION_COOKIE)
        if not raw:
            return None
        try:
            data = serializer.loads(raw)
        except Exception:
            return None
        return data if isinstance(data, dict) and data.get("user_id") else None

    def _rate_limit(self, request: Request) -> Response | None:
        path = request.url.path
        matched = next(((pattern.pattern, limit, window) for pattern, limit, window in RATE_LIMITS if pattern.match(path)), None)
        if not matched:
            return None
        bucket_key, limit, window = matched
        now = time.time()
        key = (_client_key(request), bucket_key)
        entries = [stamp for stamp in _BUCKETS.get(key, []) if now - stamp < window]
        if len(entries) >= limit:
            return Response("Too many requests. Please wait and try again.", status_code=429)
        entries.append(now)
        _BUCKETS[key] = entries
        return None

    async def _validate_csrf(self, request: Request) -> Response | None:
        cookie_token = request.cookies.get(CSRF_COOKIE)
        submitted = request.headers.get("x-csrf-token")
        if not submitted:
            content_type = request.headers.get("content-type", "")
            if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                form = await request.form()
                submitted = str(form.get("csrf_token") or "")
        if not cookie_token or not submitted or not secrets.compare_digest(cookie_token, submitted):
            return Response("CSRF validation failed.", status_code=403)
        return None

    def _ensure_csrf_cookie(self, request: Request, response: Response) -> None:
        token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
        response.set_cookie(CSRF_COOKIE, token, max_age=SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=IS_PRODUCTION)
