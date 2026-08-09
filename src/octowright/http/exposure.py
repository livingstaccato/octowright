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
from typing import Protocol, TypeVar, cast

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocket

from octowright.defaults import DASHBOARD_REMOTE_ALLOWED_ENV

ResponseT = TypeVar("ResponseT", bound=Response)


class _SensitiveGuardedHandler(Protocol):
    __octowright_sensitive_guard__: bool


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


def _host_without_port(value: str) -> str:
    """Strip an optional ``:port`` from a ``host`` / ``host:port`` value, leaving
    ``[ipv6]`` literals intact for ``is_loopback_host`` to unwrap. One canonical
    parser shared by the Host-header and WebSocket-Origin loopback checks."""
    if value.startswith("["):
        bracket_close = value.rfind("]")
        return value[: bracket_close + 1] if bracket_close != -1 else value
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def _sensitive_allowed_for_host(host: str | None) -> bool:
    return is_loopback_host(host) or remote_dashboard_allowed()


def _sensitive_allowed_for_app(app: object) -> bool:
    host = getattr(getattr(app, "state", object()), "octowright_http_host", _DEFAULT_HTTP_HOST)
    return _sensitive_allowed_for_host(host)


def sensitive_allowed_for_request(request: Request) -> bool:
    return sensitive_allowed_for_connection(request)


def request_host_loopback_allowed(raw_host: str | None) -> bool:
    """Return true iff an incoming HTTP Host header names localhost/loopback.

    Defends against DNS Rebinding: even if the app bound to loopback, an
    attacker can point a malicious DNS name at 127.0.0.1. The browser will
    connect to the local port but send ``Host: malicious.com``. We must ensure
    the requested Host is explicitly a loopback name/IP. Shared by the
    Starlette request/WebSocket guard and the mounted-ASGI guard so both apply
    the same policy.
    """
    if raw_host is None:
        return False
    stripped = raw_host.strip()
    if not stripped:
        return False
    return is_loopback_host(_host_without_port(stripped))


def sensitive_allowed_for_connection(connection: HTTPConnection) -> bool:
    if not _sensitive_allowed_for_app(connection.app):
        return False
    if remote_dashboard_allowed():
        return True
    return request_host_loopback_allowed(connection.headers.get("host"))


def _origin_host_from_origin(origin: str) -> str | None:
    """Extract the ``host:port`` (or just ``host``) portion from an Origin
    header value. Returns ``None`` if the value is malformed (no ``://``)."""
    sep = origin.find("://")
    if sep == -1:
        return None
    return origin[sep + 3 :]


