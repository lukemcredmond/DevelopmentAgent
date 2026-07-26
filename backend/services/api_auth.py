"""Opt-in localhost API token middleware and Discord secret env overrides."""

from __future__ import annotations

import os
import secrets
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


def allhands_api_token() -> str:
    return str(os.environ.get("ALLHANDS_API_TOKEN") or "").strip()


def discord_bot_token_from_env() -> str:
    return str(os.environ.get("ALLHANDS_DISCORD_BOT_TOKEN") or "").strip()


def discord_webhook_url_from_env() -> str:
    return str(os.environ.get("ALLHANDS_DISCORD_WEBHOOK_URL") or "").strip()


def resolve_discord_bot_token(settings_token: str = "") -> str:
    env = discord_bot_token_from_env()
    if env:
        return env
    return str(settings_token or "").strip()


def resolve_discord_webhook_url(settings_url: str = "") -> str:
    env = discord_webhook_url_from_env()
    if env:
        return env
    return str(settings_url or "").strip()


class AllHandsApiTokenMiddleware(BaseHTTPMiddleware):
    """When ALLHANDS_API_TOKEN is set, require Bearer or X-AllHands-Token on /api/*."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        token = allhands_api_token()
        if not token:
            return await call_next(request)
        path = request.url.path or ""
        if not path.startswith("/api/"):
            return await call_next(request)
        # Allow CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)
        provided = ""
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
        if not provided:
            provided = (request.headers.get("x-allhands-token") or "").strip()
        if not provided or not secrets.compare_digest(provided, token):
            return JSONResponse(
                {"detail": "Unauthorized — set Authorization: Bearer <ALLHANDS_API_TOKEN>"},
                status_code=401,
            )
        return await call_next(request)
