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

import pytest

from octowright.server import _state
from octowright.server.meta import octowright_status


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
