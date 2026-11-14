# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Redacted screenshots for macro runs that hold policy-admitted values.

A screenshot of a page a protected value was typed into is a durable copy of it that no
text scrub can reach, so a run holding values admitted by the blind-scrub policy refuses
screenshots by default. This module is the safe path for a Chromium page. It pauses CSS
animations (:mod:`octowright.macros.animation_freeze`), redacts the page, proves no admitted value is rendered, takes the screenshot,
proves again, and restores the page, all through one DevTools session:

1. The in-page controller (:mod:`octowright.macros.redaction_page_js`) replaces every
   spelling of the run's values in the document and in open and closed shadow roots,
   masks text controls holding one, and hides elements whose pixels or resource
   addresses cannot be redacted.
2. Rendering is let settle (two animation frames, bounded), so work the redaction left
   pending lands before counting. From then on the page's changes are counted, by Chrome for DOM and stylesheet changes
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
   no file: the image is written only after the second proof passed and the page was
   restored.

A page that changed during the capture loses that image, is restored, redacted again and
captured once more; a second change refuses. Every "page changed" refusal names the kinds
of change counted (a DevTools event name or a kind the controller counts), never a value.

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

import asyncio
import base64
import contextlib
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from provide.telemetry import get_logger

from octowright import defaults
from octowright._paths import atomic_write_via_writer, reject_unsafe_path
from octowright.macros import redaction_page_js as page_js
from octowright.macros.animation_freeze import AnimationFreeze
from octowright.macros.page_devtools import (
    PageChanges,
    PageController,
    end_view_transitions,
    view_transition_pseudo_elements,
)
from octowright.macros.privacy import sensitive_value_variants
from octowright.macros.rendered_surface import SNAPSHOT_PARAMS, rendered_leaks
from octowright.macros.screenshot_refusal import Refusals
from octowright.session.timeouts import bounded

log = get_logger(__name__)

POLICY_ENV = "OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS"
AUTHORITY_ATTR = "_octowright_sensitive_screenshot_authority"
HANDLER_ATTR = "_octowright_sensitive_screenshot_handler"
BUILT_IN_ATTR = "_octowright_redacted_screenshot_built_in"

ClassifiedScreenshotPolicy = Literal["refuse", "redact"]
ScreenshotHandler = Callable[..., Awaitable[tuple[int, int] | None]]

_OPERATION = "macro_redacted_screenshot"

# Chrome answers Page.captureScreenshot this way when it has no frame to copy
# yet (seen on a loaded Linux CI runner, between two proofs that passed). The
# capture is tried up to _CAPTURE_ATTEMPTS times in all: change counting stays
# on across the retries and the proof after the capture still covers every
# change since counting began. A page that is hidden or has no size can never
# produce a frame, so it is not retried.
_NO_FRAME = "Unable to capture screenshot"
_CAPTURE_ATTEMPTS = 3
_CAPTURE_RETRY_SECONDS = 0.1
_CAN_PRODUCE_FRAME = "document.visibilityState === 'visible' && innerWidth > 0 && innerHeight > 0"

# Rendering is let settle after the redaction and before anything is counted, so work the page or the
# redaction left pending (a style flush, the focus fix-up when the focused element was hidden) lands then
# rather than inside the capture, where it would be counted as a change. Two animation frames, bounded in
# the page and again here. Settling decides nothing: a page that does not settle is still proved and captured.
_SETTLE_FRAMES = 2
_SETTLE_TIMEOUT_SECONDS = 0.25
# A page that changed during the capture loses that image and is redacted and captured once more; a second
# change refuses. A change found before the capture refuses at once.
_CHANGE_ATTEMPTS = 2
_UNREPORTED = "unreported"


class _PageChanged(Exception):
    """The page changed since counting began: which kinds, and whether the redaction was undone after it."""

    def __init__(self, stage: str, kinds: dict[str, int]) -> None:
        super().__init__(stage)
        self.stage = stage
        self.kinds = kinds
        self.restored = False


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


