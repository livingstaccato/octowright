# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``capture_search`` bounds the cost of a caller's regex.

``re`` holds the GIL for the whole of a match, so a catastrophic pattern run
over a multi-megabyte capture froze every thread in the leader -- the MCP
transport, the dashboard, every browser's event handling -- for as long as the
backtracking took, and nothing could interrupt it.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from octowright import capture_regex, captures
from octowright.request_errors import InvalidRequestError


def _save(tmp_path: Path, content: str) -> str:
    saved = captures.save_capture(kind="text", content=content, root=tmp_path, max_total_bytes=10_000_000)
    return str(saved["capture_id"])


def test_a_regex_search_still_finds_its_matches(tmp_path: Path) -> None:
    capture_id = _save(tmp_path, "id=12\nid=345\nother")

    found = captures.search_capture(capture_id, r"^id=(\d+)$", regex=True, context_chars=0, root=tmp_path)

    assert [(m["start"], m["end"]) for m in found["matches"]] == [(0, 5), (6, 12)]


def test_a_catastrophic_regex_is_stopped_at_the_time_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capture_regex, "REGEX_SEARCH_TIMEOUT_SECONDS", 1.0)
    capture_id = _save(tmp_path, "a" * 64 + "!")

    started = time.monotonic()
    with pytest.raises(InvalidRequestError, match="did not finish within"):
        captures.search_capture(capture_id, r"(a+)+$", regex=True, root=tmp_path)

    # Unbounded, (a+)+$ over 64 a's backtracks for longer than the universe has existed.
    assert time.monotonic() - started < 15


def test_an_invalid_regex_is_the_callers_error(tmp_path: Path) -> None:
    capture_id = _save(tmp_path, "text")

    with pytest.raises(InvalidRequestError, match="invalid regex"):
        captures.search_capture(capture_id, "(unclosed", regex=True, root=tmp_path)
