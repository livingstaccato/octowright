# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Opt-in dashboard pairing: ticket/session store, access decision, routes.

The browser-facing dashboard surface (/api/sessions, media, events, /tail WS,
persona/scenario/macro writes) is loopback-Host-gated only — a *different-user*
or *sandboxed* loopback process can read live JSONL and drive writes. Embedding
the capability token in the served page would leak it to any loopback fetcher,
so the token instead reaches the human out-of-band: `octowright dashboard`
(same-user, reads the 0600 lockfile) mints a single-use short-TTL ticket and
prints ``/pair#<ticket>`` to the tty; the /pair page redeems the ticket for an
HttpOnly session cookie. ``OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING`` is OFF by
default (back-compat); everything here must be a no-op when it is off.
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from octowright.http import pairing as pairing_mod
from octowright.http.pairing import (
    MAX_SESSIONS,
    SESSION_COOKIE,
    TICKET_TTL_SECONDS,
    PairingState,
    dashboard_access_ok,
    pairing_required,
)

_TOKEN = "test-cap-token"
_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"


@pytest.fixture(autouse=True)
def _fresh_pairing_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate the module singleton per test; default the knob to unset (off)."""
    monkeypatch.delenv(_ENV, raising=False)
    pairing_mod.PAIRING.reset()
    pairing_mod.PAIRING.set_expected_token(_TOKEN)
    yield
    pairing_mod.PAIRING.reset()


def _request(headers: dict[str, str] | None = None, path: str = "/x") -> Request:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope: dict[str, Any] = {"type": "http", "method": "GET", "headers": hdrs, "query_string": b"", "path": path}

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


# --- ticket / session store ---------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_mint_then_redeem_returns_bearer_and_is_single_use() -> None:
    state = PairingState()
    ticket = state.mint_ticket()
    bearer = state.redeem_ticket(ticket)
    assert bearer is not None
    assert state.session_ok(bearer)
    # Single-use: the same ticket never redeems twice.
    assert state.redeem_ticket(ticket) is None


def test_expired_ticket_does_not_redeem() -> None:
    clock = _Clock()
    state = PairingState(clock=clock)
    ticket = state.mint_ticket()
    clock.now += TICKET_TTL_SECONDS + 1.0
    assert state.redeem_ticket(ticket) is None


def test_unknown_ticket_and_unknown_bearer_rejected() -> None:
    state = PairingState()
    assert state.redeem_ticket("no-such-ticket") is None
    assert not state.session_ok("no-such-bearer")
    assert not state.session_ok("")


def test_session_store_is_lru_bounded() -> None:
    state = PairingState()
    bearers = [state.redeem_ticket(state.mint_ticket()) for _ in range(MAX_SESSIONS + 1)]
    assert all(b is not None for b in bearers)
    # Oldest evicted, newest kept.
    assert not state.session_ok(bearers[0] or "")
    assert state.session_ok(bearers[-1] or "")


# --- env knob -----------------------------------------------------------------


def test_pairing_required_default_off() -> None:
    assert not pairing_required()


@pytest.mark.parametrize("value", ["1", "on", "true", "yes"])
def test_pairing_required_enabled_tokens(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_ENV, value)
    assert pairing_required()


@pytest.mark.parametrize("value", ["0", "off", "false", "no", ""])
def test_pairing_required_falsey_tokens(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(_ENV, value)
    assert not pairing_required()


# --- access decision ----------------------------------------------------------


def test_access_ok_when_pairing_disabled() -> None:
    assert dashboard_access_ok(_request())


def test_access_denied_when_enabled_and_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    assert not dashboard_access_ok(_request())


def test_access_ok_with_valid_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    bearer = pairing_mod.PAIRING.redeem_ticket(pairing_mod.PAIRING.mint_ticket())
    assert bearer is not None
    assert dashboard_access_ok(_request({"cookie": f"{SESSION_COOKIE}={bearer}"}))


def test_access_denied_with_bogus_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    assert not dashboard_access_ok(_request({"cookie": f"{SESSION_COOKIE}=forged"}))


def test_access_ok_with_capability_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    # Programmatic/follower path: the X-Octowright-Token header keeps working.
    monkeypatch.setenv(_ENV, "1")
    assert dashboard_access_ok(_request({"x-octowright-token": _TOKEN}))


def test_access_denied_with_wrong_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    assert not dashboard_access_ok(_request({"x-octowright-token": "nope"}))


def test_header_path_denied_when_no_expected_token(monkeypatch: pytest.MonkeyPatch) -> None:
    # Inline leader (empty token): the header alternative must not fail open.
    monkeypatch.setenv(_ENV, "1")
    pairing_mod.PAIRING.set_expected_token("")
    assert not dashboard_access_ok(_request({"x-octowright-token": ""}))


# --- routes (end-to-end through the real app) ---------------------------------


def _client() -> TestClient:
    from octowright.http.app import build_app

    return TestClient(build_app(mcp_leader=False, host="127.0.0.1", mcp_token=_TOKEN))


def test_mint_requires_capability_token() -> None:
    with _client() as client:
        assert client.post("/api/pair/mint").status_code == 403
        assert client.post("/api/pair/mint", headers={"X-Octowright-Token": "nope"}).status_code == 403
        resp = client.post("/api/pair/mint", headers={"X-Octowright-Token": _TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["ticket"]


def test_mint_unavailable_without_configured_token() -> None:
    from octowright.http.app import build_app

    with TestClient(build_app(mcp_leader=False, host="127.0.0.1", mcp_token="")) as client:
        assert client.post("/api/pair/mint").status_code == 503


def test_redeem_sets_cookie_and_ticket_is_single_use() -> None:
    with _client() as client:
        ticket = client.post("/api/pair/mint", headers={"X-Octowright-Token": _TOKEN}).json()["ticket"]
        resp = client.post("/api/pair/redeem", json={"ticket": ticket})
        assert resp.status_code == 200
        assert SESSION_COOKIE in resp.cookies
        # Replay of a consumed ticket fails.
        assert client.post("/api/pair/redeem", json={"ticket": ticket}).status_code == 403


def test_redeem_rejects_garbage() -> None:
    with _client() as client:
        assert client.post("/api/pair/redeem", json={"ticket": "bogus"}).status_code == 403
        assert client.post("/api/pair/redeem", content=b"not json").status_code == 400


def test_pair_page_served_unauthenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        resp = client.get("/pair")
        assert resp.status_code == 200
        assert "octowright" in resp.text


def test_api_gated_when_pairing_on_and_open_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    with _client() as client:
        # OFF (default): unchanged behavior.
        assert client.get("/api/sessions").status_code == 200
        monkeypatch.setenv(_ENV, "1")
        denied = client.get("/api/sessions")
        assert denied.status_code == 403
        assert "pairing" in denied.text
        # Token header still passes (follower/programmatic path).
        assert client.get("/api/sessions", headers={"X-Octowright-Token": _TOKEN}).status_code == 200


def test_api_open_after_pairing_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        ticket = client.post("/api/pair/mint", headers={"X-Octowright-Token": _TOKEN}).json()["ticket"]
        assert client.post("/api/pair/redeem", json={"ticket": ticket}).status_code == 200
        # TestClient carries the cookie forward automatically.
        assert client.get("/api/sessions").status_code == 200


def test_new_tab_exempt_from_pairing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Launched browsers land on /new-tab with no cookie; pairing must not break them.
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        assert client.get("/new-tab").status_code == 200
