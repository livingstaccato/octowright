# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Keyboard (WAI-ARIA APG) drag-and-drop: grab, navigate, drop, verify, release.

Takes ``session`` first and opens its own literal ``session.operation(...)``
lease, exactly like ``session/aria_redaction.py``. That is not ceremony: the
operation-gate architecture scanner is per-function syntax analysis and does
not follow call graphs, so a helper that accepted a bare Playwright target
would be reported as ungated Playwright access even when every caller already
held a lease. ``gated_operation`` is re-entrant for the owning task, so
nesting inside the mixin's lease costs nothing.

Keystrokes go through ``session.page.keyboard`` rather than the active target:
``Frame`` has no ``.keyboard`` attribute (``Page`` does), so a frame-scoped
call would crash on the first key press. Element lookup still uses
``session._target()`` so a frame-scoped selector resolves in its own frame.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Final

from provide.telemetry import get_logger

log = get_logger(__name__)

NAV_MODES: Final = frozenset({"tab", "arrow", "keys"})

_TAB_KEYS: Final[dict[str, str]] = {"forward": "Tab", "backward": "Shift+Tab"}
_ARROW_KEYS: Final[dict[str, str]] = {
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
}

DEFAULT_TAB_DIRECTION: Final = "forward"
DEFAULT_ARROW_DIRECTION: Final = "down"

# The accessible-name-free default: a widget in grab mode almost always keeps
# focus on the grabbed element, so "is the source still focused" is the check
# that works without the caller knowing anything about the widget.
_DEFAULT_GRABBED_JS: Final = "(el) => document.activeElement === el"


class A11yDragDropError(Exception):
    """An infrastructure failure that makes the result meaningless.

    Deliberately NOT raised for an ordinary failed verify -- that is a normal
    outcome the caller reads off ``stage_reached`` (spec section 7).
    """


def validate_params(*, nav_key: str, nav_key_sequence: list[str] | None, verify_fields_set: int) -> None:
    """Reject impossible parameter combinations before any key is sent.

    Both rules exist because the failure is otherwise silent: zero verify
    fields makes every call "succeed" without checking anything, and a
    ``nav_key_sequence`` alongside ``tab``/``arrow`` leaves it genuinely
    ambiguous which navigation the caller meant.
    """
    if nav_key not in NAV_MODES:
        raise ValueError(f"nav_key must be one of {sorted(NAV_MODES)}, got {nav_key!r}")
    if nav_key == "keys" and not nav_key_sequence:
        raise ValueError("nav_key='keys' requires a non-empty nav_key_sequence")
    if nav_key != "keys" and nav_key_sequence:
        raise ValueError(f"nav_key_sequence is only valid with nav_key='keys', not {nav_key!r}")
    if verify_fields_set != 1:
        raise ValueError(
            f"exactly one of verify_js/verify_selector_appears/verify_selector_gone/"
            f"verify_text_contains is required, got {verify_fields_set}"
        )


def _nav_keys(
    nav_key: str, nav_direction: str | None, nav_key_sequence: list[str] | None, max_nav_steps: int
) -> list[str]:
    """The exact key presses navigation will send, resolved up front.

    Returning a concrete list (rather than deciding per step) is what makes
    ``nav_steps_taken`` truthful for all three modes: ``keys`` sends its
    sequence once, while ``tab``/``arrow`` repeat one key ``max_nav_steps``
    times.
    """
    if nav_key == "keys":
        return list(nav_key_sequence or [])
    if nav_key == "arrow":
        key = _ARROW_KEYS[nav_direction or DEFAULT_ARROW_DIRECTION]
    else:
        key = _TAB_KEYS[nav_direction or DEFAULT_TAB_DIRECTION]
    return [key] * max_nav_steps


def _count_verify_fields(
    verify_js: str | None,
    verify_selector_appears: str | None,
    verify_selector_gone: str | None,
    verify_text_contains: str | None,
) -> int:
    return sum(1 for v in (verify_js, verify_selector_appears, verify_selector_gone, verify_text_contains) if v)


async def _check_verify(
    session: Any,
    *,
    verify_js: str | None,
    verify_selector_appears: str | None,
    verify_selector_gone: str | None,
    verify_text_contains: str | None,
) -> bool:
    """One evaluation of whichever verify shape the caller chose.

    Takes its own lease around the Playwright calls, re-entrant for the
    caller's task exactly like ``run_a11y_dragdrop``'s own lease -- the one
    call site already holds it. Gating it here, rather than trusting that
    every current and future caller already holds one, is what keeps this
    call site visible to the operation-gate architecture scanner as gated on
    its own terms (it does per-function syntax analysis and does not follow
    call graphs).
    """
    async with session.operation("browser_a11y_dragdrop"):
        target = session._target()
        if verify_js is not None:
            return bool(await target.evaluate(verify_js))
        if verify_selector_appears is not None:
            return await target.locator(verify_selector_appears).count() > 0
        if verify_selector_gone is not None:
            return await target.locator(verify_selector_gone).count() == 0
        return bool(await target.evaluate("(needle) => document.body.innerText.includes(needle)", verify_text_contains))


