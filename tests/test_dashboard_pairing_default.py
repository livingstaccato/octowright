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

Enforcement additionally needs a credential to pair against, so these also pin
the degradation rules -- an inline ``--no-singleton`` leader has no lockfile
and therefore no capability token, and must not ship with a dashboard nobody
can ever open.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright.http import app as _http
from octowright.http.pairing import (
    DASHBOARD_STATE_ATTR,
    DashboardPairingState,
    pairing_anchor_available,
    pairing_explicitly_enabled,
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


def test_unset_is_not_an_explicit_opt_in() -> None:
    assert pairing_explicitly_enabled() is False


@pytest.mark.parametrize("token", ["1", "on", "true", "yes"])
def test_explicit_enable_is_recognized(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv(_ENV, token)
    assert pairing_explicitly_enabled() is True


def test_build_app_always_attaches_the_pairing_anchor() -> None:
    """Guards the fail-open path in ``dashboard_access_ok``.

    That path treats a missing anchor as "cannot enforce", which is only safe
    because the anchor is not request-controlled. A refactor that stopped
    attaching it would silently disable the gate for every route.
    """
    app = _http.build_app(mcp_token=_TOKEN)
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


def test_tokenless_leader_stays_usable_under_the_default() -> None:
    """An inline --no-singleton leader has no token, so nobody could pair.

    Enforcing there would be a permanent lockout rather than a control: with
    no lockfile there is no trust anchor to gate against in the first place.
    """
    client = TestClient(_http.build_app(mcp_token=""))
    assert client.get("/api/sessions").status_code == 200


def test_tokenless_leader_fails_closed_when_pairing_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator who asked for a locked door gets one, token or not."""
    monkeypatch.setenv(_ENV, "1")
    client = TestClient(_http.build_app(mcp_token=""))
    assert client.get("/api/sessions").status_code == 401
