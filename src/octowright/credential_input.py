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
and webkit. So :func:`checked_fill` resolves the element as the same step without a
credential would -- a selector ``fill`` its first match, a ``fill_by``
strictly, raising on several -- checks the frame that owns it, and fills that
element handle. When the element is replaced the handle detaches. That covers a
re-render and a navigation, and all three engines report it at once as "not
attached". The fill then re-resolves and re-checks, so a re-rendered field is
still filled, and a navigated one is refused. Remaining window: inside the
single Playwright ``fill``, after the element-scoped focus and select step,
the value is inserted through the page keyboard, which types into whatever
document has focus. A navigation that commits between those two driver steps
is not seen. That is one driver round trip, not the whole actionability wait.

**Type.** Keys follow focus, as a real keyboard's do, so an auto-advancing
code gets one digit per box. :func:`checked_type` therefore types one
character at a time. Before each one it asks the page, in one call per
document, which element has focus, walking into focused iframes, checks the
origin of that element's document, and presses the key through that
element's handle. The rest of the value is stopped
(:class:`CredentialInputStopped`, which names why and never the value) when:

* the document that has focus is not the one that received the previous key.
  Any navigation, same-origin included, and focus moving into another frame,
  replace it. A foreign document is refused by *check* first, naming its
  origin. Playwright has no public identity for a document (a ``Frame``
  survives its navigations), so the first key marks its document with a
  property holding a per-step token, and every later key requires it. A
  navigated document is a new ``document`` object without the mark, and
  ``history.pushState`` keeps the old one, measured on all three engines;
* focus is on ``<body>``, the root element or nothing -- where it lands when
  the focused field is removed (measured on all three engines), so a
  re-render would otherwise swallow the rest of the value and report success;
* focus moved to an element that takes no text (a button, a link) and is not
  the one the first key went to. Focus moving to another text field of the
  same document is followed, which is what an auto-advancing code needs.

Remaining window: inside the single Playwright element ``type``/``press``,
after its focus step, the key goes through the page keyboard, so a navigation
that commits in that one driver round trip receives that one key. Focus moved
by the page within the same document to another text field is followed by
design, so a single-page app that swaps its view in place (``pushState``) and
focuses a search box gets the rest of the value; and a document restored from
the back/forward cache during the same step still carries its mark.

**Time.** Nothing here may hang the session gate its caller holds. The whole
step, retries and typing and the pause between keys, runs under one
``asyncio.timeout`` of the step's budget (``asyncio.timeout`` rather than
``wait_for``, which would run it in another task and lose the gate's
re-entry), and each Playwright call that takes a ``timeout`` is given what is
left. A step that spends the budget raises :class:`CredentialInputStopped`.
That matches the same step without a credential: Playwright's own
``page.type(delay=..., timeout=...)`` fails once the typing outlasts the
timeout, measured on all three engines.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

_credential_input_log = logging.getLogger(__name__)

#: One call per document on the way to the element keys go to now, through
#: open shadow roots. It answers the element when it may take the key,
#: ``{frame: el}`` when focus is inside a child frame, and ``{stop: why}``
#: otherwise. ``first`` marks the document (and the element) the first key
#: goes to; every later key needs that mark, so the answer is decided in the
#: page and a plain input costs this one round trip.
FOCUSED_ELEMENT_JS = """([key, token, first]) => {
  let el = document.activeElement;
  while (el && el.shadowRoot && el.shadowRoot.activeElement) el = el.shadowRoot.activeElement;
  if (el && (el.localName === 'iframe' || el.localName === 'frame')) return {frame: el};
  if (!el || el === document.body || el === document.documentElement) return {stop: 'nothing'};
  if (first) {
    Object.defineProperty(document, key, {value: {token, el}, configurable: true});
    return el;
  }
  const mark = Object.getOwnPropertyDescriptor(document, key);
  if (!mark || !mark.value || mark.value.token !== token) return {stop: 'document'};
  const textless = ['button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit'];
  const takesText = el.isContentEditable || el.localName === 'textarea'
    || (el.localName === 'input' && !textless.includes(el.type));
  return takesText || el === mark.value.el ? el : {stop: 'element'};
}"""

_MARK_KEY = "__octowrightCredentialInput"

_STOPPED_BECAUSE = {
    "nothing": "nothing that takes text has focus (focus is on the page body), so the rest would go nowhere",
    "document": "the document that received the previous key no longer has focus: the page navigated, "
    "or focus moved into another frame",
    "element": "focus moved to an element that takes no text",
    "frame": "focus is inside a frame whose document cannot be reached",
}

# What Playwright says when a handle's element is gone: replaced by a
# re-render, or its document navigated away. Measured on all three engines.
_DETACHED_MARKERS = ("not attached to the DOM", "Execution context was destroyed", "Frame was detached")


class CredentialInputStopped(RuntimeError):
    """The rest of a credential was not typed. The message says why, never what was typed.

    The caller names the step (``credential_sinks.credential_input_stopped``).
    """


def credential_input_detached(exc: BaseException) -> bool:
    """Whether *exc* means the element went away, so resolving again is the fix."""
    text = str(exc)
    return any(marker in text for marker in _DETACHED_MARKERS)


def _ms_left(deadline: float) -> float:
    return max(1.0, (deadline - time.monotonic()) * 1000)


