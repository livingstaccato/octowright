# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exposure guard for sensitive dashboard/API handlers."""

from __future__ import annotations

import functools
import ipaddress
import os
from collections.abc import Awaitable, Callable

from starlette.requests import HTTPConnection, Request
from starlette.responses import JSONResponse, Response

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
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def remote_dashboard_allowed() -> bool:
    return os.environ.get("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD") == "1"


def _sensitive_allowed_for_app(app: object) -> bool:
    host = getattr(getattr(app, "state", object()), "octowright_http_host", _DEFAULT_HTTP_HOST)
    return is_loopback_host(host) or remote_dashboard_allowed()


def sensitive_allowed_for_request(request: Request) -> bool:
    return _sensitive_allowed_for_app(request.app)


def sensitive_allowed_for_connection(connection: HTTPConnection) -> bool:
    return _sensitive_allowed_for_app(connection.app)


def guard_sensitive_http[ResponseT: Response](
    handler: Callable[[Request], Awaitable[ResponseT]],
) -> Callable[[Request], Awaitable[Response]]:
    @functools.wraps(handler)
    async def guarded(request: Request) -> Response:
        if not sensitive_allowed_for_request(request):
            return JSONResponse(_REMOTE_DISABLED_BODY, status_code=403)
        return await handler(request)

    return guarded
