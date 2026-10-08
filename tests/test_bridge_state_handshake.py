# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The bridge summary counts followers whose client holds another handshake.

A follower records, per connect, which ``initialize`` fields its client was
told that the leader it reached answers differently (``handshake_mismatch``:
a list, empty when they agree, None when it could not tell). A snapshot from a
follower too old to record it carries no field and is not counted: unlike a
missing version, a missing comparison says nothing about the handshake.
"""

from __future__ import annotations

import os
from pathlib import Path

from octowright import bridge_state


def _all_alive(_pid: int) -> bool:
    return True


def test_a_snapshot_records_the_handshake_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=path,
        follower_pid=os.getpid(),
        remote_url="http://127.0.0.1:6286/mcp/",
        remote_session_id=None,
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
        handshake_mismatch=["instructions"],
    )

    snapshot = bridge_state.read_state(path)["followers"][str(os.getpid())]

    assert snapshot["handshake_mismatch"] == ["instructions"]


def test_the_summary_counts_followers_whose_handshake_differs() -> None:
    data = {
        "followers": {
            "1": {"ts": 1.0, "handshake_mismatch": ["instructions"]},
            "2": {"ts": 2.0, "handshake_mismatch": ["capabilities", "instructions"]},
            "3": {"ts": 3.0, "handshake_mismatch": []},
            "4": {"ts": 4.0, "handshake_mismatch": None},
            "5": {"ts": 5.0},  # written before the field existed
            "6": {"ts": 6.0, "handshake_mismatch": "garbage"},
        },
        "events": [],
    }

    summary = bridge_state.summarize_state(data, is_alive=_all_alive)

    assert summary["handshake_mismatch_count"] == 2
    assert summary["handshake_mismatch_fields"] == {"capabilities": 1, "instructions": 2}
    assert summary["handshake_mismatch_hint"] == bridge_state._HANDSHAKE_MISMATCH_HINT


def test_no_mismatch_means_no_hint() -> None:
    data = {"followers": {"1": {"ts": 1.0, "handshake_mismatch": []}, "2": {"ts": 2.0}}, "events": []}

    summary = bridge_state.summarize_state(data, is_alive=_all_alive)

    assert summary["handshake_mismatch_count"] == 0
    assert summary["handshake_mismatch_fields"] == {}
    assert summary["handshake_mismatch_hint"] is None


def test_a_dead_follower_mismatch_is_not_counted() -> None:
    data = {"followers": {"1": {"ts": 1.0, "handshake_mismatch": ["instructions"]}}, "events": []}

    summary = bridge_state.summarize_state(data, is_alive=lambda _pid: False)

    assert summary["handshake_mismatch_count"] == 0