async def _release(handles: list[Any]) -> None:
    """Dispose *handles* together, under a short bound of their own.

    After the step: disposing one per key cost a driver round trip each, and
    this also runs when the step's budget is spent, when a wedged target
    would not answer a dispose either.
    """
    if not handles:
        return
    try:
        async with asyncio.timeout(2):
            results = await asyncio.gather(*(handle.dispose() for handle in handles), return_exceptions=True)
    except TimeoutError:
        _credential_input_log.debug("credential_input: handle dispose did not answer; left to the page's teardown")
        return
    for result in results:
        if isinstance(result, Exception):  # the document it lived in may be gone, which is the point
            _credential_input_log.debug("credential_input: handle dispose failed: %s", type(result).__name__)


async def _within(timeout_ms: float, step: Awaitable[None]) -> None:
    """Await *step*, raising :class:`CredentialInputStopped` once *timeout_ms* is spent."""
    budget = asyncio.timeout(timeout_ms / 1000)
    try:
        async with budget:
            await step
    except TimeoutError:
        if not budget.expired():
            raise
        raise CredentialInputStopped(f"the credential step did not finish within {timeout_ms:g}ms") from None


async def checked_fill(
    session: Any, locator: Any, value: str, check: Callable[[str], None], timeout_ms: float, *, strict: bool
) -> None:
    """Fill *locator*, but only in a document *check* accepts; see the module docstring.

    ``strict`` picks the element as the same step without a credential does:
    a selector ``fill`` takes the first match (``page.fill``), and a
    ``fill_by`` raises Playwright's strict-mode error on several
    (``Locator.fill``), which ``Locator.element_handle`` raises too.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    target = locator if strict else locator.first
    handles: list[Any] = []

    async def fill() -> None:
        async with session.operation("macro_credential_fill_origin"):
            while True:
                handle = await target.element_handle(timeout=_ms_left(deadline))
                handles.append(handle)
                owner = await handle.owner_frame()
                check(str(getattr(owner, "url", "") or ""))
                try:
                    await handle.fill(value, timeout=_ms_left(deadline))
                    return
                except Exception as exc:
                    if not credential_input_detached(exc) or time.monotonic() >= deadline:
                        raise

    try:
        await _within(timeout_ms, fill())
    finally:
        await _release(handles)


async def _focused(session: Any, token: str, first: bool, handles: list[Any]) -> tuple[Any, Any, str | None]:
    """``(element, frame, stop)``: the element keys go to now, the frame that owns it, or why there is none.

    Every handle it takes goes on *handles*, before anything can raise, so
    the caller releases each one whichever way this returns.
    """
    async with session.operation("macro_credential_fill_origin"):
        frame = session.page.main_frame
        while True:
            answer = await frame.evaluate_handle(FOCUSED_ELEMENT_JS, [_MARK_KEY, token, first])
            handles.append(answer)
            element = answer.as_element()
            if element is not None:
                return element, frame, None
            props = await answer.get_properties()
            handles.extend(props.values())
            owner = props.get("frame")
            owner = owner.as_element() if owner is not None else None
            if owner is None:
                stop = props.get("stop")
                return None, frame, str(await stop.json_value()) if stop is not None else "nothing"
            child = await owner.content_frame()
            if child is None:
                return None, frame, "frame"
            frame = child


async def type_character(handle: Any, char: str, timeout_ms: float) -> None:
    """The default key: Playwright's own ``type`` for one character, through the focused element."""
    await handle.type(char, timeout=timeout_ms)


async def checked_type(
    session: Any,
    locator: Any,
    text: str,
    check: Callable[[str], None],
    *,
    delay_ms: float | None,
    timeout_ms: float,
    send: Callable[[Any, str, float], Awaitable[None]] = type_character,
) -> None:
    """Type *text* into *locator*'s first match, re-checking where each key goes; see the module docstring.

    *send* delivers one character through the focused element's handle,
    within the milliseconds it is given. Its default is Playwright's text
    typing, and live replay's ``key_mode="keys"`` passes a physical-key press
    instead. ``delay_ms`` is the pause between two keys, and each check comes
    after that pause, just before the key.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    # Per step, so a mark an earlier step left on the same document is not this one's.
    token = secrets.token_hex(8)
    handles: list[Any] = []

    async def type_all() -> None:
        async with session.operation("macro_credential_fill_origin"):
            await locator.first.focus(timeout=_ms_left(deadline))
            for index, char in enumerate(text):
                if index and delay_ms:
                    await asyncio.sleep(delay_ms / 1000)
                await _checked_key(session, char, check, send, deadline, token, not index, handles)

    try:
        await _within(timeout_ms, type_all())
    finally:
        await _release(handles)


async def _checked_key(
    session: Any,
    char: str,
    check: Callable[[str], None],
    send: Callable[[Any, str, float], Awaitable[None]],
    deadline: float,
    token: str,
    first: bool,
    handles: list[Any],
) -> None:
    async with session.operation("macro_credential_fill_origin"):
        while True:
            try:
                element, frame, stop = await _focused(session, token, first, handles)
            except Exception as exc:
                # A navigation destroyed the document mid-lookup: ask the one that
                # replaced it, which is then refused or stopped below.
                if not credential_input_detached(exc) or time.monotonic() >= deadline:
                    raise
                continue
            # The origin first: a foreign document is refused by name, whatever else is wrong.
            check(str(getattr(frame, "url", "") or ""))
            if stop is not None:
                raise CredentialInputStopped(_STOPPED_BECAUSE.get(stop, stop))
            try:
                await send(element, char, _ms_left(deadline))
                return
            except Exception as exc:
                # Replaced between the lookup and the key: ask again, which stops
                # unless focus is still somewhere this step may type.
                if not credential_input_detached(exc) or time.monotonic() >= deadline:
                    raise