LEDGER_POLICY_ENV = "OCTOWRIGHT_LEDGER_SCREENSHOTS"


def ledger_screenshot_policy() -> Literal["refuse", "allow"]:
    """Whether a session holding privacy-ledger values screenshots through the boundary.

    ``refuse`` (the default) sends it through :func:`ledger_screenshot`, which
    redacts on Chromium and refuses on engines it cannot prove that on.
    ``allow`` takes the raw screenshot: the operator's way back to screenshots
    after a login on Firefox/WebKit that keeps recording redaction on, which
    ``OCTOWRIGHT_REDACT_INPUTS=off`` does not. Anything but ``allow`` means
    ``refuse`` -- a typo must not decide that a password may reach a PNG.
    """
    raw = os.environ.get(LEDGER_POLICY_ENV, "refuse").strip().lower()
    if raw == "allow":
        return "allow"
    if raw not in ("refuse", ""):
        log.warning("octowright.screenshot.ledger_policy_unknown", env=LEDGER_POLICY_ENV, value=raw)
    return "refuse"


def guards_ledger_screenshot(session: Any) -> bool:
    """Whether a ledger-holding session's screenshot must take :func:`ledger_screenshot`.

    An installed handler always does: it is the embedding application's own
    privacy decision, which an operator's environment does not override.
    """
    return installed_handler(session) is not None or ledger_screenshot_policy() == "refuse"


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


def _change_kinds(report: Mapping[str, Any], changes: PageChanges) -> dict[str, int]:
    """How many changes of each kind were counted, by name only; any the page cannot name is ``unreported``."""
    kinds = dict(changes.kinds)
    named = unreported = 0
    reported = report.get("kinds")
    for kind, raw in (reported if isinstance(reported, Mapping) else {}).items():
        count = raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 1
        if kind in page_js.CHANGE_KINDS:
            kinds[kind] = kinds.get(kind, 0) + count
            named += count
        else:
            unreported += count
    try:
        total = int(report.get("changed", 1))
    except (TypeError, ValueError):
        total = 1
    unreported += max(total - named - unreported, 0)
    if unreported:
        kinds[_UNREPORTED] = unreported
    return kinds


async def _require_unrendered(
    controller: PageController, changes: PageChanges, cdp: Any, values: list[str], refusals: Refusals, *, stage: str
) -> None:
    """Raise unless the page is unchanged since redaction and renders no classified value."""
    report = await bounded(controller.verify(), operation=_OPERATION)
    if int(report.get("changed", 1)) or changes.count:
        raise _PageChanged(stage, _change_kinds(report, changes))
    if int(report.get("remaining", 1)):
        head = f"classified values are still in the page {stage} the screenshot"
        raise refusals.refuse(head, "value left in the page", stage=stage)
    if int(report.get("transitioning", 1)):
        raise refusals.refuse(f"a view transition is running {stage} the screenshot", "view transition", stage=stage)
    # What Chrome draws, whatever root it is in: a root attached after the redaction collected its roots is not in them.
    document_tree = await bounded(cdp.send("DOM.getDocument", {"depth": -1, "pierce": True}), operation=_OPERATION)
    if view_transition_pseudo_elements(document_tree.get("root", {})):
        raise refusals.refuse(f"a view transition is drawn {stage} the screenshot", "view transition", stage=stage)
    snapshot = await bounded(cdp.send("DOMSnapshot.captureSnapshot", SNAPSHOT_PARAMS), operation=_OPERATION)
    leaks = rendered_leaks(snapshot, values)
    if leaks:
        raise refusals.rendered(snapshot, leaks, stage=stage)


