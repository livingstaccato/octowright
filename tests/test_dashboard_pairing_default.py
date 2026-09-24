# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The dashboard pairing gate ships ON.

Loopback binding and the Host/Origin guards stop a remote attacker and a
malicious web page, but they are not authentication: any other local process
could otherwise enumerate live sessions, read recorded JSONL (typed input,
URLs, console output), fetch video, subscribe to the live screencast, and
drive the browser. That made the on-by-default 0600 recording permissions and
0700 profile permissions misleading, since the daemon served the same bytes
over HTTP to anyone who asked.

Enforcement needs a credential to pair against. An inline ``--no-singleton``
leader has no lockfile and therefore no capability token; it used to be waved
through as "nothing to pair against", which left its dashboard open to every
local user under the default policy. It now gets a random in-memory anchor and
mints codes in-process, and a missing anchor is refused rather than
authorized.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright.http import app as _http
from octowright.http.pairing import (
    DASHBOARD_STATE_ATTR,
    DashboardPairingState,
    dashboard_access_ok,
    dashboard_websocket_auth,
    pairing_anchor_available,
    pairing_required,
)

_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"
_TOKEN = "test-cap-token"  # pragma: allowlist secret (synthetic fixture)


@pytest.fixture(autouse=True)
def _unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """No explicit setting -- exactly what a user gets out of the box.

    Also points the recordings root at a temp dir so /api/sessions never
    scans the developer's real session tree.
    """
    monkeypatch.delenv(_ENV, raising=False)
    from octowright import defaults
    from octowright.http import discovery

    recordings = tmp_path / "recordings"
    recordings.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", recordings)
    monkeypatch.setattr(discovery, "RECORDINGS_DIR", recordings, raising=False)


def test_pairing_is_on_out_of_the_box() -> None:
    assert pairing_required() is True


@pytest.mark.parametrize("token", [_TOKEN, ""], ids=["leader", "inline"])
def test_build_app_always_attaches_the_pairing_anchor(token: str) -> None:
    """A tokenless (inline) app gets a random anchor, not an unenforced gate."""
    app = _http.build_app(mcp_token=token)
    state = getattr(app.state, DASHBOARD_STATE_ATTR, None)
    assert isinstance(state, DashboardPairingState)
    assert pairing_anchor_available(state) is True


def test_a_real_leader_refuses_an_unauthenticated_dashboard_request() -> None:
    client = TestClient(_http.build_app(mcp_token=_TOKEN))
    response = client.get("/api/sessions")
    assert response.status_code == 401
    assert "octowright dashboard" in response.text


def test_capability_token_still_authorizes() -> None:
    """Followers and scripts keep a non-interactive path in."""
    client = TestClient(_http.build_app(mcp_token=_TOKEN))
    response = client.get("/api/sessions", headers={"x-octowright-token": _TOKEN})
    assert response.status_code == 200


def test_a_paired_bearer_authorizes() -> None:
    app = _http.build_app(mcp_token=_TOKEN)
    pairing = app.state.dashboard_pairing
    grant = pairing.redeem_code(pairing.mint_code())
    assert grant is not None
    client = TestClient(app)
    response = client.get("/api/sessions", headers={"Authorization": f"Bearer {grant.bearer}"})
    assert response.status_code == 200


def test_opting_out_restores_the_type_the_url_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "off")
    client = TestClient(_http.build_app(mcp_token=_TOKEN))
    assert client.get("/api/sessions").status_code == 200


def test_inline_leader_refuses_an_unauthenticated_dashboard_request() -> None:
    """The finding: tokenless used to mean open, to any local user."""
    client = TestClient(_http.build_app(mcp_token=""))
    assert client.get("/api/sessions").status_code == 401


def test_inline_leader_random_anchor_is_not_the_empty_token() -> None:
    """An empty or absent X-Octowright-Token must never match the anchor."""
    client = TestClient(_http.build_app(mcp_token=""))
    assert client.get("/api/sessions", headers={"x-octowright-token": ""}).status_code == 401


def test_inline_leader_mints_a_code_in_process_that_redeems() -> None:
    app = _http.build_app(mcp_token="")
    pairing = app.state.dashboard_pairing
    grant = pairing.redeem_code(pairing.mint_code())
    assert grant is not None
    client = TestClient(app)
    response = client.get("/api/sessions", headers={"Authorization": f"Bearer {grant.bearer}"})
    assert response.status_code == 200


def test_inline_leader_can_still_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "off")
    client = TestClient(_http.build_app(mcp_token=""))
    assert client.get("/api/sessions").status_code == 200


class _Stateless:
    """A connection on an embedder's own app: no pairing state attached."""

    class app:
        class state:
            pass

    headers: Any = None

    def __init__(self) -> None:
        from starlette.datastructures import Headers

        self.headers = Headers({"sec-websocket-protocol": "octowright.dashboard"})


def test_missing_anchor_is_refused_not_authorized() -> None:
    assert dashboard_access_ok(_Stateless()) is False  # type: ignore[arg-type]
    allowed, _protocol = dashboard_websocket_auth(_Stateless())  # type: ignore[arg-type]
    assert allowed is False


def test_inline_serve_prints_a_redeemable_pairing_url(capsys: pytest.CaptureFixture[str]) -> None:
    """``octowright dashboard`` needs a lockfile an inline leader never writes."""
    from octowright.cli.serve import _echo_inline_pairing_url

    app = _http.build_app(mcp_token="")
    _echo_inline_pairing_url("127.0.0.1", 6399)
    err = capsys.readouterr().err
    assert "http://127.0.0.1:6399/pair#" in err
    code = err.split("/pair#", 1)[1].split()[0]
    assert app.state.dashboard_pairing.redeem_code(code) is not None
