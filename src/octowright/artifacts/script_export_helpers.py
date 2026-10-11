# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Runtime helpers rendered once into the exported CLI, above its action loop.

``STATE_HELPERS`` is source text, not code this module runs: the network
ledger and settle wait are rendered from ``request_failures``' own source (so
the copy cannot drift from the session's), followed by the page/frame
accessors, dialog policy, mock handler and keyboard drag-and-drop that the
dispatch bodies in ``script_export_actions`` call against a plain Playwright
``page`` in the generated script.
"""

from __future__ import annotations

import inspect

from octowright import request_failures
from octowright.assertion_warnings import STRICT_OPTIONS, assertion_warning, strict_option, strict_refusal
from octowright.macros._redact import _REDACT_VALUE_ACTIONS


def _network_helpers() -> str:
    """The session's own network ledger and settle wait, rendered from their source.

    Not hand-mirrored: the copy had drifted (no in-flight bound, a per-tick
    ``is_closed`` poll instead of a close listener, requests keyed by an ``id``
    a collected object's successor could reuse). The names below are the ones
    the rendered source reads.
    """
    constants = "".join(
        f"{name} = {getattr(request_failures, name)!r}\n"
        for name in (
            "INFLIGHT_REQUEST_LIMIT",
            "NETWORK_SETTLE_TIMEOUT_MS",
            "NETWORK_QUIET_SECONDS",
            "NETWORK_SETTLE_POLL_SECONDS",
        )
    )
    frozensets = "".join(
        f"{name} = frozenset({sorted(getattr(request_failures, name))!r})\n"
        for name in ("ABORTED_REQUEST_FAILURES", "HTTP_ERROR_RESOURCE_TYPES", "LONG_LIVED_RESOURCE_TYPES")
    )
    sources = (
        request_failures.is_http_error,
        request_failures.request_frame,
        request_failures.NetworkLedger,
        request_failures.settle_network,
        # How a passing check's caveat is worded, and when a step makes it fail, shared with macro_run.
        assertion_warning,
        strict_option,
        strict_refusal,
    )
    constants += f"STRICT_OPTIONS = {STRICT_OPTIONS!r}\n"
    return constants + frozensets + "\n\n" + "\n\n\n".join(inspect.getsource(obj).rstrip() for obj in sources)


#: Runtime helpers the dispatch bodies below call. Rendered into the exported
#: script once, above the action loop.
STATE_HELPERS = (
    _network_helpers()
    + """


# Action kinds whose text/value is always redacted in logs, as replay redacts them.
_REDACT_VALUE_ACTIONS = """
    + repr(sorted(_REDACT_VALUE_ACTIONS))
    + "\n\n\n"
    + '''def _watch_network(state: dict[str, Any], page: Any) -> None:
    """Feed the page's events to the ledger expect_network_clean judges, from the moment it exists.

    Wired as the session wires them, including the close that forgets a
    closed page's requests. Lazy like the dialog policy: a macro that never
    asserts it never touches ``page.on``.
    """
    if not state["watch_network"]:
        return
    # Once per page: open_url watches its tab before goto, and the context's
    # page event (_watch_context) reports that same tab too.
    watched = state.setdefault("watched_pages", [])
    if any(seen is page for seen in watched):
        return
    watched.append(page)
    ledger = state["network"]
    page.on("request", lambda request: ledger.request_started(request, page))
    page.on("requestfinished", ledger.request_finished)
    page.on("requestfailed", ledger.request_failed)
    page.on("framenavigated", lambda frame: ledger.frame_navigated(frame, page))
    page.on("framedetached", ledger.frame_detached)
    page.on("close", lambda: ledger.page_closed(page))
    page.on("pageerror", ledger.page_error)
    page.on("response", ledger.response)


def _watch_context(state: dict[str, Any], page: Any) -> None:
    """Watch every page *page*'s context opens later -- a popup or a target=_blank tab included.

    Live replay wires the same listeners on each such page (the context's
    ``page`` event, ``_register_popup``), so a request that fails in an OAuth
    popup fails the run there; without this it passed here.
    """
    if state["watch_network"]:
        page.context.on("page", lambda opened: _watch_network(state, opened))


def _report_assertion(state: dict[str, Any], index: int, kind: str, observation: dict[str, Any]) -> None:
    """Print what a passing check saw, as macro_run returns it, and a warning line for a caveat."""
    record = {"event": "assertion", "index": index, "action": kind, **observation}
    warning = assertion_warning(kind, observation)
    if warning is not None:
        record["warning"] = warning
    print(json.dumps(_redact_value(record, state["sensitive_values"]), sort_keys=True))
    if warning is not None:
        line = {"event": "warning", "index": index, "action": kind, "warning": warning}
        print(json.dumps(_redact_value(line, state["sensitive_values"]), sort_keys=True))


def _page(state: dict[str, Any]) -> Any:
    """The active page — what switch_page/close_page/open_url move between."""
    return state["pages"][state["index"]]


def _target(state: dict[str, Any]) -> Any:
    """The active frame if one is selected, else the active page.

    Mirrors ``BrowserSession._target()``: every element-scoped action resolves
    through it, so a recorded switch_frame keeps meaning what it meant live.
    """
    return state["frame"] if state["frame"] is not None else _page(state)


def _install_dialog_policy(state: dict[str, Any]) -> None:
    """Hook every not-yet-hooked page. Called only once a policy is actually set.

    Lazy on purpose: a macro with no ``set_dialog_policy`` never touches
    ``page.on`` at all, so the exported script stays a straight-line driver for
    the common case.
    """

    async def _on_dialog(dialog: Any) -> None:
        policy = state["dialog_policy"]
        if policy == "accept":
            await dialog.accept(state["dialog_prompt_text"] or "")
        elif policy == "dismiss":
            await dialog.dismiss()

    for page in state["pages"]:
        if page in state["dialog_pages"]:
            continue
        page.on("dialog", lambda d: asyncio.ensure_future(_on_dialog(d)))
        state["dialog_pages"].append(page)


async def _switch_frame(state: dict[str, Any], action: dict[str, Any]) -> None:
    selector = action.get("selector")
    name = action.get("name")
    url_pattern = action.get("url_pattern")
    page = _page(state)
    if selector is not None:
        handle = await page.wait_for_selector(selector, timeout=action.get("timeout_ms"))
        frame = await handle.content_frame()
    elif name is not None:
        frame = page.frame(name=name)
    elif url_pattern is not None:
        frame = page.frame(url=re.compile(url_pattern))
    else:
        raise RuntimeError("switch_frame needs one of selector/name/url_pattern")
    if frame is None:
        raise RuntimeError(f"no frame matched: {action!r}")
    state["frame"] = frame


def _mock_handler(action: dict[str, Any]) -> Any:
    status = action.get("status", 200)
    body = action.get("body") or ""
    content_type = action.get("content_type", "application/json")
    headers = action.get("headers") or {}

    async def _fulfill(route: Any) -> None:
        await route.fulfill(status=status, body=body, content_type=content_type, headers=headers)

    return _fulfill


# Keyboard (WAI-ARIA APG) drag-and-drop navigation key tables, mirroring
# session/a11y_dragdrop.py's _TAB_KEYS / _ARROW_KEYS exactly.
_A11Y_TAB_KEYS = {"forward": "Tab", "backward": "Shift+Tab"}
_A11Y_ARROW_KEYS = {"up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight"}
# Mirrors _DEFAULT_GRABBED_JS: a widget in grab mode almost always keeps focus
# on the grabbed element, so this is the check that works without the caller
# knowing anything about the widget.
_A11Y_DEFAULT_GRABBED_JS = "(el) => document.activeElement === el"


_A11Y_VERIFY_FIELDS = ("verify_js", "verify_selector_appears", "verify_selector_gone", "verify_text_contains")


def _a11y_validate(action: dict[str, Any]) -> None:
    """Mirrors ``a11y_dragdrop.validate_params``: refuse impossible shapes.

    Without it the exported script diverged from replay on exactly the inputs
    that need an error: ``nav_key="swipe"`` raised on replay but fell through
    to Tab navigation here, ``nav_key="keys"`` with no sequence silently sent
    zero keys, and a missing verify field reached the dispatch below as a bare
    ``KeyError``. An exported script is run without octowright's lint, so this
    is the only thing standing between a hand-edited macro and a silent
    behaviour difference.
    """
    nav_key = action.get("nav_key", "tab")
    if nav_key not in ("tab", "arrow", "keys"):
        raise RuntimeError(f"a11y_dragdrop nav_key must be one of ['arrow', 'keys', 'tab'], got {nav_key!r}")
    if nav_key == "keys" and not action.get("nav_key_sequence"):
        raise RuntimeError("a11y_dragdrop nav_key='keys' requires a non-empty nav_key_sequence")
    if nav_key != "keys" and action.get("nav_key_sequence"):
        raise RuntimeError(f"a11y_dragdrop nav_key_sequence is only valid with nav_key='keys', not {nav_key!r}")
    nav_direction = action.get("nav_direction")
    table = _A11Y_ARROW_KEYS if nav_key == "arrow" else _A11Y_TAB_KEYS
    if nav_key != "keys" and nav_direction is not None and nav_direction not in table:
        raise RuntimeError(
            f"a11y_dragdrop nav_direction for nav_key={nav_key!r} must be one of "
            f"{sorted(table)}, got {nav_direction!r}"
        )
    provided = [f for f in _A11Y_VERIFY_FIELDS if action.get(f)]
    if len(provided) != 1:
        raise RuntimeError(
            f"a11y_dragdrop requires exactly one verify_* field "
            f"({', '.join(_A11Y_VERIFY_FIELDS)}), got {len(provided)}"
        )


def _a11y_nav_keys(action: dict[str, Any]) -> list[str]:
    """The concrete key-press sequence, mirroring ``a11y_dragdrop._nav_keys``."""
    nav_key = action.get("nav_key", "tab")
    if nav_key == "keys":
        return list(action.get("nav_key_sequence") or [])
    nav_direction = action.get("nav_direction")
    if nav_key == "arrow":
        key = _A11Y_ARROW_KEYS[nav_direction or "down"]
    else:
        key = _A11Y_TAB_KEYS[nav_direction or "forward"]
    return [key] * int(action.get("max_nav_steps", 12))


async def _a11y_check_verify(state: dict[str, Any], action: dict[str, Any]) -> bool:
    """One evaluation of whichever verify_* field is set.

    Mirrors ``session/a11y_dragdrop.py``'s ``_check_verify`` dispatch order
    exactly: js, then selector-appears, then selector-gone, then text-contains
    as the unconditional fallback. Dispatch is by TRUTHINESS, matching the
    engine and the lint's arity count -- an ``is not None`` test here would
    take the ``verify_js`` branch on ``verify_js=""`` and evaluate the empty
    string while the author's real check went unrun. ``_a11y_validate`` has
    already guaranteed exactly one field is truthy by the time this runs, so
    the fallback is reached only with a real needle.
    """
    target = _target(state)
    if action.get("verify_js"):
        return bool(await target.evaluate(action["verify_js"]))
    if action.get("verify_selector_appears"):
        return await target.locator(action["verify_selector_appears"]).count() > 0
    if action.get("verify_selector_gone"):
        return await target.locator(action["verify_selector_gone"]).count() == 0
    return bool(
        await target.evaluate(
            "(needle) => document.body.innerText.includes(needle)", action.get("verify_text_contains")
        )
    )


async def _a11y_dragdrop(state: dict[str, Any], action: dict[str, Any]) -> None:
    """Keyboard (WAI-ARIA APG) drag-and-drop: grab, navigate, drop, verify, release.

    Mirrors ``session/a11y_dragdrop.run_a11y_dragdrop`` behaviourally, with
    none of its infrastructure -- no operation gate, no recorder, no
    telemetry, since this runs against a bare Playwright page/frame with no
    octowright session. The release-on-failure semantics are the part that
    must not drift: a grabbed-predicate returning False skips release outright
    (the key was pressed but the widget demonstrably never entered grab mode
    -- nothing to release), while a failed verify OR any exception during
    grab/navigate/drop/verify DOES release, matching the engine's own
    carve-out (a grab that succeeded followed by a drop that did not would
    otherwise leave the widget stuck in grab mode). ``focus()`` stays outside
    the protected region for the same reason it does in the engine: no key has
    been delivered yet.
    """
    _a11y_validate(action)
    target = _target(state)
    keyboard = _page(state).keyboard
    source = target.locator(action["source_selector"])
    release_key = action.get("release_key", "Escape")

    await source.focus()
    try:
        await keyboard.press(action.get("grab_key", "Space"))
        grabbed_js = action.get("grabbed_predicate_js") or _A11Y_DEFAULT_GRABBED_JS
        if not bool(await source.evaluate(grabbed_js)):
            return
        for key in _a11y_nav_keys(action):
            await keyboard.press(key)
        await keyboard.press(action.get("drop_key", "Space"))

        verify_timeout_ms = int(action.get("verify_timeout_ms", 2000))
        verify_poll_ms = int(action.get("verify_poll_ms", 100))
        deadline = time.monotonic() + verify_timeout_ms / 1000
        while True:
            if await _a11y_check_verify(state, action):
                return
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(verify_poll_ms / 1000)
        await keyboard.press(release_key)
    except Exception:
        try:
            await keyboard.press(release_key)
        except Exception:
            pass
        raise
'''
)