def _settle_expression() -> str:
    """Page script resolving true after `_SETTLE_FRAMES` animation frames, or false after the timeout."""
    frames = "resolve(true)"
    for _ in range(_SETTLE_FRAMES):
        frames = f"requestAnimationFrame(() => {frames})"
    timeout_ms = int(_SETTLE_TIMEOUT_SECONDS * 1000)
    return f"new Promise((resolve) => {{ setTimeout(() => resolve(false), {timeout_ms}); {frames}; }})"


async def _settle(cdp: Any) -> None:
    """Let rendering settle before counting begins; a page that does not settle is logged, not refused."""
    params = {"expression": _settle_expression(), "awaitPromise": True, "returnByValue": True}
    try:
        evaluated = cdp.send("Runtime.evaluate", params)
        reply = await bounded(evaluated, operation=_OPERATION, timeout=2 * _SETTLE_TIMEOUT_SECONDS)
    except Exception as exc:
        log.info("octowright.macro.redacted_screenshot.unsettled", error=type(exc).__name__)
        return
    if reply.get("result", {}).get("value") is not True:
        log.info("octowright.macro.redacted_screenshot.unsettled", error=None)


async def _capture(cdp: Any, image: str) -> bytes:
    """The screenshot's bytes from ``Page.captureScreenshot``; nothing is written."""
    for attempt in range(1, _CAPTURE_ATTEMPTS + 1):
        try:
            shot = await bounded(cdp.send("Page.captureScreenshot", {"format": image}), operation=_OPERATION)
            break
        except Exception as exc:
            if _NO_FRAME not in str(exc) or attempt == _CAPTURE_ATTEMPTS or not await _can_produce_frame(cdp):
                raise
            log.debug("octowright.macro.redacted_screenshot.no_frame", attempt=attempt)
            await asyncio.sleep(_CAPTURE_RETRY_SECONDS)
    return base64.b64decode(shot["data"])


async def _write(target: Path, data: bytes) -> None:
    """Write a proved screenshot, atomically."""
    target.parent.mkdir(parents=True, exist_ok=True)

    async def write(temporary: Path) -> None:
        temporary.write_bytes(data)

    await atomic_write_via_writer(target, write)


async def _can_produce_frame(cdp: Any) -> bool:
    """Whether asking again could succeed: the page is visible and has a size. Unknown reads as no."""
    try:
        reply = await bounded(
            cdp.send("Runtime.evaluate", {"expression": _CAN_PRODUCE_FRAME, "returnByValue": True}),
            operation=_OPERATION,
        )
    except Exception:
        log.debug("octowright.macro.redacted_screenshot.frame_probe_failed", exc_info=True)
        return False
    return reply.get("result", {}).get("value") is True


def _image_format(target: Path) -> str:
    return "jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "png"


async def _pause_animations(freeze: AnimationFreeze) -> None:
    """Stop the page's CSS animations and transitions, so the scans and the capture see one frame."""
    await freeze.pause()


async def _end_view_transitions(cdp: Any, refusals: Refusals) -> None:
    """End every running view transition, which draws a raster of its scope taken before the redaction."""
    try:
        await bounded(end_view_transitions(cdp), operation=_OPERATION)
    except Exception as exc:
        raise refusals.refuse("a view transition could not be ended", "view transition") from exc


async def _apply_styles(changes: PageChanges, refusals: Refusals) -> None:
    """Have Chrome apply the page's pending style changes; one it cannot apply refuses the screenshot."""
    try:
        await bounded(changes.apply_styles(), operation=_OPERATION)
    except Exception as exc:
        raise refusals.refuse("the page's styles could not be applied", "styles not applied") from exc


async def _release(cdp: Any, freeze: AnimationFreeze) -> None:
    """Resume animations and detach; each step is attempted even if an earlier one failed."""
    with contextlib.suppress(Exception):
        await freeze.resume()
    with contextlib.suppress(Exception):
        await cdp.detach()


