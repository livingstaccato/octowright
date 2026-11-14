# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redacted screenshot names what changed, lets rendering settle first, and tries once more.

A classified macro run on Chromium was refused at a different screenshot each time with
"the page changed after the redacted screenshot" and nothing else: no app request was
outstanding, and only a count of the changes was kept. Now:

- the refusal and its ``octowright.macro.screenshot_refused`` warning say which kinds of
  change were seen (a DevTools event name, or the controller's own kind such as
  ``focusout``), never a value or a selector;
- rendering is allowed to settle (two animation frames, bounded) after the redaction and
  before anything is counted, so pending style or focus work is not blamed on the capture;
- a page that changed during the capture loses that image, is restored, redacted again and
  captured once more; only a second change refuses. The first image is never written.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
from pathlib import Path
from typing import Any

import pytest
import structlog

from octowright import defaults
from octowright.macros import safe_screenshot
from octowright.macros.page_devtools import PageChanges
from octowright.macros.screenshot_refusal import ScreenshotRefused
from tests._redacted_screenshot_fakes import CLEAN, PNG, TAKEN, FakePage, _shoot

#: One attempt's protocol after the animations are paused: up to and including the restore.
_ATTEMPT = ["view_transitions", "redact", "watch", "verify", "snapshot", "screenshot", "verify", "snapshot", "restore"]
_CHANGED_AFTER = ["view_transitions", "redact", "watch", "verify", "snapshot", "screenshot", "verify", "restore"]


@pytest.fixture(autouse=True)
def _recordings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    return tmp_path


def _warnings(logs: Iterable[MutableMapping[str, Any]]) -> list[MutableMapping[str, Any]]:
    return [entry for entry in logs if entry["log_level"] == "warning"]


def _png_writes(tmp_path: Path) -> list[Path]:
    return [path for path in tmp_path.rglob("*") if path.is_file()]


# --- which kinds changed ------------------------------------------------------------------------


def test_page_changes_count_each_devtools_event_by_its_name() -> None:
    class CDP:
        def __init__(self) -> None:
            self.handlers: dict[str, Any] = {}

        def on(self, method: str, handler: Any) -> None:
            self.handlers[method] = handler

    cdp = CDP()
    changes = PageChanges(cdp)
    for method in ("DOM.childNodeInserted", "DOM.pseudoElementAdded", "CSS.styleSheetChanged", "DOM.attributeModified"):
        changes._handlers.append((method, changes._handler(method)))
        cdp.on(method, changes._handlers[-1][1])

    cdp.handlers["DOM.childNodeInserted"]({})  # before counting: neither counted nor named
    changes.begin()
    cdp.handlers["DOM.childNodeInserted"]({"parentNodeId": 1})
    cdp.handlers["DOM.childNodeInserted"]({"parentNodeId": 2})
    cdp.handlers["DOM.pseudoElementAdded"]({})
    cdp.handlers["CSS.styleSheetChanged"]({"styleSheetId": "3"})
    cdp.handlers["DOM.attributeModified"]({"name": "style", "value": "x"})  # inline style: the controller's call

    assert changes.count == 4
    assert changes.kinds == {"DOM.childNodeInserted": 2, "DOM.pseudoElementAdded": 1, "CSS.styleSheetChanged": 1}


async def test_a_refusal_names_the_devtools_change_and_never_a_value(tmp_path: Path) -> None:
    page = FakePage(events={"verify": (("DOM.childNodeInserted", {"parentNodeId": 7, "value": "secret"}),)})

    with structlog.testing.capture_logs() as logs, pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == (
        "the page changed before the redacted screenshot (DOM.childNodeInserted x1); screenshot refused"
    )
    assert raised.value.fields == {
        "reasons": ["page changed"],
        "stage": "before",
        "changes": {"DOM.childNodeInserted": 1},
        "tiers": [],
        "args": [],
    }
    assert [entry["changes"] for entry in _warnings(logs)] == [{"DOM.childNodeInserted": 1}]


