# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-side capability-token auth for the /mcp transport.

The loopback /mcp endpoint drives browsers (RCE-equivalent) and otherwise has
**no auth** — any local process can POST to it. ``BridgeTokenGuard`` requires the
``X-Octowright-Token`` header to match the per-leader token that lives only in the
0600 lockfile, so a process that can't read the lockfile — a *different user* on a
shared host, or a *sandboxed* process — can't drive the leader.

Limits (be honest): this does NOT defend against a *same-user* process that reads
the 0600 lockfile (it gets the token); the lockfile is the same-user trust
boundary, and a process with the user's privileges can read it. It also does not
close the lockfile-poisoning MITM (an attacker who rewrites the lock writes the
token too). The win is bounded to cross-user / sandbox isolation on a shared host.

The guard wraps the /mcp ASGI app *inside* the host/origin ``SensitiveASGIGuard``
(host checked first, then token). It's a no-op when the expected token is empty
(no token configured) or when ``OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN`` is disabled.
"""

from __future__ import annotations

import hmac
import os

from starlette.types import ASGIApp, Receive, Scope, Send

_HEADER = b"x-octowright-token"
# Tokens that DISABLE the gate. Default is on.
_REQUIRE_OFF = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})


def require_token_enabled() -> bool:
    """Whether /mcp token auth is enforced. ON by default; disable with
    ``OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN`` set to a falsey token."""
    return os.environ.get("OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN", "on").strip().lower() not in _REQUIRE_OFF


class BridgeTokenGuard:
    """ASGI wrapper that rejects /mcp requests lacking a matching token."""

    def __init__(self, app: ASGIApp, expected_token: str) -> None:
        self._app = app
        self._expected = expected_token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        gated = scope["type"] in ("http", "websocket") and bool(self._expected) and require_token_enabled()
        if gated and not self._token_ok(scope):
            await self._reject(scope, send)
            return
        await self._app(scope, receive, send)

    def _token_ok(self, scope: Scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == _HEADER:
                # Constant-time compare so a wrong token can't be probed by timing.
                return hmac.compare_digest(value, self._expected)
        return False

    async def _reject(self, scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": b"missing or invalid X-Octowright-Token"})