async def _restore(controller: PageController, *, quiet: bool) -> bool:
    """Undo the redaction; whether it was undone. The screenshot is written only after this succeeded.

    ``quiet`` logs a failure and returns False instead of raising; a cancellation always propagates.
    """
    try:
        await bounded(controller.restore(), operation=_OPERATION)
    except BaseException as exc:
        log.warning("octowright.macro.redacted_screenshot.restore_failed", error=type(exc).__name__)
        if not quiet or not isinstance(exc, Exception):
            raise
        return False
    finally:
        with contextlib.suppress(Exception):
            await bounded(controller.dispose(), operation=_OPERATION)
    return True


async def _redact_and_capture(
    cdp: Any, changes: PageChanges, values: list[str], image: str, refusals: Refusals
) -> tuple[PageController, bytes]:
    """Redact, settle, count, prove, capture and prove again; on any failure restore and re-raise.

    On failure the captured image is dropped before re-raising, so the exception's traceback, which keeps this
    frame, holds no reference to the unproved image.
    """
    controller: PageController | None = None
    data: bytes | None = None
    try:
        await _end_view_transitions(cdp, refusals)
        closed_roots = await bounded(changes.start(), operation=_OPERATION)
        controller = await bounded(PageController.create(cdp, values), operation=_OPERATION)
        await bounded(controller.redact(closed_roots), operation=_OPERATION)
        await _settle(cdp)
        await _apply_styles(changes, refusals)
        latent = await bounded(changes.sheets_hold(values), operation=_OPERATION)
        await bounded(controller.watch(latent), operation=_OPERATION)
        changes.begin()
        await _require_unrendered(controller, changes, cdp, values, refusals, stage="before")
        data = await _capture(cdp, image)
        await _require_unrendered(controller, changes, cdp, values, refusals, stage="after")
    except _PageChanged as changed:
        # The image, if any, is dropped here: it is never returned or written.
        data = None
        changed.restored = controller is not None and await _restore(controller, quiet=True)
        raise
    except BaseException:
        data = None
        if controller is not None:
            await _restore(controller, quiet=True)
        raise
    return controller, data


async def _proved_capture(
    cdp: Any, freeze: AnimationFreeze, values: list[str], image: str, refusals: Refusals
) -> bytes:
    """Pause animations, then redact, prove, capture, prove again and restore; once more after a capture-time change.

    Only a change found after the capture, on a page whose redaction was undone, is tried again, and only once;
    each attempt counts afresh. Any other refusal, or a second change, refuses.
    """
    await _pause_animations(freeze)
    attempt = 1
    while True:
        changes = PageChanges(cdp)
        refused: tuple[str, dict[str, int]] | None = None
        try:
            controller, data = await _redact_and_capture(cdp, changes, values, image, refusals)
            try:
                await _restore(controller, quiet=False)
            except BaseException:
                del data  # the traceback keeps this frame: it must not keep the image
                raise
            return data
        except _PageChanged as changed:
            if changed.stage == "after" and changed.restored and attempt < _CHANGE_ATTEMPTS:
                log.warning(
                    "octowright.macro.redacted_screenshot.changed_during_capture",
                    attempt=attempt,
                    changes=changed.kinds,
                )
                attempt += 1
                continue
            refused = (changed.stage, changed.kinds)
        finally:
            with contextlib.suppress(Exception):
                await bounded(changes.close(), operation=_OPERATION)
        # Raised outside the handler, so the refusal carries no context chain back to the attempt's frames.
        stage, kinds = refused
        head = f"the page changed {stage} the redacted screenshot"
        raise refusals.refuse(head, "page changed", stage=stage, changes=kinds)


