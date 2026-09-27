# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Typing a credential only into a document whose origin passed the check.

Shared by the live session (``session.core_page_mixin`` /
``core_locator_mixin``, under their gated operations) and the exported macro
CLI, which renders this module's source verbatim
(:func:`octowright.artifacts.script_export.render_macro_cli`). So it imports
only the standard library, and every name here is fair game in the script.

*check* is called with the URL of the document about to receive the value and
raises to refuse it (``credential_sinks.offsite_credential_origin`` decides).
*session* supplies ``page`` and ``operation(name)``. Live, that is the
``BrowserSession`` and the operation re-enters the lease its caller already
holds. The script passes a stand-in whose operation does nothing, because a
standalone script has one page and no gate.

**Fill.** A plain selector fill cannot be checked. Playwright's actionability
wait re-resolves the selector after a navigation, so a trusted page that holds
a disabled field and then moves to a foreign origin with an enabled one gets
the value filled on the foreign origin. That was measured on chromium, firefox
and webkit. So :func:`checked_fill` resolves the FIRST match (non-strict, as a
selector fill is), checks the frame that owns it, and fills that element
handle. When the element is replaced the handle detaches. That covers a
re-render and a navigation, and all three engines report it at once as "not
attached". The fill then re-resolves and re-checks, so a re-rendered field is
still filled, and a navigated one is refused. Remaining window: inside the
single Playwright ``fill``, after the element-scoped focus and select step,
the value is inserted through the page keyboard, which types into whatever
document has focus. A navigation that commits between those two driver steps
is not seen. That is one driver round trip, not the whole actionability wait.

**Type.** Keys follow focus, as a real keyboard's do, so an auto-advancing
code gets one digit per box. :func:`checked_type` therefore types one
character at a time. Before each one it finds the element that has focus,
walking into focused iframes, checks the origin of that element's document,
and presses the key through that element's handle. A navigation between two
keys detaches the handle or changes the focused document, and the rest of the
value is refused. The window per key is the same one-round-trip gap inside
Playwright's element ``press``/``type``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

#: Resolves the deepest focused element of one document, through open shadow roots.
FOCUSED_ELEMENT_JS = """() => {
  let el = document.activeElement || document.body || document.documentElement;
  while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
  return el;
}"""

# What Playwright says when a handle's element is gone: replaced by a
# re-render, or its document navigated away. Measured on all three engines.
_DETACHED_MARKERS = ("not attached to the DOM", "Execution context was destroyed", "Frame was detached")


def credential_input_detached(exc: BaseException) -> bool:
    """Whether *exc* means the element went away, so resolving again is the fix."""
    text = str(exc)
    return any(marker in text for marker in _DETACHED_MARKERS)


def _ms_left(deadline: float) -> float:
    return max(1.0, (deadline - time.monotonic()) * 1000)


async def _release(handle: Any) -> None:
    try:
        await handle.dispose()
    except Exception:  # the document it lived in may be gone, which is the point
        pass


async def checked_fill(session: Any, locator: Any, value: str, check: Callable[[str], None], timeout_ms: float) -> None:
    """Fill *locator*'s first match, but only in a document *check* accepts; see the module docstring."""
    deadline = time.monotonic() + timeout_ms / 1000
    async with session.operation("macro_credential_fill_origin"):
        while True:
            handle = await locator.first.element_handle(timeout=_ms_left(deadline))
            try:
                owner = await handle.owner_frame()
                check(str(getattr(owner, "url", "") or ""))
                try:
                    await handle.fill(value, timeout=_ms_left(deadline))
                    return
                except Exception as exc:
                    if not credential_input_detached(exc) or time.monotonic() >= deadline:
                        raise
            finally:
                await _release(handle)


async def focused_element(session: Any) -> tuple[Any, Any]:
    """``(handle, frame)``: the element keys go to now, and the frame whose document owns it."""
    async with session.operation("macro_credential_fill_origin"):
        frame = session.page.main_frame
        while True:
            handle = (await frame.evaluate_handle(FOCUSED_ELEMENT_JS)).as_element()
            child = await handle.content_frame() if handle is not None else None
            if child is None:
                return handle, frame
            await _release(handle)
            frame = child


async def type_character(handle: Any, char: str) -> None:
    """The default key: Playwright's own ``type`` for one character, through the focused element."""
    await handle.type(char)


async def checked_type(
    session: Any,
    locator: Any,
    text: str,
    check: Callable[[str], None],
    *,
    delay_ms: float | None,
    timeout_ms: float,
    send: Callable[[Any, str], Awaitable[None]] = type_character,
) -> None:
    """Type *text* into *locator*'s first match, re-checking the receiving origin before every key.

    *send* delivers one character through the focused element's handle. Its
    default is Playwright's text typing, and live replay's ``key_mode="keys"``
    passes a physical-key press instead. ``delay_ms`` is the pause between two
    keys, and each check comes after that pause, just before the key.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    async with session.operation("macro_credential_fill_origin"):
        await locator.first.focus(timeout=_ms_left(deadline))
        for index, char in enumerate(text):
            if index and delay_ms:
                await asyncio.sleep(delay_ms / 1000)
            await _checked_key(session, char, check, send, deadline)


async def _checked_key(
    session: Any, char: str, check: Callable[[str], None], send: Callable[[Any, str], Awaitable[None]], deadline: float
) -> None:
    async with session.operation("macro_credential_fill_origin"):
        while True:
            handle, frame = await focused_element(session)
            if handle is None:  # nothing in the document can take the key
                raise RuntimeError("no element has focus to type into")
            try:
                check(str(getattr(frame, "url", "") or ""))
                try:
                    await send(handle, char)
                    return
                except Exception as exc:
                    if not credential_input_detached(exc) or time.monotonic() >= deadline:
                        raise
            finally:
                await _release(handle)