async def test_a_refusal_names_the_controllers_own_change_kinds(tmp_path: Path) -> None:
    changed = {**CLEAN, "changed": 3, "kinds": {"focusout": 2, "inline style": 1}}

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(FakePage(reports=(changed,)), tmp_path / "shot.png")

    assert raised.value.fields["changes"] == {"focusout": 2, "inline style": 1}
    assert "(focusout x2, inline style x1)" in str(raised.value)


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        ({**CLEAN, "changed": 2}, {"unreported": 2}),  # a count without kinds
        ({"remaining": 0, "transitioning": 0}, {"unreported": 1}),  # no count at all reads as changed
        ({**CLEAN, "changed": 1, "kinds": {"focusin": 1, "<b>secret</b>": 4}}, {"focusin": 1, "unreported": 4}),
        ({**CLEAN, "changed": 1, "kinds": ["focusin"]}, {"unreported": 1}),
    ],
    ids=["count-only", "missing", "unknown-kind", "malformed-kinds"],
)
async def test_change_kinds_the_page_cannot_name_are_reported_as_unreported(
    report: dict[str, Any], expected: dict[str, int], tmp_path: Path
) -> None:
    with structlog.testing.capture_logs() as logs, pytest.raises(ScreenshotRefused) as raised:
        await _shoot(FakePage(reports=(report,)), tmp_path / "shot.png")

    assert raised.value.fields["changes"] == expected
    assert "secret" not in str(raised.value)
    assert _warnings(logs) and "secret" not in repr(_warnings(logs))


# --- settle before counting ---------------------------------------------------------------------


async def test_rendering_settles_after_the_redaction_and_before_counting_begins(tmp_path: Path) -> None:
    # Work the redaction left pending (a focus fix-up, a style flush) lands while the page settles: not counted.
    pending = (("DOM.childNodeInserted", {"parentNodeId": 1}), ("DOM.pseudoElementRemoved", {}))
    page = FakePage(events={"settle": pending})

    assert await _shoot(page, tmp_path / "shot.png") == (1, 0)

    assert page.settles == [["animations:0", "view_transitions", "redact"]]
    assert page.calls == TAKEN


async def test_a_page_that_never_settles_is_still_proved_and_captured(tmp_path: Path) -> None:
    page = FakePage(settle_reply={"result": {"value": False}})

    with structlog.testing.capture_logs() as logs:
        assert await _shoot(page, tmp_path / "shot.png") == (1, 0)

    assert (tmp_path / "shot.png").read_bytes() == PNG
    assert [entry["event"] for entry in logs if entry["log_level"] == "info"].count(
        "octowright.macro.redacted_screenshot.unsettled"
    ) == 1


async def test_a_settle_that_fails_does_not_decide_the_screenshot(tmp_path: Path) -> None:
    page = FakePage(settle_error=RuntimeError("Execution context was destroyed"))

    assert await _shoot(page, tmp_path / "shot.png") == (1, 0)
    assert (tmp_path / "shot.png").read_bytes() == PNG


def test_the_settle_is_two_frames_bounded_by_its_timeout() -> None:
    assert safe_screenshot._SETTLE_FRAMES == 2
    assert 0 < safe_screenshot._SETTLE_TIMEOUT_SECONDS <= 1
    assert str(int(safe_screenshot._SETTLE_TIMEOUT_SECONDS * 1000)) in safe_screenshot._settle_expression()
    assert safe_screenshot._settle_expression().count("requestAnimationFrame") == safe_screenshot._SETTLE_FRAMES


# --- one more attempt ---------------------------------------------------------------------------


async def test_a_change_during_the_first_capture_is_tried_once_more(tmp_path: Path) -> None:
    changed = {**CLEAN, "changed": 1, "kinds": {"focusout": 1}}
    page = FakePage(reports=(CLEAN, changed, CLEAN, CLEAN))

    with structlog.testing.capture_logs() as logs:
        assert await _shoot(page, tmp_path / "shot.png") == (1, 0)

    # The first attempt is restored before the page is redacted again; one settle per attempt.
    assert page.calls == ["animations:0", *_CHANGED_AFTER, *_ATTEMPT, "animations:1", "detach"]
    assert len(page.settles) == 2
    assert (tmp_path / "shot.png").read_bytes() == PNG
    assert _warnings(logs) == [
        {
            "event": "octowright.macro.redacted_screenshot.changed_during_capture",
            "attempt": 1,
            "changes": {"focusout": 1},
            "log_level": "warning",
        }
    ]


async def test_a_change_during_both_captures_refuses_with_the_second_attempts_kinds(tmp_path: Path) -> None:
    first = {**CLEAN, "changed": 1, "kinds": {"focusout": 1}}
    second = {**CLEAN, "changed": 1, "kinds": {"hashchange": 1}}
    page = FakePage(reports=(CLEAN, first, CLEAN, second))

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert str(raised.value) == ("the page changed after the redacted screenshot (hashchange x1); screenshot refused")
    assert raised.value.fields["stage"] == "after"
    assert page.calls == ["animations:0", *_CHANGED_AFTER, *_CHANGED_AFTER, "animations:1", "detach"]
    assert _png_writes(tmp_path) == []