async def run_a11y_dragdrop(
    session: Any,
    *,
    source_selector: str,
    nav_key: str = "tab",
    nav_direction: str | None = None,
    nav_key_sequence: list[str] | None = None,
    max_nav_steps: int = 12,
    grab_key: str = "Space",
    drop_key: str = "Space",
    release_key: str = "Escape",
    grabbed_predicate_js: str | None = None,
    verify_js: str | None = None,
    verify_selector_appears: str | None = None,
    verify_selector_gone: str | None = None,
    verify_text_contains: str | None = None,
    verify_timeout_ms: int = 2000,
    verify_poll_ms: int = 100,
) -> dict[str, Any]:
    """One atomic keyboard drag attempt. Never retries -- that is the caller's job."""
    validate_params(
        nav_key=nav_key,
        nav_key_sequence=nav_key_sequence,
        verify_fields_set=_count_verify_fields(
            verify_js, verify_selector_appears, verify_selector_gone, verify_text_contains
        ),
    )

    result: dict[str, Any] = {
        "ok": False,
        "grabbed": False,
        "dropped": False,
        "verified": False,
        "released": False,
        "stage_reached": "failed_grab",
        "nav_steps_taken": 0,
    }

    async with session.operation("browser_a11y_dragdrop"):
        target = session._target()
        keyboard = session.page.keyboard

        # --- Grab -------------------------------------------------------
        source = target.locator(source_selector)
        await source.focus()
        await keyboard.press(grab_key)

        # `grab_key` is already pressed at this point, so the widget's
        # grabbed state is genuinely unknown until the predicate resolves --
        # everything from here, INCLUDING an exception raised BY the
        # predicate check itself, must go through the release path below.
        # The one case that legitimately skips it is the predicate
        # returning False: the key was pressed but the widget demonstrably
        # never entered grab mode, so there is nothing to release (that is
        # an ordinary early ``return``, not an exception, so it never
        # reaches ``except`` below). A predicate that THROWS is different --
        # ``grabbed_predicate_js`` is caller-supplied arbitrary JS, so a
        # throwing predicate, or a target-closed/detached ``evaluate``, is a
        # real path with grabbed state genuinely unknown. A spurious Escape
        # when nothing was grabbed is a no-op in the APG pattern, so we err
        # toward releasing. A grab that succeeded followed by a drop that
        # did not would otherwise leave the widget stuck in grab mode,
        # indistinguishable from a grab that never registered -- that is the
        # exact bug this ``try``/``except`` exists to prevent.
        try:
            grabbed = bool(await source.evaluate(grabbed_predicate_js or _DEFAULT_GRABBED_JS))
            if not grabbed:
                return result
            result["grabbed"] = True

            for key in _nav_keys(nav_key, nav_direction, nav_key_sequence, max_nav_steps):
                await keyboard.press(key)
                result["nav_steps_taken"] += 1
            result["stage_reached"] = "navigated"

            await keyboard.press(drop_key)
            result["dropped"] = True
            result["stage_reached"] = "dropped"

            # Poll in THIS task. Never spawn one: `gated_operation` re-enters
            # only for the owning task, so a helper task calling back into a
            # gated session method would queue behind the lease this frame is
            # still holding and deadlock until the queue timeout.
            deadline = time.monotonic() + verify_timeout_ms / 1000
            while True:
                if await _check_verify(
                    session,
                    verify_js=verify_js,
                    verify_selector_appears=verify_selector_appears,
                    verify_selector_gone=verify_selector_gone,
                    verify_text_contains=verify_text_contains,
                ):
                    result["verified"] = True
                    result["ok"] = True
                    result["stage_reached"] = "verified"
                    return result
                if time.monotonic() >= deadline:
                    break
                await asyncio.sleep(verify_poll_ms / 1000)

            await keyboard.press(release_key)
            result["released"] = True
            result["stage_reached"] = "failed_verify"
            return result
        except Exception:
            # The release press itself can fail -- most likely in exactly
            # the situation this handler exists for (page/connection already
            # gone). A failing release must not replace the ORIGINAL
            # exception the caller needs to see, so the secondary failure is
            # caught, logged (silent-swallow policy: a swallow in a
            # user-action path must log, not vanish), and the bare `raise`
            # below re-raises the exception this `except` is still handling
            # -- not the release failure.
            try:
                await keyboard.press(release_key)
                result["released"] = True
            except Exception as release_exc:
                log.warning(
                    "octowright.a11y_dragdrop.release_failed",
                    instance_id=getattr(session, "instance_id", None),
                    error=repr(release_exc),
                )
            raise
