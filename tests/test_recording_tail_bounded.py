# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``tail_log`` must not size its memory use to the recording on disk.

The read was ``fh.read()`` with no bound. A recording has no ceiling by default
(``OCTOWRIGHT_RECORDING_MAX_BYTES`` is off), and a long session -- or a page
spewing console output -- grows the JSONL for as long as the browser lives. One
``GET /api/sessions/{id}/events?since=0`` then pulls the whole file into the
leader, splits it into lines and ``json.loads`` every one: several GB of file
becomes many times that in Python objects, inside the process that owns every
live browser.

Every caller already speaks the cursor protocol -- ``browser_tail_recording``
documents "pass the returned cursor back as since", ``discovery`` returns
``complete``, ``ScenarioPool.tail`` returns ``cursors`` -- so a bounded read is
contract-compatible: it just means ``complete`` is False for another round.

The bound introduces one hazard of its own, pinned below: a single line LONGER
than the window contains no newline, and the pre-existing "no newline means a
partial trailing line, wait" branch would then hold the cursor still forever
while the caller polls. Trading an OOM for a livelock is not a fix.
"""

from __future__ import annotations

import json
from pathlib import Path

from octowright.recorder import tail_log


def _write(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def test_read_is_bounded_and_resumes_from_the_returned_cursor(tmp_path: Path) -> None:
    """The whole file still arrives -- across calls, not in one allocation."""
    target = tmp_path / "r.jsonl"
    events = [{"action": "click", "i": i, "pad": "x" * 200} for i in range(200)]
    _write(target, events)

    first, cursor, total = tail_log(target, 0, max_bytes=2048)

    assert 0 < len(first) < len(events), "the window must cut the file short"
    assert cursor < total

    seen = list(first)
    while cursor < total:
        batch, cursor, total = tail_log(target, cursor, max_bytes=2048)
        assert batch, "the cursor must advance every round"
        seen.extend(batch)

    assert [e["i"] for e in seen] == list(range(200))


def test_the_window_never_splits_a_line(tmp_path: Path) -> None:
    """The cursor lands on a line boundary, so no event is half-parsed."""
    target = tmp_path / "r.jsonl"
    _write(target, [{"action": "click", "i": i, "pad": "y" * 100} for i in range(50)])

    _events, cursor, _total = tail_log(target, 0, max_bytes=333)

    assert target.read_bytes()[cursor - 1 : cursor] == b"\n"


def test_a_line_longer_than_the_window_does_not_stall_the_cursor(tmp_path: Path) -> None:
    """An oversized line cannot be buffered -- that is the OOM we came to stop --
    but leaving the cursor put makes every subsequent poll re-read the same
    window and return nothing, forever. Skip past it and keep going."""
    target = tmp_path / "r.jsonl"
    huge = json.dumps({"action": "console", "text": "z" * 5000})
    target.write_text(huge + "\n" + json.dumps({"action": "click", "i": 1}) + "\n", encoding="utf-8")

    events, cursor, total = tail_log(target, 0, max_bytes=512)

    assert cursor > 0, "the cursor must move past the unreadable line"
    follow_on, cursor, total = tail_log(target, cursor, max_bytes=512)
    assert [e.get("i") for e in [*events, *follow_on]] == [1]
    assert cursor == total


def test_an_incomplete_trailing_line_still_holds_the_cursor(tmp_path: Path) -> None:
    """The pre-existing partial-write behaviour, unchanged: a fragment the writer
    has not finished is NOT skipped -- it is re-read once the newline lands."""
    target = tmp_path / "r.jsonl"
    target.write_text(json.dumps({"action": "click", "i": 1}) + "\n" + '{"action": "cli', encoding="utf-8")

    events, cursor, _total = tail_log(target, 0, max_bytes=4096)

    assert [e["i"] for e in events] == [1]

    with target.open("a", encoding="utf-8") as fh:
        fh.write('ck", "i": 2}\n')
    rest, _cursor, _total = tail_log(target, cursor, max_bytes=4096)
    assert [e["i"] for e in rest] == [2]


def test_an_unfilled_window_short_circuits_without_scanning_ahead(tmp_path: Path, monkeypatch) -> None:
    """A read that came back SHORTER than the window hit EOF, so there is nothing
    ahead to scan -- and scanning anyway is not merely wasted work. The recorder
    appends concurrently, so a scan launched here could pick up bytes written
    after the first read and "skip past" a line that was only ever a partial
    write, dropping a real event. The length check is what makes the skip apply
    to genuinely oversized lines only.
    """
    target = tmp_path / "r.jsonl"
    target.write_text('{"action": "cli', encoding="utf-8")

    from octowright import recorder

    def _must_not_scan(*_args: object, **_kwargs: object) -> int | None:
        raise AssertionError("a partial trailing line must not trigger a forward scan")

    monkeypatch.setattr(recorder, "_offset_after_next_newline", _must_not_scan)

    assert recorder.tail_log(target, 0, max_bytes=4096) == ([], 0, 15)


def test_the_bound_is_on_by_default(tmp_path: Path) -> None:
    """A caller that passes nothing must still be protected -- the OOM path is
    `discovery.get_events`, which passes only a cursor."""
    target = tmp_path / "r.jsonl"
    _write(target, [{"action": "click", "i": i} for i in range(20)])

    from octowright import recorder

    assert recorder._tail_max_bytes() is not None
    assert recorder._tail_max_bytes() > 0


def test_the_bound_can_be_disabled_for_back_compat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_TAIL_MAX_BYTES", "off")
    from octowright import recorder

    assert recorder._tail_max_bytes() is None