async def redacted_screenshot(
    session: Any,
    action: dict[str, Any],
    sensitive_values: tuple[str, ...],
    *,
    root: Path | None = None,
) -> tuple[int, int]:
    """Pause animations, end a view transition, redact, prove nothing is rendered, screenshot, prove again, restore.

    Returns ``(executed, skipped)``. No file is written unless both proofs passed and the
    page was restored; a capture the page changed during is redacted and taken once more. Refusals raise `ScreenshotRefused` (a ``RuntimeError``) naming
    what was found, its tier and argument, never the value (see `screenshot_refusal`).

    Every value is matched anywhere, word-bounded ledger values included
    (`PrivacyLedger.word_bounded`), and that is deliberate. The redaction also
    catches a value that is reversed, split across nodes or spelled in digits,
    and none of those forms has token boundaries to respect. Blanking
    ``Administrator`` for a typed ``admin`` is the accepted cost of never
    drawing the password.
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
    refusals = Refusals(session, sensitive_values)
    # Re-enters the caller's lease (the macro run, or an artifact run) in the same
    # task, so no other operation on this page interleaves with redact .. restore.
    async with session.operation("macro_run"):
        page = session.page
        try:
            cdp = await bounded(page.context.new_cdp_session(page), operation=_OPERATION)
        except Exception as exc:
            head = "a redacted screenshot needs a Chromium page to read what is rendered"
            raise refusals.refuse(head, "no rendered-surface snapshot") from exc
        freeze = AnimationFreeze(cdp)
        try:
            data = await _proved_capture(cdp, freeze, values, _image_format(target), refusals)
            await _write(target, data)
        finally:
            await _release(cdp, freeze)
        recorder = getattr(session, "recorder", None)
        if recorder is not None:
            recorder.record("screenshot", path=str(target))
    return 1, 0


async def ledger_screenshot(session: Any, path: Path, sensitive_values: tuple[str, ...]) -> None:
    """``BrowserSession.screenshot`` for a session whose privacy ledger holds values.

    The generic ``browser_screenshot`` wrote raw pixels and never consulted the
    ledger, so a password filled through a browser tool -- or a macro credential
    -- that the page rendered back reached a PNG, while the macro path refused
    the same screenshot. It now takes the macro path's boundary: a caller's
    installed handler if there is one, otherwise :func:`redacted_screenshot`,
    which redacts, proves nothing is rendered, and refuses on an engine it
    cannot prove that on. It does not consult ``OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS``:
    that ``refuse`` default governs a macro run, and applied here it would make
    every screenshot after a login fail. The caller consults
    ``OCTOWRIGHT_LEDGER_SCREENSHOTS`` (:func:`guards_ledger_screenshot`), whose
    ``allow`` takes the raw screenshot instead unless a handler is installed.

    *path* was already chosen and contained by the caller, so it is its own root.
    """
    action = {"action": "screenshot", "path": str(path)}
    handler = installed_handler(session)
    if handler is not None:
        if await handler(action=action, sensitive_values=sensitive_values) is None:
            head = "the session's screenshot privacy handler refused the screenshot"
            raise Refusals(session, sensitive_values).refuse(head, "handler refused")
        return
    await redacted_screenshot(session, action, sensitive_values, root=path.parent)


async def dispatch_classified_screenshot(
    session: Any,
    action: dict[str, Any],
    sensitive_values: tuple[str, ...],
) -> tuple[int, int]:
    """Route a screenshot holding policy-admitted values through the privacy boundary.

    A screenshot of a page a credential was typed into is a durable copy of
    that credential, so the generic capture path is never used. In order: an
    explicitly authorized handler decides; otherwise octowright's own redacted
    screenshot runs when ``OCTOWRIGHT_MACRO_CLASSIFIED_SCREENSHOTS=redact``;
    otherwise the screenshot is refused.
    """
    handler = installed_handler(session)
    if handler is not None:
        handled = await handler(action=action, sensitive_values=sensitive_values)
        if handled is None:
            head = "classified macro screenshot privacy handler refused the action"
            raise Refusals(session, sensitive_values).refuse(head, "handler refused")
        return handled
    if classified_screenshot_policy() == "redact":
        return await redacted_screenshot(session, action, sensitive_values)
    head = "classified macro screenshot requires an explicit privacy handler"
    raise Refusals(session, sensitive_values).refuse(head, "no privacy handler")
