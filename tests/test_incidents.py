# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Bounded incident ring (browser_pool.incidents) — recent crash / driver records."""

from __future__ import annotations

import pytest

from octowright.browser_pool import incidents


@pytest.fixture(autouse=True)
def _reset() -> None:
    incidents.reset()
    incidents._rebuild_ring()  # restore default maxlen if a prior test resized it


def test_record_returns_and_stores_with_timestamp() -> None:
    rec = incidents.record("renderer_crash", instance_id="abc", outcome="recovered")
    assert rec["category"] == "renderer_crash"
    assert rec["instance_id"] == "abc"
    assert rec["outcome"] == "recovered"
    assert isinstance(rec["ts"], str) and rec["ts"].endswith("Z")
    assert incidents.recent() == [rec]


def test_ring_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(incidents, "_RING_SIZE", 3)
    incidents._rebuild_ring()
    for i in range(5):
        incidents.record("renderer_crash", instance_id=str(i))
    got = incidents.recent()
    assert len(got) == 3  # oldest two evicted
    assert [r["instance_id"] for r in got] == ["2", "3", "4"]


def test_recent_limit_returns_newest() -> None:
    for i in range(5):
        incidents.record("driver_restart", n=i)
    last2 = incidents.recent(limit=2)
    assert [r["n"] for r in last2] == [3, 4]


def test_recent_filter_by_category() -> None:
    incidents.record("renderer_crash", instance_id="a")
    incidents.record("driver_restart", n=1)
    incidents.record("renderer_crash", instance_id="b")
    crashes = incidents.recent(category="renderer_crash")
    assert [r["instance_id"] for r in crashes] == ["a", "b"]
    assert incidents.recent(category="driver_restart")[0]["n"] == 1


def test_counts_by_outcome() -> None:
    incidents.record("renderer_crash", outcome="recovered")
    incidents.record("renderer_crash", outcome="recovered")
    incidents.record("renderer_crash", outcome="failed")
    incidents.record("renderer_crash", instance_id="no-outcome")  # outcome-less → ignored
    counts = incidents.counts(category="renderer_crash")
    assert counts == {"recovered": 2, "failed": 1}
