# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Requiring dashboard pairing must not break the MCP surface.

The pairing gate is applied by ``exposure.guard_sensitive_http`` to every
*route*, but the follower bridge talks to a **mounted ASGI app** (``/mcp``)
guarded by ``SensitiveASGIGuard`` + ``BridgeTokenGuard`` instead. If the
pairing check ever reached that mount, every follower -- and therefore every
MCP client -- would start getting 401 from a healthy leader. Same for the
routes a launched browser and the pairing bootstrap itself must reach with no
credential at all.

These pin the boundary from both sides: what must stay open, and what must
now be closed.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright.http import app as _http_app

_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"
_TOKEN = "cap-token-probe"  # pragma: allowlist secret (synthetic fixture)

_RPC = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "probe", "version": "1"},
    },
}
_RPC_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


@pytest.fixture(autouse=True)
def _pairing_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv(_ENV, "1")
    from octowright import defaults

    recordings = tmp_path / "recordings"
    recordings.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", recordings)


@pytest.fixture
def client() -> Any:
    return TestClient(_http_app.build_app(mcp_token=_TOKEN), base_url="http://127.0.0.1")


# --- must stay reachable ------------------------------------------------------


def test_new_tab_needs_no_credential(client: Any) -> None:
    """browser_launch with no URL sends the browser here; it has no bearer."""
    assert client.get("/new-tab").status_code == 200


def test_pair_page_needs_no_credential(client: Any) -> None:
    """The bootstrap cannot require the credential it exists to hand out."""
    assert client.get("/pair").status_code == 200


def test_spa_shell_is_not_pairing_gated(client: Any) -> None:
    """The page must not be gated, so it can send the user into pairing.

    Asserts "not 401" rather than "200" on purpose: CI runs the test suite
    *before* the frontend SPA build step, so the bundle is absent there and
    the shell legitimately 404s. Whether the bundle exists is orthogonal to
    whether the route sits behind the credential gate, which is what this
    pins.
    """
    assert client.get("/").status_code != 401


def test_mint_works_with_the_capability_token(client: Any) -> None:
    assert client.post("/api/pair/mint", headers={"x-octowright-token": _TOKEN}).status_code == 200


def test_the_mcp_mount_is_not_pairing_gated() -> None:
    """The load-bearing one: a follower presenting the capability token must
    never be answered 401, or every MCP client breaks against a healthy leader.

    A wrong token is refused by the bridge guard (403). A correct token gets
    past authorization -- what the MCP transport then answers depends on
    session/handshake state, and is not this test's business; the assertion is
    only that it is neither 401 nor the 403 the guard would have produced.
    """
    app = _http_app.build_app(mcp_leader=True, mcp_token=_TOKEN)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        good = client.post("/mcp/", headers={**_RPC_HEADERS, "x-octowright-token": _TOKEN}, json=_RPC)
        bad = client.post("/mcp/", headers={**_RPC_HEADERS, "x-octowright-token": "wrong"}, json=_RPC)

    assert bad.status_code == 403, "the bridge token guard must still refuse a bad token"
    assert good.status_code != 401, "pairing must never gate the MCP transport"
    assert good.status_code != 403, "a valid capability token must pass authorization"


# --- must now be gated --------------------------------------------------------


def test_dashboard_api_is_closed_without_a_credential(client: Any) -> None:
    assert client.get("/api/sessions").status_code == 401


def test_dashboard_api_opens_for_the_capability_token(client: Any) -> None:
    assert client.get("/api/sessions", headers={"x-octowright-token": _TOKEN}).status_code == 200


def test_mcp_events_keeps_its_own_403_contract(client: Any) -> None:
    """Follower-only channel: refused by the token guard, not the pairing gate."""
    assert client.get("/api/mcp-events").status_code == 403
