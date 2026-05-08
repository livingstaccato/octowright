# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import time
from pathlib import Path

from octowright.http.artifacts import scan_recording_artifacts
from octowright.http.discovery import _tail_jsonl


def _write_large_recording(path: Path, *, total_events: int) -> None:
    rows: list[str] = []
    for index in range(total_events):
        if index == 0:
            action = "launch"
        elif index % 400 == 0:
            action = "download_saved"
        elif index % 25 == 0:
            action = "console"
        else:
            action = "click"
        rows.append(json.dumps({"ts": "2026-01-01T00:00:00Z", "action": action, "i": index}) + "\n")
    path.write_text("".join(rows), encoding="utf-8")


def test_scan_recording_artifacts_10k_guardrail(tmp_path: Path) -> None:
    jsonl = tmp_path / "20260101T000000Z-chromium-perf10000a.jsonl"
    _write_large_recording(jsonl, total_events=10_000)

    started = time.perf_counter()
    result = scan_recording_artifacts(jsonl)
    elapsed = time.perf_counter() - started

    assert result["event_count"] == 10_000
    assert result["console_count"] > 0
    assert result["download_count"] > 0
    # CI-stable upper bound: scan must stay comfortably sub-second on typical runners.
    assert elapsed < 2.0


def test_scan_and_tail_recording_100k_guardrail(tmp_path: Path) -> None:
    jsonl = tmp_path / "20260101T000000Z-chromium-perf10000b.jsonl"
    _write_large_recording(jsonl, total_events=100_000)

    started_scan = time.perf_counter()
    scan = scan_recording_artifacts(jsonl)
    scan_elapsed = time.perf_counter() - started_scan

    started_tail = time.perf_counter()
    tail = _tail_jsonl(jsonl, since=0)
    tail_elapsed = time.perf_counter() - started_tail

    assert scan["event_count"] == 100_000
    assert len(tail["events"]) == 100_000
    assert tail["complete"] is True
    # CI-stable bounds to catch accidental algorithmic regressions.
    assert scan_elapsed < 15.0
    assert tail_elapsed < 15.0
