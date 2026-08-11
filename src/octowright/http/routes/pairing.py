# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pairing endpoints: mint (token-authed), redeem (ticket → cookie), /pair page.

See ``octowright.http.pairing`` for the threat model and full flow. These
routes are ``pairing_exempt`` (they *are* the bootstrap) but still sit behind
the loopback/Host/cross-origin guard like every sensitive route.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from octowright.http.bridge_auth import header_token_ok
from octowright.http.exposure import guard_sensitive_http
from octowright.http.pairing import PAIRING, SESSION_COOKIE, TICKET_TTL_SECONDS

_PAIR_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>octowright — pair</title></head>
<body style="font-family:system-ui;background:#111;color:#eee;display:grid;place-items:center;height:100vh;margin:0">
<div id="msg" style="max-width:38rem;text-align:center;line-height:1.5">Pairing&hellip;</div>
<script>
const t = location.hash.slice(1);
const msg = document.getElementById("msg");
// Scrub the ticket from the address bar immediately, success or fail.
history.replaceState(null, "", "/pair");
if (!t) {
  msg.textContent = "No pairing ticket in the URL fragment. Run `octowright dashboard` " +
    "and open the FULL printed URL (including everything after the #).";
} else {
  fetch("/api/pair/redeem", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({ticket: t}),
  }).then((r) => {
    if (r.ok) { location.replace("/"); return; }
    msg.textContent = "Pairing failed (" + r.status + "). Tickets are single-use and expire " +
      "quickly — run `octowright dashboard` again for a fresh URL.";
  }).catch((e) => { msg.textContent = "Pairing failed: " + e; });
}
</script>
</body></html>
"""


async def pair_mint(request: Request) -> Response:
    """Mint a single-use pairing ticket. Requires the capability token — the
    same credential the follower presents on /mcp — so only a process that can
    read the 0600 lockfile (the `octowright dashboard` CLI) can mint."""
    expected = PAIRING.expected_token
    if not expected:
        # Inline (--no-singleton) leader: no lockfile, no token — there is no
        # authenticated minter, so refuse rather than fail open.
        return JSONResponse(
            {"error": "pairing unavailable: this leader has no capability token (inline/--no-singleton mode)"},
            status_code=503,
        )
    if not header_token_ok(request.headers.get("x-octowright-token"), expected):
        return JSONResponse({"error": "missing or invalid X-Octowright-Token"}, status_code=403)
    ticket = PAIRING.mint_ticket()
    return JSONResponse({"ok": True, "ticket": ticket, "expires_in": int(TICKET_TTL_SECONDS)})


async def pair_redeem(request: Request) -> Response:
    """Consume a ticket and set the HttpOnly session cookie."""
    try:
        body = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    ticket = body.get("ticket") if isinstance(body, dict) else None
    bearer = PAIRING.redeem_ticket(ticket) if isinstance(ticket, str) and ticket else None
    if bearer is None:
        return JSONResponse({"error": "invalid or expired ticket"}, status_code=403)
    response = JSONResponse({"ok": True})
    # No Secure flag: the dashboard is plain-HTTP on loopback by design.
    response.set_cookie(SESSION_COOKIE, bearer, httponly=True, samesite="strict", path="/")
    return response


async def pair_page(_request: Request) -> Response:
    return HTMLResponse(_PAIR_HTML)


def routes() -> list[Route]:
    return [
        Route("/api/pair/mint", guard_sensitive_http(pair_mint, pairing_exempt=True), methods=["POST"]),
        Route("/api/pair/redeem", guard_sensitive_http(pair_redeem, pairing_exempt=True), methods=["POST"]),
        Route("/pair", guard_sensitive_http(pair_page, pairing_exempt=True), methods=["GET"]),
    ]
