# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The bridge token gate, composed into the REAL leader app mount.

Unit tests pin BridgeTokenGuard in isolation; this drives it through the actual
`build_app(mcp_leader=True)` /mcp mount — SensitiveASGIGuard (host/origin) wrapping
BridgeTokenGuard wrapping the real streamable-HTTP transport — so the wiring and
ordering are proven, not just the guard. A request without the lockfile token is
403'd before it reaches the MCP transport; the correct token gets through the gate.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from octowright.http.app import build_app

_TOKEN = "test-capability-token"
# Minimal streamable-HTTP MCP headers so a token-accepted request reaches the
# transport (its own response — not a 403 — is all we assert).
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


def test_mcp_token_gate_composed_in_real_app() -> None:
    # ONE app / ONE lifespan: the MCP streamable session manager is a process
    # singleton, so building several leader apps in one process collides. All
    # three assertions share the single client. raise_server_exceptions=False so
    # a transport-level error on the accept path surfaces as a 5xx (still != 403),
    # not a test crash.
    app = build_app(mcp_leader=True, host="127.0.0.1", mcp_token=_TOKEN)
    with TestClient(app, raise_server_exceptions=False) as client:
        # Missing token → 403, rejected by the guard before the MCP transport.
        assert client.post("/mcp/", json=_INIT_BODY, headers=_MCP_HEADERS).status_code == 403
        # Wrong token → 403.
        wrong = client.post("/mcp/", json=_INIT_BODY, headers={**_MCP_HEADERS, "X-Octowright-Token": "nope"})
        assert wrong.status_code == 403
        # Correct token → passes the gate and reaches the transport (NOT a 403).
        ok = client.post("/mcp/", json=_INIT_BODY, headers={**_MCP_HEADERS, "X-Octowright-Token": _TOKEN})
        assert ok.status_code != 403

    # The mcp_token="" → open (inline --no-singleton back-compat) case can't build
    # a 2nd leader app in this process (MCP session manager is a singleton); it's
    # covered by test_bridge_token.TestBridgeTokenGuard::test_empty_expected_token_bypasses.
