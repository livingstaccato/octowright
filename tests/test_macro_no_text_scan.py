# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``expect_no_text`` reports, and how it treats frames and the Chromium snapshot.

The rendered-text scan itself is proven on real engines in
``test_macro_no_text_live.py``; this file covers the Python side: the result a
caller can tell a vacuous pass by, a truncated scan's refusal, frames that go
away mid-scan, and the snapshot's definition of drawn text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import Error as PlaywrightError

from octowright.defaults import REDACTED_ASSERTION_TEXT
from octowright.drawn_text import ELEMENT_LIMIT, NO_TEXT_OBSERVATION_KEYS, contains
from octowright.macros.lint import lint_macro
from octowright.macros.runtime import _normalize_replay_kwargs
from octowright.session.core import BrowserSession
from octowright.session.rendered_text import snapshot_drawn_text

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


def _frame(*pieces: str, matched: int = 1, truncated: bool = False, detached: bool = False) -> MagicMock:
    frame = MagicMock()
    frame.is_detached = MagicMock(return_value=detached)
    frame.evaluate = AsyncMock(return_value={"pieces": list(pieces), "matched": matched, "truncated": truncated})
    return frame


def _session(tmp_path: Path, kind: str = "chromium", frames: list[Any] | None = None) -> BrowserSession:
    page = MagicMock()
    page.url = "https://octowright.com/"
    page.frames = frames if frames is not None else [_frame()]
    page.evaluate = AsyncMock(side_effect=AssertionError("the page is scanned frame by frame"))
    session = BrowserSession(
        instance_id="test",
        kind=kind,
        label="test-label",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "test.jsonl",
        recorder=MagicMock(),
    )
    session._snapshot_leaks = AsyncMock(return_value=False)  # type: ignore[method-assign]
    return session


# --- F: the result tells a vacuous pass from a real one ------------------------------


@pytest.mark.anyio
async def test_the_result_says_what_was_scanned(tmp_path: Path) -> None:
    session = _session(tmp_path, frames=[_frame("Welcome"), _frame("ad")])
    result = await session.expect_no_text(SECRET)
    assert result == {"matched": 2, "frames_scanned": 2, "frames_skipped": 0, "truncated": False, "snapshot": "checked"}


