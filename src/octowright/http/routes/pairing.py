# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pairing endpoints: mint, one-time-code redemption, and SPA bootstrap.

See ``octowright.http.pairing`` for the threat model and full flow. These
routes are ``pairing_exempt`` (they *are* the bootstrap) but still sit behind
the loopback/Host/cross-origin guard like every sensitive route.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, Response
from starlette.routing import Route

from octowright.http import state
from octowright.http.exposure import guard_sensitive_http
from octowright.http.json_response import SafeJSONResponse
from octowright.http.pairing import PAIR_CODE_TTL_SECONDS, dashboard_pairing_state
from octowright.http.routes._common import _read_json_body

#: Redemption is reached with no credential at all -- it is the bootstrap -- and
#: reads its body before it can tell a real code from a bad one. A code is a few
#: dozen bytes, so this fixed ceiling costs nothing and cannot be switched off,
#: unlike ``OCTOWRIGHT_MAX_REQUEST_BODY_BYTES`` (off by default). Without it any
#: process that can reach loopback made the leader buffer an unbounded body.
PAIR_REDEEM_MAX_BODY_BYTES = 4096


async def pair_mint(request: Request) -> Response:
    """Mint a single-use pairing code. Requires the capability token — the
    same credential the follower presents on /mcp — so only a process that can
    read the 0600 lockfile (the `octowright dashboard` CLI) can mint."""
    pairing = dashboard_pairing_state(request)
    if pairing is None or not pairing.token_configured or not pairing.http_mint:
        # Inline (--no-singleton) leader: no lockfile, so no token anyone
        # outside the process could present here. It mints in-process instead.
        return SafeJSONResponse(
            {
                "error": "pairing unavailable over HTTP: this leader has no published capability token "
                "(inline/--no-singleton mode); use the pairing URL it printed at startup or the "
                "octowright_dashboard_url MCP tool"
            },
            status_code=503,
        )
    if not pairing.capability_token_ok(request.headers.get("x-octowright-token")):
        return SafeJSONResponse({"error": "missing or invalid X-Octowright-Token"}, status_code=403)
    code = pairing.mint_code()
    return SafeJSONResponse(
        {"code": code, "expires_in": int(PAIR_CODE_TTL_SECONDS)},
        headers={"Cache-Control": "no-store"},
    )


async def pair_redeem(request: Request) -> Response:
    """Consume a code and return an origin-scoped browser bearer once."""
    body, error = await _read_json_body(request, max_bytes=PAIR_REDEEM_MAX_BODY_BYTES)
    if error is not None:
        return error
    code = body.get("code") if isinstance(body, dict) else None
    pairing = dashboard_pairing_state(request)
    grant = pairing.redeem_code(code) if pairing is not None and isinstance(code, str) else None
    if grant is None:
        return SafeJSONResponse(
            {"error": "invalid or expired pairing code"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    return SafeJSONResponse(
        {"bearer": grant.bearer, "expires_at": grant.expires_at},
        headers={"Cache-Control": "no-store"},
    )


async def pair_page(_request: Request) -> Response:
    target = state.FRONTEND_DIR / "index.html"
    if not target.exists():
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><title>octowright pairing</title>"
            "<p>Dashboard frontend not bundled. Run <code>npm run build</code>.</p>",
            headers={"Cache-Control": "no-store"},
        )
    return FileResponse(str(target), media_type="text/html", headers={"Cache-Control": "no-store"})


def routes() -> list[Route]:
    return [
        Route("/api/pair/mint", guard_sensitive_http(pair_mint, pairing_exempt=True), methods=["POST"]),
        Route("/api/pair/redeem", guard_sensitive_http(pair_redeem, pairing_exempt=True), methods=["POST"]),
        Route("/pair", guard_sensitive_http(pair_page, pairing_exempt=True), methods=["GET"]),
    ]
