# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Redacted screenshots for macro runs that hold classified values.

A screenshot of a page a credential or identity was typed into is a durable copy of
it that no text scrub can reach, so a classified run refuses screenshots by default.
This module is the safe path for a Chromium page. It pauses CSS animations, redacts the
page, proves nothing classified is rendered, takes the screenshot, proves again, and
restores the page, all through one DevTools session:

1. The in-page controller (:mod:`octowright.macros.redaction_page_js`) replaces every
   spelling of the run's values in the document and in open and closed shadow roots,
   masks text controls holding one, and hides elements whose pixels or resource
   addresses cannot be redacted.
2. From then on the page's changes are counted, by Chrome for DOM and stylesheet changes
   (:mod:`octowright.macros.page_devtools`) and by the controller for style, form state
   and focus changes. Before and after the capture the count must be zero, the
   controller must find no remaining value, and Chrome's rendered surface
   (:mod:`octowright.macros.rendered_surface`) must hold no value. Content redaction
   cannot reach, such as generated content from a stylesheet, is refused there rather
   than redacted.
3. The screenshot is captured through the same DevTools session
   (``Page.captureScreenshot``). Playwright's screenshot helper is not used, because it
   writes styles onto the page before capturing and a page can react to them.
4. The page is restored whatever happened. A refused or unrestorable screenshot leaves
   no file.

Opt in per session with :func:`enable_redacted_screenshots`, or for every session with
``OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS=redact``. A caller-installed handler still
wins and may wrap :func:`redacted_screenshot` with its own checks.

