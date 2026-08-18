# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A summary computed over ONE window must not read as whole-recording totals.

0.15.0 bounded ``recorder.tail_log`` to a per-call window so one
``?since=0`` on a long-lived recording could not pull gigabytes into the
leader. That was the right fix, but it silently changed what
``browser_tail_recording(response_mode="summary")`` MEANS: ``event_count`` and
``by_action`` used to describe the whole file and now describe only the bytes
scanned in that call.

The top-level ``complete`` flag does say the read was partial, but it sits
outside the ``summary`` block, so the counts still present as authoritative --
an agent reads ``event_count: 40000`` and reports "the recording has 40,000
events" for a recording holding ten times that.

Fixing it by scanning the whole file would reinstate the very allocation the
bound exists to prevent. So the summary states its own scope instead: the same
counts, plus a ``partial`` flag beside them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.server.browser import inspect_recording as _inspect


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _recording(path: Path, count: int, pad: int = 0) -> None:
    rows = [{"action": "click", "i": i, "pad": "x" * pad} for i in range(count)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _patch_pool(monkeypatch: pytest.MonkeyPatch, log_path: Path) -> None:
    session = MagicMock()
    session.log_path = str(log_path)
    pool = MagicMock()
    pool.get.return_value = session
    monkeypatch.setattr(_inspect, "pool", pool)


def _summary(monkeypatch: pytest.MonkeyPatch, log_path: Path, **kwargs: Any) -> dict[str, Any]:
    _patch_pool(monkeypatch, log_path)
    return _inspect.browser_tail_recording("i", response_mode="summary", **kwargs)


def test_a_truncated_summary_declares_itself_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The counts describe the scanned window, so the window must be visible
    at the same nesting level as the counts."""
    log_path = tmp_path / "r.jsonl"
    _recording(log_path, 400, pad=200)
    monkeypatch.setenv("OCTOWRIGHT_TAIL_MAX_BYTES", "2048")

    out = _summary(monkeypatch, log_path)

    assert out["complete"] is False, "precondition: the window must cut this file short"
    assert out["summary"]["partial"] is True
    assert out["summary"]["event_count"] < 400


def test_a_complete_summary_is_not_marked_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "r.jsonl"
    _recording(log_path, 5)

    out = _summary(monkeypatch, log_path)

    assert out["complete"] is True
    assert out["summary"]["partial"] is False
    assert out["summary"]["event_count"] == 5


def test_partial_tracks_the_read_not_the_event_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resuming from the returned cursor eventually reports a complete tail,
    so an agent following `next_actions` converges instead of re-reporting
    partial forever."""
    log_path = tmp_path / "r.jsonl"
    _recording(log_path, 200, pad=200)
    monkeypatch.setenv("OCTOWRIGHT_TAIL_MAX_BYTES", "4096")

    seen = 0
    cursor = 0
    for _ in range(50):
        out = _summary(monkeypatch, log_path, since=cursor)
        seen += out["summary"]["event_count"]
        cursor = out["cursor"]
        if out["complete"]:
            assert out["summary"]["partial"] is False
            break
    else:  # pragma: no cover - guards an infinite-loop regression
        pytest.fail("tail never reached a complete read")

    assert seen == 200
