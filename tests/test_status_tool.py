# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright_status`` — first-touch session banner snapshot."""

from __future__ import annotations

import os

from octowright.server.meta import octowright_status


def test_status_returns_required_top_level_blocks() -> None:
    """The status snapshot must include daemon, defaults, pool, personas, dashboard_url."""
    snap = octowright_status()
    for key in ("daemon", "defaults", "pool", "personas", "dashboard_url"):
        assert key in snap, f"missing top-level field {key!r}: {snap}"


def test_status_defaults_block_advertises_persistent_default() -> None:
    """The whole point of the banner — confirm ephemeral_default=False."""
    snap = octowright_status()
    assert snap["defaults"]["ephemeral_default"] is False


def test_status_includes_idle_grace_and_badge_position() -> None:
    snap = octowright_status()
    assert "idle_grace_seconds" in snap["defaults"]
    assert isinstance(snap["defaults"]["idle_grace_seconds"], int | float)
    assert snap["defaults"]["badge_position_default"] == "bottom-right"


def test_status_daemon_block_reports_this_pid() -> None:
    """this_pid must always be the calling process; lets the user/agent
    distinguish 'I am the daemon' from 'I am bridging to a daemon'."""
    snap = octowright_status()
    assert snap["daemon"]["this_pid"] == os.getpid()


def test_status_pool_counts_are_ints() -> None:
    snap = octowright_status()
    assert isinstance(snap["pool"]["live_browsers"], int)
    assert isinstance(snap["pool"]["live_scenarios"], int)


def test_status_personas_returns_name_list() -> None:
    snap = octowright_status()
    assert "names" in snap["personas"]
    assert isinstance(snap["personas"]["names"], list)
    assert snap["personas"]["count"] == len(snap["personas"]["names"])