Limits. A page is assumed to be the application under test, not an adversary: page
script keeps running, and a script that kept its own references to the form-state
setters the controller counts could change that state without being counted. The text
matching (see ``rendered_surface``) covers invisible characters, re-casing,
normalization, whole reversal, digit punctuation, reordering by position and a few
interleaved text boxes, not every way a page could draw a value; a value only partly
reversed by a bidi override, or displayed in another format than its digits, is not
matched. A masked control still shows how long its value is. Pixels of an ordinary
image are not read; an image is hidden only when one of its resource addresses holds a
value. The page's own mutation observers see the redaction while it lasts. Engines other
than Chromium have no rendered-surface snapshot, so the screenshot is refused there.
"""

from __future__ import annotations

import base64
import contextlib
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from provide.telemetry import get_logger

from octowright import defaults
from octowright._paths import atomic_write_via_writer, reject_unsafe_path
from octowright.macros.page_devtools import (
    PageChanges,
    PageController,
    end_view_transitions,
    view_transition_pseudo_elements,
)
from octowright.macros.privacy import sensitive_value_variants
from octowright.macros.rendered_surface import SNAPSHOT_PARAMS, rendered_leaks
from octowright.session.timeouts import bounded

log = get_logger(__name__)

POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"
AUTHORITY_ATTR = "_octowright_sensitive_screenshot_authority"
HANDLER_ATTR = "_octowright_sensitive_screenshot_handler"
BUILT_IN_ATTR = "_octowright_redacted_screenshot_built_in"

ClassifiedScreenshotPolicy = Literal["refuse", "redact"]
ScreenshotHandler = Callable[..., Awaitable[tuple[int, int] | None]]

_OPERATION = "macro_redacted_screenshot"
_REFUSED = "screenshot refused"


def classified_screenshot_policy() -> ClassifiedScreenshotPolicy:
    """What a classified run does with a screenshot when no handler is installed.

    ``refuse`` (the default) or ``redact``. Any other value raises, because a typo
    here would otherwise silently decide whether credentials reach a PNG.
    """
    raw = os.environ.get(POLICY_ENV, "refuse").strip().lower()
    if raw == "refuse":
        return "refuse"
    if raw == "redact":
        return "redact"
    raise ValueError(f"{POLICY_ENV} must be 'refuse' or 'redact'")


def enable_redacted_screenshots(session: Any, *, handler: ScreenshotHandler | None = None) -> None:
    """Authorize screenshots on a classified run for this session.

    Without ``handler`` the session uses :func:`redacted_screenshot`. A caller that
    needs its own checks passes a handler, which may call :func:`redacted_screenshot`
    itself; octowright then never takes a screenshot on that session by any other
    route, including the automatic artifact screenshots.
    """
    if handler is None:

        async def built_in(*, action: dict[str, Any], sensitive_values: tuple[str, ...]) -> tuple[int, int]:
            return await redacted_screenshot(session, action, sensitive_values)

        setattr(session, HANDLER_ATTR, built_in)
        setattr(session, BUILT_IN_ATTR, True)
    else:
        setattr(session, HANDLER_ATTR, handler)
        setattr(session, BUILT_IN_ATTR, False)
    setattr(session, AUTHORITY_ATTR, True)


def installed_handler(session: Any) -> ScreenshotHandler | None:
    """The explicitly authorized handler, or None when the session has none."""
    handler = getattr(session, HANDLER_ATTR, None)
    if getattr(session, AUTHORITY_ATTR, None) is True and callable(handler):
        return handler
    return None


def built_in_redaction_applies(session: Any) -> bool:
    """Whether octowright itself may take a redacted screenshot on this session.

    False whenever a caller installed its own handler: an automatic screenshot must
    never bypass that handler's checks.
    """
    if installed_handler(session) is not None:
        return getattr(session, BUILT_IN_ATTR, None) is True
    return classified_screenshot_policy() == "redact"


async def _require_unrendered(
    controller: PageController, changes: PageChanges, cdp: Any, values: list[str], *, stage: str
) -> None:
    """Raise unless the page is unchanged since redaction and renders no classified value."""
    report = await bounded(controller.verify(), operation=_OPERATION)
    if int(report.get("changed", 1)) or changes.count:
        raise RuntimeError(f"the page changed {stage} the redacted screenshot; {_REFUSED}")
    if int(report.get("remaining", 1)):
        raise RuntimeError(f"classified values are still in the page {stage} the screenshot; {_REFUSED}")
    if int(report.get("transitioning", 1)):
        raise RuntimeError(f"a view transition is running {stage} the screenshot; {_REFUSED}")
    # What Chrome draws, whatever root it is in: a root attached after the redaction collected its roots is not in them.
    document_tree = await bounded(cdp.send("DOM.getDocument", {"depth": -1, "pierce": True}), operation=_OPERATION)
    if view_transition_pseudo_elements(document_tree.get("root", {})):
        raise RuntimeError(f"a view transition is drawn {stage} the screenshot; {_REFUSED}")
    snapshot = await bounded(cdp.send("DOMSnapshot.captureSnapshot", SNAPSHOT_PARAMS), operation=_OPERATION)
    leaks = rendered_leaks(snapshot, values)
    if leaks:
        raise RuntimeError(
            f"classified values are still rendered {stage} the screenshot ({', '.join(leaks)}); {_REFUSED}"
        )


async def _capture(cdp: Any, target: Path) -> None:
    """Write the screenshot from ``Page.captureScreenshot``, atomically."""
    image = "jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "png"
    shot = await bounded(cdp.send("Page.captureScreenshot", {"format": image}), operation=_OPERATION)
    data = base64.b64decode(shot["data"])
    target.parent.mkdir(parents=True, exist_ok=True)

    async def write(temporary: Path) -> None:
        temporary.write_bytes(data)

    await atomic_write_via_writer(target, write)


async def _pause_animations(cdp: Any) -> None:
    """Stop the page's CSS animations and transitions, so the scans and the capture see one frame."""
    await bounded(cdp.send("Animation.enable"), operation=_OPERATION)
    await bounded(cdp.send("Animation.setPlaybackRate", {"playbackRate": 0}), operation=_OPERATION)


