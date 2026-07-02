# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The MCP session-idle-timeout leak fix (``http/app.py``).

The StreamableHTTP session manager defaults ``session_idle_timeout=None`` — it
never reaps abandoned/idle sessions, so each one's per-session server task +
transport lingers in the manager's task group forever (~54KB/session, unbounded;
a reconnect storm left a leader at 2.4GB with zero live browsers). ``build_app``
sets the timeout after the manager is built so idle sessions are reaped.
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
        ("garbage", None),  # unparsable → disabled (fail-safe to off, not a crash)
        ("  120  ", 120.0),  # whitespace tolerated
    ],
)
def test_mcp_session_idle_seconds_parsing(raw: str, expected: float | None) -> None:
    assert _app._mcp_session_idle_seconds(raw) == expected


def test_mcp_session_idle_seconds_default_is_300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", raising=False)
    assert _app._mcp_session_idle_seconds() == 300.0


def test_mcp_session_idle_seconds_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "45")
    assert _app._mcp_session_idle_seconds() == 45.0
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "off")
    assert _app._mcp_session_idle_seconds() is None


def test_apply_sets_manager_timeout_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "77")
    mgr = SimpleNamespace(session_idle_timeout=None)
    fake_mcp = SimpleNamespace(_session_manager=mgr)
    _app._apply_mcp_session_idle_timeout(fake_mcp)
    assert mgr.session_idle_timeout == 77.0


def test_apply_leaves_manager_default_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "off")
    mgr = SimpleNamespace(session_idle_timeout=None)
    fake_mcp = SimpleNamespace(_session_manager=mgr)
    _app._apply_mcp_session_idle_timeout(fake_mcp)
    assert mgr.session_idle_timeout is None  # untouched → mcp's leaky default (opt-out honored)


def test_apply_is_safe_when_manager_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "300")
    # No _session_manager attribute → must not raise (defensive).
    _app._apply_mcp_session_idle_timeout(SimpleNamespace())


def test_build_app_leader_sets_session_idle_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: build_app(mcp_leader=True) leaves the real StreamableHTTP session
    manager with a finite idle timeout (not the leaky None default)."""
    monkeypatch.setenv("OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS", "123")
    from octowright import http as _http
    from octowright.server import mcp as _mcp

    # Fresh manager for this build (FastMCP caches it lazily).
    _mcp._session_manager = None
    _http.build_app(mcp_leader=True)
    assert _mcp._session_manager is not None
    assert _mcp._session_manager.session_idle_timeout == 123.0
