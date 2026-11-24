# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The MCP session-idle-timeout leak fix (``http/app.py``).

``build_app`` pins the StreamableHTTP session manager's idle timeout to
``OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS``: a number when opted in, ``None`` (never
reap) otherwise. mcp 2.1 defaulted it to ``None``; mcp 2.2.0 changed the
default to 1800s, so "off" has to be written explicitly or every session idle
for 30 minutes is reaped and its client must reconnect by hand.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octowright.http import app as _app


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("300", 300.0),
        ("600", 600.0),
        ("30.5", 30.5),
        ("0", None),
        ("off", None),
        ("never", None),
        ("none", None),
        ("disabled", None),
        ("false", None),
        ("no", None),
        ("-5", None),  # non-positive → disabled
        ("garbage", None),  # unparsable → off, with a warning (see below), not a crash
        ("  120  ", 120.0),  # whitespace tolerated
    ],
)
def test_mcp_session_idle_seconds_parsing(raw: str, expected: float | None) -> None:
    assert _app._mcp_session_idle_seconds(raw) == expected


def test_mcp_session_idle_seconds_default_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", raising=False)
    assert _app._mcp_session_idle_seconds() is None


def test_mcp_session_idle_seconds_opt_in_via_positive_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "1800")
    assert _app._mcp_session_idle_seconds() == 1800.0


def test_mcp_session_idle_seconds_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "45")
    assert _app._mcp_session_idle_seconds() == 45.0
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "off")
    assert _app._mcp_session_idle_seconds() is None


def test_unparsable_value_is_off_and_warned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in an opt-in ("1800s", "30m") must not look like a deliberate off."""
    warnings: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(_app, "log", SimpleNamespace(warning=lambda event, **kw: warnings.append((event, kw))))
    for raw in ("1800s", "30m", "inf", "nan"):
        assert _app._mcp_session_idle_seconds(raw) is None
    assert [kw["raw"] for event, kw in warnings if event == "octowright.mcp.session_idle_timeout_unparsable"] == [
        "1800s",
        "30m",
        "inf",
        "nan",
    ]
    warnings.clear()
    for raw in ("off", "0", "-5", "300"):
        _app._mcp_session_idle_seconds(raw)
    assert warnings == []


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        (SimpleNamespace(session_idle_timeout=None), None),
        (SimpleNamespace(session_idle_timeout=77.0), 77.0),
    ],
)
def test_confirm_accepts_a_manager_holding_the_value(manager: object, expected: float | None) -> None:
    assert _app._confirm_mcp_session_idle_timeout(SimpleNamespace(session_manager=manager), expected) is True


@pytest.mark.parametrize(
    "fake_mcp",
    [
        SimpleNamespace(),  # no session manager at all
        SimpleNamespace(session_manager=SimpleNamespace()),  # attribute renamed or removed upstream
        SimpleNamespace(session_manager=SimpleNamespace(session_idle_timeout=1800)),  # value not taken
    ],
)
def test_confirm_warns_when_the_timeout_did_not_take(monkeypatch: pytest.MonkeyPatch, fake_mcp: object) -> None:
    """Each case leaves the library default possibly reaping live sessions, so
    it must be loud rather than a silent no-op."""
    warnings: list[str] = []
    monkeypatch.setattr(_app, "log", SimpleNamespace(warning=lambda event, **_kw: warnings.append(event)))
    assert _app._confirm_mcp_session_idle_timeout(fake_mcp, None) is False
    assert warnings == ["octowright.mcp.session_idle_timeout_unconfirmed"]


def test_installed_mcp_still_reaps_by_default() -> None:
    """Pins the premise of the fix against the installed library: left alone,
    the session manager reaps idle sessions. If a release goes back to never
    reaping, this fails and the explicit off can be reconsidered."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("idle-default-probe")
    server.streamable_http_app()
    assert server.session_manager.session_idle_timeout is not None


def test_build_app_leader_sets_session_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: build_app(mcp_leader=True) leaves the real StreamableHTTP
    session manager with the opted-in finite idle timeout."""
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "123")
    from octowright import http as _http
    from octowright.server import mcp as _mcp

    _http.build_app(mcp_leader=True)
    assert _mcp.session_manager.session_idle_timeout == 123.0


def test_build_app_leader_default_never_reaps_idle_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end against the installed mcp: with the knob unset, the real
    manager must not reap. Its own default (1800s from mcp 2.2.0) is what
    disconnected clients after a 30-minute pause."""
    monkeypatch.delenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", raising=False)
    from octowright import http as _http
    from octowright.server import mcp as _mcp

    _http.build_app(mcp_leader=True)
    assert _mcp.session_manager.session_idle_timeout is None


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
_MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


def _ping_after_idle(monkeypatch: pytest.MonkeyPatch, raw: str, idle: float) -> int:
    """Open a real session on the leader app, stay silent for ``idle`` seconds,
    then ping it; return the ping's HTTP status."""
    import time

    from starlette.testclient import TestClient

    from octowright import http as _http

    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", raw)
    # The SDK's transport security wants a loopback Host with a port.
    with TestClient(_http.build_app(mcp_leader=True), base_url="http://127.0.0.1:6286") as client:
        init = client.post("/mcp/", json=_INIT, headers=_MCP_HEADERS)
        assert init.status_code == 200, init.text
        session = {**_MCP_HEADERS, "mcp-session-id": init.headers["mcp-session-id"]}
        client.post("/mcp/", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=session)
        time.sleep(idle)
        return client.post("/mcp/", json={"jsonrpc": "2.0", "id": 2, "method": "ping"}, headers=session).status_code


def test_configured_timeout_reaches_the_reaper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioural: the value build_app passes is the one the library reaps by.
    A session idle past a short opted-in timeout is gone (404)."""
    assert _ping_after_idle(monkeypatch, "0.3", idle=1.5) == 404


def test_off_keeps_an_idle_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavioural counterpart: with the knob off, the same idle gap keeps the
    session. Together with the test above, this shows the setting is the
    reaper's live input, not an attribute nothing reads."""
    assert _ping_after_idle(monkeypatch, "off", idle=1.5) == 200