async def _end_view_transitions(cdp: Any) -> None:
    """End every running view transition, which draws a raster of its scope taken before the redaction."""
    try:
        await bounded(end_view_transitions(cdp), operation=_OPERATION)
    except Exception as exc:
        raise RuntimeError(f"a view transition could not be ended; {_REFUSED}") from exc


async def _release(cdp: Any, changes: PageChanges) -> None:
    """Stop counting, resume animations and detach; each step is attempted even if an earlier one failed."""
    with contextlib.suppress(Exception):
        await bounded(changes.close(), operation=_OPERATION)
    for method, params in (("Animation.setPlaybackRate", {"playbackRate": 1}), ("Animation.disable", None)):
        with contextlib.suppress(Exception):
            await bounded(cdp.send(method, params) if params else cdp.send(method), operation=_OPERATION)
    with contextlib.suppress(Exception):
        await cdp.detach()


async def _restore(controller: PageController, target: Path, *, quiet: bool) -> None:
    """Undo the redaction. On failure the screenshot is deleted; ``quiet`` logs instead of raising."""
    try:
        await bounded(controller.restore(), operation=_OPERATION)
    except Exception as exc:
        target.unlink(missing_ok=True)
        log.warning("octowright.macro.redacted_screenshot.restore_failed", error=type(exc).__name__)
        if not quiet:
            raise
    finally:
        with contextlib.suppress(Exception):
            await bounded(controller.dispose(), operation=_OPERATION)


async def _redact_and_capture(cdp: Any, changes: PageChanges, values: list[str], target: Path) -> PageController:
    """Pause, redact, count, prove, capture and prove again; on any failure restore and re-raise."""
    controller: PageController | None = None
    try:
        await _pause_animations(cdp)
        await _end_view_transitions(cdp)
        closed_roots = await bounded(changes.start(), operation=_OPERATION)
        controller = await bounded(PageController.create(cdp, values), operation=_OPERATION)
        await bounded(controller.redact(closed_roots), operation=_OPERATION)
        latent = await bounded(changes.sheets_hold(values), operation=_OPERATION)
        await bounded(controller.watch(latent), operation=_OPERATION)
        changes.begin()
        await _require_unrendered(controller, changes, cdp, values, stage="before")
        await _capture(cdp, target)
        await _require_unrendered(controller, changes, cdp, values, stage="after")
    except BaseException:
        target.unlink(missing_ok=True)
        if controller is not None:
            await _restore(controller, target, quiet=True)
        raise
    return controller


async def redacted_screenshot(
    session: Any,
    action: dict[str, Any],
    sensitive_values: tuple[str, ...],
    *,
    root: Path | None = None,
) -> tuple[int, int]:
    """Pause animations, end a view transition, redact, prove nothing is rendered, screenshot, prove again, restore.

    Returns ``(executed, skipped)``. No file survives unless both proofs passed and the
    page was restored. Refusals raise ``RuntimeError`` naming what was found, never the
    value.
    """
    path_value = action.get("path")
    if not path_value:
        return 0, 1
    target = reject_unsafe_path(
        Path(str(path_value)),
        root if root is not None else defaults.RECORDINGS_DIR,
        label="screenshot path",
    )
    values = list(sensitive_value_variants(sensitive_values))
    # Re-enters the caller's lease (the macro run, or an artifact run) in the same
    # task, so no other operation on this page interleaves with redact .. restore.
    async with session.operation("macro_run"):
        page = session.page
        try:
            cdp = await bounded(page.context.new_cdp_session(page), operation=_OPERATION)
        except Exception as exc:
            raise RuntimeError(
                f"a redacted screenshot needs a Chromium page to read what is rendered; {_REFUSED}"
            ) from exc
        changes = PageChanges(cdp)
        try:
            controller = await _redact_and_capture(cdp, changes, values, target)
            await _restore(controller, target, quiet=False)
        finally:
            await _release(cdp, changes)
        recorder = getattr(session, "recorder", None)
        if recorder is not None:
            recorder.record("screenshot", path=str(target))
    return 1, 0
