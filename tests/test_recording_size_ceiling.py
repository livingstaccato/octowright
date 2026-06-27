# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-recording byte ceiling (disk-fill DoS guard).

OCTOWRIGHT_RECORDING_MAX_BYTES is OFF by default (unbounded, back-compat). When
set to a positive byte count, the recorder stops appending once the file would
exceed it, writing a single ``recording_truncated`` marker so replay/export see
the cut. Mirrors the opt-in posture of OCTOWRIGHT_MIN_FREE_MEMORY_MB /
OCTOWRIGHT_IDLE_GRACE.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.recorder import Recorder


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_unbounded_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OCTOWRIGHT_RECORDING_MAX_BYTES", raising=False)
    rec = Recorder(tmp_path / "r.jsonl")
    for i in range(200):
        rec.record("navigate", url=f"https://octowright.com/{i}")
    rec.close()

    rows = _read_lines(tmp_path / "r.jsonl")
    assert len(rows) == 200
    assert not any(r["action"] == "recording_truncated" for r in rows)


@pytest.mark.parametrize("disable_token", ["0", "off", "never", "none", "disabled", "-1"])
def test_falsey_tokens_disable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, disable_token: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", disable_token)
    rec = Recorder(tmp_path / "r.jsonl")
    for i in range(100):
        rec.record("navigate", url=f"https://octowright.com/{i}")
    rec.close()

    rows = _read_lines(tmp_path / "r.jsonl")
    assert len(rows) == 100
    assert not any(r["action"] == "recording_truncated" for r in rows)


def test_ceiling_truncates_and_marks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Small ceiling: a handful of records fit, then the recorder stops.
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "400")
    rec = Recorder(tmp_path / "r.jsonl")
    for i in range(100):
        rec.record("navigate", url=f"https://octowright.com/{i:04d}")
    rec.close()

    rows = _read_lines(tmp_path / "r.jsonl")
    markers = [r for r in rows if r["action"] == "recording_truncated"]
    data = [r for r in rows if r["action"] == "navigate"]

    assert len(markers) == 1, "exactly one truncation marker expected"
    assert rows[-1]["action"] == "recording_truncated", "marker must be the final line"
    assert len(data) < 100, "later records must be dropped"
    assert data, "at least one record should have been written before the cut"
    # File stayed under the ceiling plus one marker line.
    assert (tmp_path / "r.jsonl").stat().st_size <= 400 + 256


def test_truncation_marker_carries_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "300")
    rec = Recorder(tmp_path / "r.jsonl")
    for i in range(100):
        rec.record("navigate", url=f"https://octowright.com/{i:04d}")
    rec.close()

    marker = next(r for r in _read_lines(tmp_path / "r.jsonl") if r["action"] == "recording_truncated")
    assert marker["limit_bytes"] == 300
    assert marker["bytes_written"] >= 0


def test_dropped_records_do_not_grow_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "250")
    rec = Recorder(tmp_path / "r.jsonl")
    for i in range(50):
        rec.record("navigate", url=f"https://octowright.com/{i:04d}")
    size_after_truncation = (tmp_path / "r.jsonl").stat().st_size
    for i in range(50):  # all dropped
        rec.record("navigate", url=f"https://octowright.com/extra-{i:04d}")
    rec.close()

    assert (tmp_path / "r.jsonl").stat().st_size == size_after_truncation


def test_ceiling_counts_existing_bytes_on_reopen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Reopening an already-large recording must respect bytes already on disk."""
    path = tmp_path / "r.jsonl"
    monkeypatch.delenv("OCTOWRIGHT_RECORDING_MAX_BYTES", raising=False)
    rec = Recorder(path)
    for i in range(40):
        rec.record("navigate", url=f"https://octowright.com/{i:04d}")
    rec.close()
    already = path.stat().st_size
    assert already > 300

    # Reopen with a ceiling well below the current size: the very next record
    # must immediately truncate (counted existing bytes), not blow past it.
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "300")
    rec2 = Recorder(path)
    rec2.record("navigate", url="https://octowright.com/after-reopen")
    rec2.close()

    rows = _read_lines(path)
    assert rows[-1]["action"] == "recording_truncated"
    assert not any(r.get("url") == "https://octowright.com/after-reopen" for r in rows)