@pytest.mark.anyio
async def test_nothing_matched_passes_and_says_so(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.page.evaluate = AsyncMock(return_value={"pieces": [], "matched": 0, "truncated": False})
    result = await session.expect_no_text(SECRET, selector="#error-banner")
    assert result["matched"] == 0
    assert result["snapshot"] == "skipped"


@pytest.mark.anyio
async def test_other_engines_report_the_snapshot_unsupported(tmp_path: Path) -> None:
    session = _session(tmp_path, kind="firefox")
    assert (await session.expect_no_text(SECRET))["snapshot"] == "unsupported"
    session._snapshot_leaks.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.anyio
async def test_the_recording_carries_the_result_never_the_text(tmp_path: Path) -> None:
    from octowright.macros.privacy import assertion_text_digest

    session = _session(tmp_path)
    await session.expect_no_text(SECRET, selector="body")
    session.recorder.record.assert_called_with(
        "expect_no_text",
        selector="body",
        text=REDACTED_ASSERTION_TEXT,
        text_digest=assertion_text_digest(SECRET),
        matched=1,
        frames_scanned=1,
        frames_skipped=0,
        truncated=False,
        snapshot="checked",
    )


def test_a_recorded_result_replays_and_lints() -> None:
    """The result fields are observations: replay drops them and lint accepts them."""
    row = {"text": "{{pw}}", "selector": "body", "matched": 1, "frames_scanned": 1}
    row |= {"frames_skipped": 0, "truncated": False, "snapshot": "checked"}
    assert _normalize_replay_kwargs("expect_no_text", dict(row)) == {"text": "{{pw}}", "selector": "body"}
    issues = lint_macro({"name": "m", "actions": [{"action": "expect_no_text", **row}]})
    assert [i for i in issues if i.severity == "error"] == []


@pytest.mark.anyio
async def test_everything_a_check_records_besides_its_inputs_is_dropped_on_replay(tmp_path: Path) -> None:
    """The recorded row, not a hand-kept list: a new summary field must not make replay raise TypeError."""
    session = _session(tmp_path)
    await session.expect_no_text(SECRET, selector="body", element_limit=50)
    recorded = dict(session.recorder.record.call_args.kwargs)
    inputs = {"text", "selector", "element_limit"}
    assert set(recorded) - inputs == set(NO_TEXT_OBSERVATION_KEYS)
    assert set(_normalize_replay_kwargs("expect_no_text", recorded)) == inputs


@pytest.mark.anyio
async def test_a_truncated_scan_that_found_nothing_is_refused(tmp_path: Path) -> None:
    """A security assertion must not pass on a page it only partly read."""
    session = _session(tmp_path, frames=[_frame("Welcome", truncated=True)])
    with pytest.raises(RuntimeError) as excinfo:
        await session.expect_no_text(SECRET)
    message = str(excinfo.value)
    assert str(ELEMENT_LIMIT) in message
    assert "selector" in message
    assert SECRET not in message


@pytest.mark.anyio
async def test_a_truncated_scan_that_found_the_text_fails_on_the_text(tmp_path: Path) -> None:
    session = _session(tmp_path, frames=[_frame(SECRET, truncated=True)])
    with pytest.raises(RuntimeError, match="forbidden text"):
        await session.expect_no_text(SECRET)


@pytest.mark.anyio
async def test_a_main_frame_without_a_result_is_an_error(tmp_path: Path) -> None:
    frame = _frame()
    frame.evaluate = AsyncMock(return_value=None)
    session = _session(tmp_path, frames=[frame])
    with pytest.raises(RuntimeError, match="no result"):
        await session.expect_no_text(SECRET)


# --- G: every frame, and frames that go away ------------------------------------------


@pytest.mark.anyio
async def test_every_frame_of_a_real_frame_list_is_scanned(tmp_path: Path) -> None:
    frames = [_frame("main"), _frame("ad"), _frame(f"child says {SECRET}")]
    session = _session(tmp_path, frames=frames)
    with pytest.raises(RuntimeError, match="forbidden text"):
        await session.expect_no_text(SECRET)
    for frame in frames:
        frame.evaluate.assert_awaited_once()


@pytest.mark.parametrize(
    ("message", "detached"),
    [
        # Detaching is asked of the frame, whatever the message says (Firefox
        # reports a removed iframe as a destroyed context, measured).
        ("Frame was detached", True),
        ("SyntaxError: anything at all", True),
        # Navigating is recognised by the message every engine gives it.
        ("Frame.evaluate: Execution context was destroyed, most likely because of a navigation", False),
    ],
)
@pytest.mark.anyio
async def test_a_child_frame_that_goes_away_is_skipped(tmp_path: Path, message: str, detached: bool) -> None:
    child = _frame(detached=detached)
    child.evaluate = AsyncMock(side_effect=PlaywrightError(message))
    session = _session(tmp_path, frames=[_frame("main"), child, _frame("other")])
    result = await session.expect_no_text(SECRET)
    assert (result["frames_scanned"], result["frames_skipped"]) == (2, 1)


@pytest.mark.anyio
async def test_the_main_frame_going_away_still_fails(tmp_path: Path) -> None:
    main = _frame()
    main.evaluate = AsyncMock(side_effect=PlaywrightError("Execution context was destroyed"))
    session = _session(tmp_path, frames=[main, _frame()])
    with pytest.raises(PlaywrightError):
        await session.expect_no_text(SECRET)


@pytest.mark.anyio
async def test_other_child_frame_errors_still_fail(tmp_path: Path) -> None:
    child = _frame()
    child.evaluate = AsyncMock(side_effect=PlaywrightError("SyntaxError: unexpected token"))
    session = _session(tmp_path, frames=[_frame(), child])
    with pytest.raises(PlaywrightError):
        await session.expect_no_text(SECRET)


# --- H: which scan caught it ------------------------------------------------------------


@pytest.mark.anyio
async def test_the_script_scan_names_itself(tmp_path: Path) -> None:
    session = _session(tmp_path, frames=[_frame(SECRET)])
    with pytest.raises(RuntimeError, match=r"forbidden text .*\(script scan\)"):
        await session.expect_no_text(SECRET)


@pytest.mark.anyio
async def test_the_snapshot_names_itself(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session._snapshot_leaks = AsyncMock(return_value=True)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match=r"forbidden text .*\(DOM snapshot\)") as excinfo:
        await session.expect_no_text(SECRET)
    assert SECRET not in str(excinfo.value)


# --- D/E: the snapshot counts drawn text only ---------------------------------------


class _Snap:
    """A one-document ``DOMSnapshot.captureSnapshot`` result built node by node."""

    def __init__(self) -> None:
        self.strings: list[str] = []
        self.nodes: dict[str, list[Any]] = {"parentIndex": [], "nodeName": [], "attributes": []}
        self.layout: dict[str, list[Any]] = {"nodeIndex": [], "text": [], "styles": []}

    def _s(self, value: str) -> int:
        self.strings.append(value)
        return len(self.strings) - 1

    def element(self, name: str, parent: int = -1, **attributes: str) -> int:
        self.nodes["parentIndex"].append(parent)
        self.nodes["nodeName"].append(self._s(name))
        self.nodes["attributes"].append([self._s(x) for pair in attributes.items() for x in pair])
        return len(self.nodes["nodeName"]) - 1

    def text(self, value: str, parent: int, visibility: str = "visible") -> int:
        node = self.element("#text", parent)
        self.layout["nodeIndex"].append(node)
        self.layout["text"].append(self._s(value))
        self.layout["styles"].append([self._s(visibility), self._s("none")])
        return node

    def result(self) -> dict[str, Any]:
        return {"strings": self.strings, "documents": [{"nodes": self.nodes, "layout": self.layout}]}


def test_the_snapshot_reads_drawn_text() -> None:
    snap = _Snap()
    body = snap.element("BODY")
    snap.text("Hello ", body)
    snap.text(SECRET, snap.element("B", body))
    assert any(SECRET in piece for piece in snapshot_drawn_text(snap.result()))


def test_hidden_text_is_not_drawn_text() -> None:
    snap = _Snap()
    snap.text(SECRET, snap.element("P", snap.element("BODY")), visibility="hidden")
    assert all(SECRET not in piece for piece in snapshot_drawn_text(snap.result()))


def test_octowrights_overlay_is_left_out_of_the_snapshot() -> None:
    """E: excluding the overlay, not skipping the scan, is what lets a page leak still fail."""
    snap = _Snap()
    body = snap.element("BODY")
    badge = snap.element("DIV", body, id="__octowright_badge__")
    snap.text("octowright-label", snap.element("SPAN", badge))
    snap.text("page shows octowright-label too", snap.element("P", body))
    pieces = snapshot_drawn_text(snap.result())
    assert pieces == ["page shows octowright-label too"]


def test_a_digit_spelling_is_not_the_text() -> None:
    """The screenshot scanner's digit-form match errs toward refusal; drawn text compares as written."""
    assert not contains(["call 555-013-7788"], "5550137788")
    assert contains(["call 555 013 7788"], "5550137788")


def test_attributes_are_not_drawn_text() -> None:
    """A token in a resource address or data attribute is not text a reader sees."""
    snap = _Snap()
    body = snap.element("BODY")
    snap.element("IMG", body, src=f"/img?t={SECRET}", alt="logo")
    snap.text("Welcome", snap.element("P", body, **{"data-token": SECRET}))
    assert all(SECRET not in piece for piece in snapshot_drawn_text(snap.result()))
