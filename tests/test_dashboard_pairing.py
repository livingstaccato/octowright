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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from octowright.http.pairing import (
    DASHBOARD_SESSION_TTL_SECONDS,
    DASHBOARD_STATE_ATTR,
    DASHBOARD_WS_BEARER_PREFIX,
    DASHBOARD_WS_PROTOCOL,
    MAX_DASHBOARD_SESSIONS,
    MAX_PAIR_CODES,
    PAIR_CODE_TTL_SECONDS,
    DashboardPairingState,
    dashboard_access_ok,
    pairing_required,
)

_TOKEN = "test-cap-token"
_ENV = "OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING"


@pytest.fixture(autouse=True)
def _fresh_pairing_state(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Default the opt-in knob to unset (off)."""
    monkeypatch.delenv(_ENV, raising=False)
    yield


def _request(
    headers: dict[str, str] | None = None,
    path: str = "/x",
    pairing: DashboardPairingState | None = None,
) -> Request:
    hdrs = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    app = Starlette()
    setattr(app.state, DASHBOARD_STATE_ATTR, pairing or DashboardPairingState(expected_token=_TOKEN))
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "headers": hdrs,
        "query_string": b"",
        "path": path,
        "app": app,
    }

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


# --- ticket / session store ---------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _redeem(state: DashboardPairingState) -> str:
    grant = state.redeem_code(state.mint_code())
    assert grant is not None
    return grant.bearer


def test_pairing_state_redeems_code_once_and_returns_random_bearer() -> None:
    state = DashboardPairingState(expected_token=_TOKEN)
    code = state.mint_code()
    grant = state.redeem_code(code)
    assert grant is not None
    assert grant.bearer != code
    assert grant.expires_at > 0
    assert state.bearer_ok(grant.bearer)
    assert state.redeem_code(code) is None


def test_pairing_state_expires_codes_and_bearers() -> None:
    clock = _Clock()
    wall = _Clock()
    state = DashboardPairingState(expected_token=_TOKEN, monotonic_clock=clock, wall_clock=wall)
    code = state.mint_code()
    clock.now += PAIR_CODE_TTL_SECONDS + 1.0
    assert state.redeem_code(code) is None

    bearer = _redeem(state)
    clock.now += DASHBOARD_SESSION_TTL_SECONDS + 1.0
    assert not state.bearer_ok(bearer)


def test_pairing_state_code_store_is_bounded() -> None:
    state = DashboardPairingState(expected_token=_TOKEN)
    codes = [state.mint_code() for _ in range(MAX_PAIR_CODES + 1)]
    assert state.redeem_code(codes[0]) is None
    assert state.redeem_code(codes[-1]) is not None


def test_pairing_state_session_store_is_true_lru() -> None:
    state = DashboardPairingState(expected_token=_TOKEN, max_sessions=3)
    oldest, touched, newest = [_redeem(state) for _ in range(3)]
    assert state.bearer_ok(oldest)
    replacement = _redeem(state)
    assert state.bearer_ok(oldest)
    assert not state.bearer_ok(touched)
    assert state.bearer_ok(newest)
    assert state.bearer_ok(replacement)


def test_pairing_state_invalid_bearer_does_not_change_lru_order() -> None:
    state = DashboardPairingState(expected_token=_TOKEN, max_sessions=2)
    oldest, newest = [_redeem(state) for _ in range(2)]
    assert not state.bearer_ok("invalid")
    replacement = _redeem(state)
    assert not state.bearer_ok(oldest)
    assert state.bearer_ok(newest)
    assert state.bearer_ok(replacement)


def test_pairing_state_never_exposes_raw_credentials_in_repr(caplog: pytest.LogCaptureFixture) -> None:
    state = DashboardPairingState(expected_token=_TOKEN)
    code = state.mint_code()
    bearer = _redeem(state)
    rendered = repr(state)
    assert code not in rendered
    assert bearer not in rendered
    assert _TOKEN not in rendered
    assert code not in caplog.text
    assert bearer not in caplog.text


def test_pairing_state_defaults_are_bounded_and_positive() -> None:
    assert MAX_PAIR_CODES > 0
    assert MAX_DASHBOARD_SESSIONS > 0
    assert PAIR_CODE_TTL_SECONDS > 0
    assert DASHBOARD_SESSION_TTL_SECONDS > 0


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


def test_access_ok_with_valid_origin_scoped_bearer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    state = DashboardPairingState(expected_token=_TOKEN)
    bearer = _redeem(state)
    assert dashboard_access_ok(_request({"authorization": f"Bearer {bearer}"}, pairing=state))


def test_access_ignores_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    state = DashboardPairingState(expected_token=_TOKEN)
    bearer = _redeem(state)
    assert not dashboard_access_ok(_request({"cookie": f"octowright_dash={bearer}"}, pairing=state))


@pytest.mark.parametrize(
    "authorization",
    ["", "Basic abc", "Bearer", "Bearer ", "Bearer one two", "Bearer\ttoken"],
)
def test_access_rejects_malformed_authorization(
    monkeypatch: pytest.MonkeyPatch,
    authorization: str,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    assert not dashboard_access_ok(_request({"authorization": authorization}))


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
    state = DashboardPairingState(expected_token="")
    assert not dashboard_access_ok(_request({"x-octowright-token": ""}, pairing=state))


def test_bearer_is_local_to_one_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    first = DashboardPairingState(expected_token=_TOKEN)
    second = DashboardPairingState(expected_token=_TOKEN)
    bearer = _redeem(first)
    assert dashboard_access_ok(_request({"authorization": f"Bearer {bearer}"}, pairing=first))
    assert not dashboard_access_ok(_request({"authorization": f"Bearer {bearer}"}, pairing=second))


# --- routes (end-to-end through the real app) ---------------------------------


def _app() -> Starlette:
    from octowright.http.app import build_app

    return build_app(mcp_leader=False, host="127.0.0.1", mcp_token=_TOKEN)


def _client() -> TestClient:
    return TestClient(_app())


def _pair(client: TestClient) -> str:
    code = client.post("/api/pair/mint", headers={"X-Octowright-Token": _TOKEN}).json()["code"]
    response = client.post("/api/pair/redeem", json={"code": code})
    assert response.status_code == 200
    return str(response.json()["bearer"])


def test_mint_requires_capability_token() -> None:
    with _client() as client:
        assert client.post("/api/pair/mint").status_code == 403
        assert client.post("/api/pair/mint", headers={"X-Octowright-Token": "nope"}).status_code == 403
        resp = client.post("/api/pair/mint", headers={"X-Octowright-Token": _TOKEN})
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"]
        assert body["expires_in"] == int(PAIR_CODE_TTL_SECONDS)


def test_mint_unavailable_without_configured_token() -> None:
    from octowright.http.app import build_app

    with TestClient(build_app(mcp_leader=False, host="127.0.0.1", mcp_token="")) as client:
        assert client.post("/api/pair/mint").status_code == 503


def test_redeem_returns_no_store_bearer_and_code_is_single_use() -> None:
    with _client() as client:
        code = client.post("/api/pair/mint", headers={"X-Octowright-Token": _TOKEN}).json()["code"]
        resp = client.post("/api/pair/redeem", json={"code": code})
        assert resp.status_code == 200
        assert set(resp.json()) == {"bearer", "expires_at"}
        assert resp.json()["bearer"]
        assert resp.json()["expires_at"] > 0
        assert resp.headers["cache-control"] == "no-store"
        assert "set-cookie" not in resp.headers
        assert client.post("/api/pair/redeem", json={"code": code}).status_code == 403


def test_redeem_rejects_garbage() -> None:
    with _client() as client:
        assert client.post("/api/pair/redeem", json={"code": "bogus"}).status_code == 403
        assert client.post("/api/pair/redeem", json={}).status_code == 403
        assert client.post("/api/pair/redeem", content=b"not json").status_code == 415
        assert (
            client.post(
                "/api/pair/redeem",
                content=b"not json",
                headers={"content-type": "application/json"},
            ).status_code
            == 400
        )


def test_redeem_honors_streaming_body_cap_without_trusting_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", "64")
    with _client() as client:
        oversized = b'{"code":"' + (b"x" * 100) + b'"}'
        response = client.post(
            "/api/pair/redeem",
            content=oversized,
            headers={"content-type": "application/json", "content-length": "2"},
        )
        assert response.status_code == 413


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
        assert denied.status_code == 401
        assert "pairing" in denied.text
        # Token header still passes (follower/programmatic path).
        assert client.get("/api/sessions", headers={"X-Octowright-Token": _TOKEN}).status_code == 200


def test_api_open_after_pairing_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        bearer = _pair(client)
        assert client.get("/api/sessions", headers={"Authorization": f"Bearer {bearer}"}).status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/api/sessions", "/api/sessions/missing/video", "/api/dashboard/events"],
)
def test_sensitive_http_json_media_and_sse_require_bearer(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


@pytest.mark.parametrize(
    "path",
    ["/api/sessions", "/api/sessions/missing/video"],
)
def test_sensitive_http_json_and_media_accept_bearer(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        bearer = _pair(client)
        assert client.get(path, headers={"Authorization": f"bEaReR {bearer}"}).status_code != 401


@pytest.mark.parametrize(
    "path",
    ["/api/sessions/missing/tail", "/api/sessions/missing/screencast"],
)
def test_dashboard_websockets_require_private_bearer_protocol(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    with TestClient(_app()) as client:
        with (
            pytest.raises(WebSocketDisconnect) as missing,
            client.websocket_connect(path, subprotocols=[DASHBOARD_WS_PROTOCOL]),
        ):
            pass
        assert missing.value.code == 1008

        with (
            pytest.raises(WebSocketDisconnect) as wrong,
            client.websocket_connect(
                path,
                subprotocols=[DASHBOARD_WS_PROTOCOL, f"{DASHBOARD_WS_BEARER_PREFIX}wrong"],
            ),
        ):
            pass
        assert wrong.value.code == 1008


@pytest.mark.parametrize(
    "path",
    ["/api/sessions/missing/tail", "/api/sessions/missing/screencast"],
)
def test_dashboard_websockets_negotiate_only_stable_protocol(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        bearer = _pair(client)
        secret_protocol = f"{DASHBOARD_WS_BEARER_PREFIX}{bearer}"
        with client.websocket_connect(
            path,
            subprotocols=[DASHBOARD_WS_PROTOCOL, secret_protocol],
        ) as websocket:
            assert websocket.accepted_subprotocol == DASHBOARD_WS_PROTOCOL
            with pytest.raises(WebSocketDisconnect):
                websocket.receive_json()


def test_new_tab_exempt_from_pairing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Launched browsers land on /new-tab with no cookie; pairing must not break them.
    monkeypatch.setenv(_ENV, "1")
    with _client() as client:
        assert client.get("/new-tab").status_code == 200
