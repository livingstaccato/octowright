# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exposure guard for sensitive dashboard/API handlers."""

from __future__ import annotations

import functools
import ipaddress
import json
import os
from collections.abc import Awaitable, Callable
from typing import TypeVar

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from octowright.defaults import DASHBOARD_REMOTE_ALLOWED_ENV

ResponseT = TypeVar("ResponseT", bound=Response)
_DEFAULT_HTTP_HOST = "127.0.0.1"
_REMOTE_DISABLED_BODY = {
    "error": "remote dashboard access is disabled",
    "hint": "Bind the HTTP dashboard to 127.0.0.1 or set OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1.",
}


def is_loopback_host(host: str | None) -> bool:
    """Return true only for localhost/loopback bind addresses."""
    if host is None:
        return False
    normalized = host.strip().lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def remote_dashboard_allowed() -> bool:
    return os.environ.get(DASHBOARD_REMOTE_ALLOWED_ENV) == "1"


def _sensitive_allowed_for_app(app: object) -> bool:
    host = getattr(getattr(app, "state", object()), "octowright_http_host", _DEFAULT_HTTP_HOST)
    return is_loopback_host(host) or remote_dashboard_allowed()


def sensitive_allowed_for_request(request: Request) -> bool:
    return _sensitive_allowed_for_app(request.app)


def sensitive_allowed_for_connection(connection: HTTPConnection) -> bool:
    return _sensitive_allowed_for_app(connection.app)


def guard_sensitive_http(
    handler: Callable[[Request], Awaitable[ResponseT]],
) -> Callable[[Request], Awaitable[Response]]:
    @functools.wraps(handler)
    async def guarded(request: Request) -> Response:
        if not sensitive_allowed_for_request(request):
            return JSONResponse(_REMOTE_DISABLED_BODY, status_code=403)
        return await handler(request)

    return guarded


class SensitiveASGIGuard:
    """ASGI wrapper for sensitive mounted apps such as FastMCP transport."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type in {"http", "websocket"} and not _sensitive_allowed_for_app(scope["app"]):
            if scope_type == "websocket":
                await send({"type": "websocket.close", "code": 1008, "reason": _REMOTE_DISABLED_BODY["error"]})
                return
            payload = json.dumps(_REMOTE_DISABLED_BODY).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        await self.app(scope, receive, send)


def guard_sensitive_asgi_app(app: ASGIApp) -> ASGIApp:
    return SensitiveASGIGuard(app)