async def test_the_discarded_image_is_never_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every write the screenshot makes, wherever it would land.
    writes: list[tuple[Path, bytes]] = []
    real = safe_screenshot._write

    async def watched(target: Path, data: bytes) -> None:
        writes.append((target, data))
        await real(target, data)

    monkeypatch.setattr(safe_screenshot, "_write", watched)
    first = {**CLEAN, "changed": 1}
    page = FakePage(reports=(CLEAN, first, CLEAN, CLEAN), capture_images=(b"first-image", PNG))

    assert await _shoot(page, tmp_path / "shot.png") == (1, 0)

    assert writes == [((tmp_path / "shot.png").resolve(), PNG)]
    assert _png_writes(tmp_path) == [tmp_path / "shot.png"]


def _holders(exc: BaseException, marker: bytes) -> list[str]:
    """Every frame local, along the exception chain and its tracebacks, that holds bytes containing *marker*."""
    found: list[str] = []
    seen: set[int] = set()
    pending: list[BaseException | None] = [exc]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        assert marker not in repr(current.args).encode()
        pending.extend((current.__cause__, current.__context__))
        tb = current.__traceback__
        while tb is not None:
            for name, value in tb.tb_frame.f_locals.items():
                # The marker itself is this test's own local; only a captured image extends it.
                if isinstance(value, (bytes, bytearray)) and marker in value and value != marker:
                    found.append(f"{tb.tb_frame.f_code.co_name}.{name}")
            tb = tb.tb_next
    return found


@pytest.mark.parametrize(
    "reports",
    [
        (CLEAN, {**CLEAN, "changed": 1}, CLEAN, {**CLEAN, "changed": 1}),  # changed during both captures
        (CLEAN, {**CLEAN, "remaining": 1}),  # a value left after the capture
    ],
    ids=["changed-twice", "value-left-after"],
)
async def test_a_refusal_after_the_capture_keeps_no_reference_to_the_image(
    reports: tuple[dict[str, Any], ...], tmp_path: Path
) -> None:
    marker = b"unproved-image-canary"
    page = FakePage(reports=reports, capture_images=(marker + b"-1", marker + b"-2"))

    with pytest.raises(ScreenshotRefused) as raised:
        await _shoot(page, tmp_path / "shot.png")

    assert _holders(raised.value, marker) == []


async def test_a_change_before_the_capture_is_not_tried_again(tmp_path: Path) -> None:
    page = FakePage(reports=({**CLEAN, "changed": 1}, CLEAN, CLEAN))

    with pytest.raises(ScreenshotRefused, match="changed before"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls.count("redact") == 1


async def test_a_value_rendered_after_the_capture_is_not_tried_again(tmp_path: Path) -> None:
    page = FakePage(reports=(CLEAN, {**CLEAN, "remaining": 1}, CLEAN, CLEAN))

    with pytest.raises(ScreenshotRefused, match="still in the page after"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls.count("redact") == 1


async def test_a_first_attempt_that_cannot_be_restored_is_not_tried_again(tmp_path: Path) -> None:
    # A page the restore may have left redacted is not redacted over: the change refuses.
    page = FakePage(reports=(CLEAN, {**CLEAN, "changed": 1}, CLEAN, CLEAN), restore_error=RuntimeError("navigated"))

    with pytest.raises(ScreenshotRefused, match="changed after"):
        await _shoot(page, tmp_path / "shot.png")

    assert page.calls.count("redact") == 1
    assert _png_writes(tmp_path) == []


def test_one_more_attempt_and_no_more() -> None:
    assert safe_screenshot._CHANGE_ATTEMPTS == 2


# --- on a real page -----------------------------------------------------------------------------


@pytest.mark.live_browser
@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ("focus", "focusin"),
        ("checked", "checked"),
        ("custom validity", "custom validity"),
        ("location hash", "hashchange"),
        ("child added", "DOM.childNodeInserted"),
    ],
)
async def test_a_real_page_names_the_kind_of_change(change: str, kind: str) -> None:
    from tests.test_macro_redaction_controller_live import _CHANGES, _PAGE, _SETTLED, _page, _watched

    async with _page(_PAGE) as page, _watched(page) as watched:
        await page.evaluate(_CHANGES[change])
        await page.evaluate(_SETTLED)
        report = await watched.controller.verify()

    kinds = {**report.get("kinds", {}), **watched.changes.kinds}
    assert kinds.get(kind, 0) >= 1, kinds
