# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Leader-mode observability.

The default ``octowright serve`` spawns a detached daemon and runs as a
follower; it only runs the leader *inline* (in the client's own process) as a
fallback when the daemon spawn times out. That inline leader is fragile — if
the client dies, every browser dies with it. These tests cover the signals
that make the fragile state visible: ``octowright_status()["daemon"]["mode"]``
and the loud stderr warning emitted on the fallback.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server import _state
from octowright.server.meta import octowright_status


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_leader_mode() -> None:
    _state.set_leader_mode("unknown", inline_reason=None)
    yield  # type: ignore[misc]
    _state.set_leader_mode("unknown", inline_reason=None)


def test_set_leader_mode_roundtrip() -> None:
    _state.set_leader_mode("daemon")
    assert _state.leader_mode_snapshot() == {"mode": "daemon", "inline_reason": None}
    _state.set_leader_mode("inline", inline_reason="no_singleton")
    assert _state.leader_mode_snapshot() == {"mode": "inline", "inline_reason": "no_singleton"}


def test_leader_mode_snapshot_returns_a_copy() -> None:
    snap = _state.leader_mode_snapshot()
    snap["mode"] = "tampered"
    assert _state.leader_mode_snapshot()["mode"] == "unknown"


def test_status_surfaces_leader_mode_default() -> None:
    snap = octowright_status()
    assert snap["daemon"]["mode"] == "unknown"
    assert snap["daemon"]["inline_reason"] is None


def test_status_surfaces_inline_fallback_mode() -> None:
    _state.set_leader_mode("inline", inline_reason="daemon_spawn_failed")
    snap = octowright_status()
    assert snap["daemon"]["mode"] == "inline"
    assert snap["daemon"]["inline_reason"] == "daemon_spawn_failed"


def test_status_surfaces_daemon_mode() -> None:
    _state.set_leader_mode("daemon")
    snap = octowright_status()
    assert snap["daemon"]["mode"] == "daemon"
    assert snap["daemon"]["inline_reason"] is None


def test_inline_fallback_warning_names_the_risk() -> None:
    from octowright.cli.serve import _INLINE_FALLBACK_WARNING

    text = _INLINE_FALLBACK_WARNING.lower()
    # The warning must make the degraded state and its consequence obvious.
    assert "inline" in text
    assert "leader" in text
    assert "browser" in text


# --- Regression: the actual serve-path wiring, not just the unit state ---


@pytest.mark.anyio
async def test_inline_fallback_path_records_mode_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the detached daemon spawn times out, _ensure_leader_or_inline must
    run the leader inline AND record the fragile mode AND warn loudly."""
    import octowright.daemonize as _daemonize_mod
    import octowright.singleton as _sn_mod
    from octowright.cli import serve as _serve

    monkeypatch.setattr(_sn_mod, "read_lock", lambda: None)  # no live leader
    monkeypatch.setattr(_sn_mod, "is_stale", lambda _info: True)
    monkeypatch.setattr(_sn_mod, "probe_http_alive", AsyncMock(return_value=False))
    monkeypatch.setattr(_daemonize_mod, "spawn_daemon", MagicMock())
    monkeypatch.setattr(_daemonize_mod, "wait_for_daemon", AsyncMock(return_value=None))  # spawn times out
    run_leader = AsyncMock()
    monkeypatch.setattr(_serve, "_run_leader", run_leader)
    captured: list[str] = []
    monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))

    result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)

    assert result is None  # inline fallback → caller returns
    run_leader.assert_awaited_once()
    assert _state.leader_mode_snapshot() == {"mode": "inline", "inline_reason": "daemon_spawn_failed"}
    assert any("INLINE" in line for line in captured), captured


@pytest.mark.anyio
async def test_following_existing_leader_leaves_mode_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client that finds a live leader becomes a follower — it must NOT run
    the leader or claim a leader mode (so it never falsely reports daemon/inline
    while another MCP client owns the daemon)."""
    import octowright.singleton as _sn_mod
    from octowright.cli import serve as _serve

    fake_info = MagicMock(mcp_url="http://127.0.0.1:8765/mcp/")
    monkeypatch.setattr(_sn_mod, "read_lock", lambda: fake_info)
    monkeypatch.setattr(_sn_mod, "is_stale", lambda _info: False)
    monkeypatch.setattr(_sn_mod, "probe_http_alive", AsyncMock(return_value=True))
    run_leader = AsyncMock()
    monkeypatch.setattr(_serve, "_run_leader", run_leader)

    result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)

    assert result is fake_info  # found the live leader → follow it
    run_leader.assert_not_awaited()
    assert _state.leader_mode_snapshot()["mode"] == "unknown"
