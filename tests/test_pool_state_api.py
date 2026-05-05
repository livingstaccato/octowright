# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from types import SimpleNamespace

from octowright.pool import BrowserPool


def test_pool_public_state_api_reads_sessions_without_private_callers() -> None:
    pool = BrowserPool()
    session = SimpleNamespace(
        instance_id="abc123",
        kind="webkit",
        label="demo",
        profile="demo",
        url="https://example.com",
        log_path="/tmp/demo.jsonl",
        har_path=None,
    )
    pool._sessions["abc123"] = session  # type: ignore[assignment]

    assert pool.has_session("abc123") is True
    assert pool.maybe_get("abc123") is session
    assert list(pool.iter_sessions()) == [session]
    assert pool.active_count() == 1
    assert pool.list_sessions() == [
        {
            "instance_id": "abc123",
            "kind": "webkit",
            "label": "demo",
            "profile": "demo",
            "url": "https://example.com",
            "log_path": "/tmp/demo.jsonl",
            "har_path": None,
        }
    ]