def websocket_origin_allowed(websocket: WebSocket) -> bool:
    """Mirror the HTTP cross-origin guard for WebSocket handshakes.

    HTTP routes get cross-origin protection via ``guard_sensitive_http``;
    without an equivalent here a cross-origin page in the victim's browser
    could open ``ws://127.0.0.1:8765/api/sessions/{id}/tail`` (the loopback
    check passes because the kernel sees the connection coming from the
    same host) and read the live JSONL — including form input, navigated
    URLs, and console output.

    Policy:
      * No ``Origin`` header → non-browser client (curl/python-websockets),
        allow. Browsers always send Origin on WS handshakes.
      * Origin host matches the request ``Host`` header → same origin, allow.
      * Origin host is a loopback name/address → allow (developer tooling
        on the same machine, e.g. another local dashboard tab).
      * Otherwise → block.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    origin_host = _origin_host_from_origin(origin)
    if origin_host is None:
        # Malformed Origin header — refuse rather than fail open.
        return False
    if origin_host == websocket.headers.get("host", ""):
        return True
    # Strip any path/query that snuck through (Origin is host-only per spec),
    # then the optional :port via the shared canonical parser.
    bare_host = origin_host.split("/", 1)[0]
    return is_loopback_host(_host_without_port(bare_host))


def _cross_origin_blocked_from_parts(
    *,
    method: str,
    origin: str | None,
    sec_fetch_site: str | None,
    scheme: str,
    host: str,
    side_effect_get: bool,
) -> bool:
    """Shared cross-origin policy for Request handlers and mounted ASGI apps."""
    # CORS preflight is always allowed; the follow-up request carries the
    # method we actually care about and is checked on its own.
    if method == "OPTIONS":
        return False
    if method in {"GET", "HEAD"} and not side_effect_get:
        return False
    if origin:
        same_origin = f"{scheme}://{host}"
        if origin != same_origin:
            return True
    return (sec_fetch_site or "").lower() in {"cross-site", "same-site"}


def _cross_origin_blocked(request: Request, *, side_effect_get: bool) -> bool:
    """Block browser-driven unsafe cross-origin requests to localhost dashboard
    APIs. ``side_effect_get`` opts a GET/HEAD route into the same protection
    as POST/PUT/DELETE — use it for endpoints whose handler triggers work on
    the live browser (live screenshot, live markdown capture, etc.)."""
    return _cross_origin_blocked_from_parts(
        method=request.method,
        origin=request.headers.get("origin"),
        sec_fetch_site=request.headers.get("sec-fetch-site"),
        scheme=request.url.scheme,
        host=request.headers.get("host", ""),
        side_effect_get=side_effect_get,
    )


def guard_sensitive_http(
    handler: Callable[[Request], Awaitable[ResponseT]],
    *,
    side_effect_get: bool = False,
) -> Callable[[Request], Awaitable[Response]]:
    """Wrap a handler with the bind-host + cross-origin guard. Pass
    ``side_effect_get=True`` at the route definition for GET endpoints that
    drive the live browser; this keeps the policy decision next to the route
    rather than in a separate path-matcher that can drift."""

    @functools.wraps(handler)
    async def guarded(request: Request) -> Response:
        if not sensitive_allowed_for_request(request):
            return JSONResponse(_REMOTE_DISABLED_BODY, status_code=403)
        if _cross_origin_blocked(request, side_effect_get=side_effect_get):
            return JSONResponse({"error": "cross-origin dashboard request is blocked"}, status_code=403)
        return await handler(request)

    cast(_SensitiveGuardedHandler, guarded).__octowright_sensitive_guard__ = True
    return guarded


class SensitiveASGIGuard:
    """ASGI wrapper for sensitive mounted apps such as the MCP transport.

    The bind host is captured at wrap time because ``scope["app"]`` inside a
    Starlette ``Mount`` resolves to the inner mounted app, not the outer
    Starlette app where ``octowright_http_host`` is set. Reading from a
    closure keeps the guard correct regardless of mount nesting.
    """

    def __init__(self, app: ASGIApp, host: str | None = None, *, side_effect_get: bool = False) -> None:
        self.app = app
        self._host = host
        self._side_effect_get = side_effect_get

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type in {"http", "websocket"}:
            headers = _headers_from_scope(scope)
            if not _sensitive_allowed_for_host(self._host):
                await _send_blocked_asgi_response(send, scope_type, _REMOTE_DISABLED_BODY)
                return
            if not remote_dashboard_allowed() and not request_host_loopback_allowed(headers.get("host")):
                await _send_blocked_asgi_response(send, scope_type, _REMOTE_DISABLED_BODY)
                return
            if _asgi_cross_origin_blocked(scope, headers=headers, side_effect_get=self._side_effect_get):
                await _send_blocked_asgi_response(
                    send,
                    scope_type,
                    {"error": "cross-origin dashboard request is blocked"},
                )
                return
        await self.app(scope, receive, send)


def _headers_from_scope(scope: Scope) -> dict[str, str]:
    return {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope["headers"]}


def _asgi_cross_origin_blocked(
    scope: Scope,
    *,
    headers: dict[str, str] | None = None,
    side_effect_get: bool,
) -> bool:
    scope_type = scope["type"]
    if headers is None:
        headers = _headers_from_scope(scope)
    scheme = _http_scheme_for_scope(scope)
    return _cross_origin_blocked_from_parts(
        method=str(scope.get("method", "GET")) if scope_type == "http" else "GET",
        origin=headers.get("origin"),
        sec_fetch_site=headers.get("sec-fetch-site"),
        scheme=scheme,
        host=headers.get("host", ""),
        side_effect_get=side_effect_get if scope_type == "http" else True,
    )


def _http_scheme_for_scope(scope: Scope) -> str:
    scheme = str(scope.get("scheme", "http"))
    if scope["type"] == "websocket":
        return "https" if scheme in {"wss", "https"} else "http"
    return scheme


async def _send_blocked_asgi_response(send: Send, scope_type: str, body: dict[str, str]) -> None:
    if scope_type == "websocket":
        await send({"type": "websocket.close", "code": 1008, "reason": body["error"]})
        return
    payload = json.dumps(body).encode("utf-8")
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


def guard_sensitive_asgi_app(app: ASGIApp, *, host: str | None = None, side_effect_get: bool = False) -> ASGIApp:
    return SensitiveASGIGuard(app, host=host, side_effect_get=side_effect_get)
