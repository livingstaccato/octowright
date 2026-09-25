# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported CLI's per-action dispatch, as data rather than as a literal chain.

Carrying the ``if/elif kind == ...`` chain inline in ``script_export``'s
template string means branches get added by hand as gaps are noticed, which is
how such a chain ends up covering 13 of ``macros.runtime._ACTION_MAP``'s 29 kinds
while ending in ``raise RuntimeError("unsupported macro action in exported
CLI")`` — so exporting a macro containing an ordinary ``hover``, ``evaluate`` or
``screenshot`` produced a script that aborted on it.

Holding the branches in a dict keyed by action kind makes the coverage
question answerable (``exported_action_kinds()`` vs ``_ACTION_MAP``) instead of
requiring someone to notice. A test asserts the two sets are equal, so adding a
dispatchable action without an export branch fails in CI rather than in a
user's generated script.

The bodies below are the source of the GENERATED script, not of this module:
they run inside the exported file against a plain Playwright ``page``, with no
octowright session, pool or recorder. Where a session method means something
pool-shaped (extra tabs, an active iframe, a dialog policy), the equivalent is
kept in the generated script's ``state`` dict — see ``STATE_HELPERS``.
"""

from __future__ import annotations

import inspect

from octowright import request_failures
from octowright.assertion_warnings import assertion_warning
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
    )
    sources += (assertion_warning,)
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

#: ``kind -> dispatch body``. Each body runs with ``action`` and ``state`` in
#: scope and is responsible for its own ``executed``/``skipped`` bookkeeping.
EXPORT_DISPATCH: dict[str, str] = {
    "navigate": """
await _page(state).goto(action["url"])
executed += 1
""",
    "click": """
await _target(state).click(action["selector"])
executed += 1
""",
    "fill": """
await _target(state).fill(action["selector"], action.get("value", ""))
executed += 1
""",
    "type": """
await _target(state).type(action["selector"], action.get("text", ""), delay=action.get("delay_ms") or 0)
executed += 1
""",
    "press_key": """
await _page(state).keyboard.press(action["key"])
executed += 1
""",
    "wait_for": """
if action.get("selector"):
    await _target(state).wait_for_selector(action["selector"], timeout=action.get("timeout_ms"))
elif action.get("text"):
    await _target(state).wait_for_function(
        "text => document.body && document.body.innerText.includes(text)",
        arg=action["text"],
        timeout=action.get("timeout_ms"),
    )
else:
    await _page(state).wait_for_load_state("networkidle", timeout=action.get("timeout_ms"))
executed += 1
""",
    "expect_url": """
pattern = action["pattern"]
mode = action.get("mode", "regex")
actual = _page(state).url
if mode == "equals" and actual != pattern:
    raise RuntimeError(f"URL mismatch: expected {pattern!r}, got {actual!r}")
if mode == "contains" and pattern not in actual:
    raise RuntimeError(f"URL mismatch: expected substring {pattern!r}, got {actual!r}")
if mode == "regex" and re.search(pattern, actual) is None:
    raise RuntimeError(f"URL mismatch: expected pattern {pattern!r}, got {actual!r}")
executed += 1
""",
    "expect_selector": """
present = bool(action.get("present", True))
if present:
    await _target(state).wait_for_selector(action["selector"], timeout=action.get("timeout_ms"))
elif await _target(state).query_selector(action["selector"]) is not None:
    raise RuntimeError(f"selector should be absent: {action['selector']!r}")
executed += 1
""",
    "expect_text": """
element = await _target(state).wait_for_selector(action["selector"], timeout=action.get("timeout_ms"))
actual = await element.inner_text()
expected = action["text"]
mode = action.get("mode", "contains")
if mode == "equals" and actual != expected:
    raise RuntimeError(f"text mismatch: expected {expected!r}, got {actual!r}")
if mode == "contains" and expected not in actual:
    raise RuntimeError(f"text mismatch: expected substring {expected!r}, got {actual!r}")
if mode == "regex" and re.search(expected, actual) is None:
    raise RuntimeError(f"text mismatch: expected pattern {expected!r}, got {actual!r}")
executed += 1
""",
    "expect_js": """
result = await _target(state).evaluate(action["expression"])
if "equals" in action and result != action["equals"]:
    raise RuntimeError(f"JS assertion failed: expected {action['equals']!r}, got {result!r}")
if "equals" not in action and not result:
    raise RuntimeError(f"JS assertion failed: got {result!r}")
executed += 1
""",
    # The whole script is one run, so since="run" counts from zero.
    "expect_network_clean": """
window = state["network"].window(action.get("since", "run"))
settle = action.get("settle_timeout_ms")
settle = NETWORK_SETTLE_TIMEOUT_MS if settle is None else int(settle)
in_flight = await settle_network(state["network"].pending, settle) if settle > 0 else state["network"].pending()
observation = {**state["network"].judge(window, bool(action.get("http_errors"))), "in_flight": in_flight}
untracked = state["network"].since(window)[3]
if untracked > 0:
    observation["in_flight_untracked"] = untracked
_report_assertion(state, index, kind, observation)
executed += 1
""",
    "mark_network_clean": """
state["network"].mark()
executed += 1
""",
    # The same drawn_text functions replay calls, rendered verbatim above the
    # loop (see script_export), so every rule and message is one copy. Replay
    # also reads Chromium's DOM snapshot; the export has no CDP session, so a
    # closed shadow root is not checked here.
    "expect_no_text": """
forbidden = action["text"]
check_forbidden_text(forbidden)
selector = action.get("selector", "body")
limit = resolve_element_limit(action.get("element_limit"), os.environ)
target = _target(state)
summary = new_scan_summary()
for position, scanned in enumerate(target.frames if selector == "body" and state["frame"] is None else [target]):
    try:
        found = await scanned.evaluate(COLLECT_RENDERED_TEXT_JS, collect_args(selector, limit))
    except Exception as exc:
        if not skip_gone_frame(summary, position, scanned.is_detached(), exc):
            raise
        continue
    fold_frame_result(summary, position, found, forbidden, selector)
if summary["truncated"]:
    raise RuntimeError(truncation_message(selector, limit))
_report_assertion(state, index, kind, {**summary, "selector": selector})
executed += 1
""",
    "click_by": """
await _locator(_target(state), action).click(timeout=action.get("timeout_ms"))
executed += 1
""",
    "fill_by": """
await _locator(_target(state), action).fill(action.get("value", ""), timeout=action.get("timeout_ms"))
executed += 1
""",
    "get_text_by": """
await _locator(_target(state), action).inner_text()
executed += 1
""",
    "evaluate": """
await _target(state).evaluate(action["expression"])
executed += 1
""",
    # Mirrors macros.runtime._dispatch_standard: a screenshot with no path is a
    # skip, not a failure — the recorder writes the row before the path is known.
    "screenshot": """
if not action.get("path"):
    skipped += 1
elif _sensitive_arg_values(args):
    raise RuntimeError(
        "classified macro screenshot requires a composition-owned privacy handler; "
        "standalone exports do not provide one"
    )
else:
    await _page(state).screenshot(path=action["path"])
    executed += 1
""",
    "hover": """
await _target(state).hover(action["selector"])
executed += 1
""",
    "select_option": """
options = {k: action[k] for k in ("value", "label", "index") if action.get(k) is not None}
await _target(state).select_option(action["selector"], **options)
executed += 1
""",
    # The recorder writes source/target; the session parameters are
    # source_selector/target_selector (macros.runtime._REPLAY_RENAME_KEYS), so
    # accept either spelling here rather than depending on which side wrote it.
    "drag": """
source = action.get("source") or action["source_selector"]
target = action.get("target") or action["target_selector"]
await _target(state).drag_and_drop(source, target)
executed += 1
""",
    # `_a11y_dragdrop` (STATE_HELPERS) always returns normally on an ordinary
    # failed grab/verify -- it never raises for those, matching how the live
    # session method never raises for them either (macros.runtime._dispatch_standard
    # just awaits the call and counts it as executed). Only a genuine exception
    # (e.g. a selector that never resolves) propagates and aborts the script.
    "a11y_dragdrop": """
await _a11y_dragdrop(state, action)
executed += 1
""",
    "set_input_files": """
# The live upload allowlist (session.upload_paths), resolved paths and all.
await _target(state).set_input_files(action["selector"], _upload_paths(action.get("paths") or action.get("files")))
executed += 1
""",
    "upload_files": """
# Checked before the chooser opens, as live upload_files does.
paths = _upload_paths(action["paths"])
target = _target(state)
trigger = target.locator(action["selector"]) if action.get("selector") is not None else _locator(target, action)
timeout = action.get("timeout_ms")
if timeout is None or timeout == 0:
    timeout = _DEFAULT_ACTION_TIMEOUT_MS
async with _page(state).expect_file_chooser(timeout=timeout) as chooser_info:
    await trigger.click(timeout=timeout)
chooser = await chooser_info.value
await chooser.set_files(paths, timeout=timeout)
executed += 1
""",
    "resize": """
await _page(state).set_viewport_size({"width": int(action["width"]), "height": int(action["height"])})
executed += 1
""",
    "navigate_back": """
await _page(state).go_back()
executed += 1
""",
    "open_url": """
new_page = await _page(state).context.new_page()
# Watched before the load, or a failure during the tab's first page load is
# missed; a no-op when the context's page event already did it.
_watch_network(state, new_page)
await new_page.goto(action["url"])
state["pages"].append(new_page)
if state["dialog_policy"] != "manual":
    _install_dialog_policy(state)
executed += 1
""",
    "switch_page": """
index = int(action["index"])
if not 0 <= index < len(state["pages"]):
    raise RuntimeError(f"no page at index {index}")
state["index"] = index
state["frame"] = None
executed += 1
""",
    "close_page": """
if len(state["pages"]) <= 1:
    raise RuntimeError("cannot close the last remaining page")
index = int(action["index"]) if action.get("index") is not None else state["index"]
if not 0 <= index < len(state["pages"]):
    raise RuntimeError(f"no page at index {index}")
was_active = index == state["index"]
await state["pages"].pop(index).close()
if was_active:
    state["index"] = 0
    state["frame"] = None
elif index < state["index"]:
    state["index"] -= 1
executed += 1
""",
    "switch_frame": """
await _switch_frame(state, action)
executed += 1
""",
    "reset_frame": """
state["frame"] = None
executed += 1
""",
    # The recorder writes the field as `pattern`; the session parameter is
    # `url_pattern`. Both spellings reach an exported script, so accept both.
    "mock_route": """
pattern = action.get("pattern") or action["url_pattern"]
handler = _mock_handler(action)
if pattern in state["routes"]:
    await _page(state).unroute(pattern, state["routes"][pattern])
await _page(state).route(pattern, handler)
state["routes"][pattern] = handler
executed += 1
""",
    "unmock_route": """
pattern = action.get("pattern") or action["url_pattern"]
handler = state["routes"].pop(pattern, None)
if handler is None:
    raise RuntimeError(f"no active mock for pattern {pattern!r}")
await _page(state).unroute(pattern, handler)
executed += 1
""",
    # Per-endpoint injection, on the CONTEXT so it follows popups and pages
    # opened later -- matching the live session method. A page route dies at the
    # page boundary, so the exported script would silently drop the header on
    # exactly the popup traffic the live run covered.
    # A fulfilling mock still ends the chain, and mock_route is a PAGE route,
    # which takes precedence over a context one -- so a mock on an overlapping
    # pattern suppresses this injector.
    "inject_headers": """
pattern = action.get("pattern") or action["url_pattern"]
headers = action["headers"]
scrubbed = sorted(k for k, v in headers.items() if v == "<redacted:header>")
if scrubbed:
    raise RuntimeError(
        f"header(s) {', '.join(scrubbed)} hold the recorder's redaction placeholder; "
        "parameterize the macro and pass the real value at run time"
    )


def _make_header_injector(extra):
    async def _inject(route):
        await route.fallback(headers={**route.request.headers, **extra})

    return _inject


handler = _make_header_injector(dict(headers))
if pattern in state["header_routes"]:
    await _page(state).context.unroute(pattern, state["header_routes"][pattern])
await _page(state).context.route(pattern, handler)
state["header_routes"][pattern] = handler
executed += 1
""",
    "uninject_headers": """
pattern = action.get("pattern") or action["url_pattern"]
handler = state["header_routes"].pop(pattern, None)
if handler is None:
    raise RuntimeError(f"no active header injection for pattern {pattern!r}")
await _page(state).context.unroute(pattern, handler)
executed += 1
""",
    # Page-level, matching the session method: a popup opened later does not
    # inherit these. A value the recorder scrubbed is refused rather than sent,
    # since it would authenticate as nobody and surface as a puzzling 401.
    "set_extra_http_headers": """
headers = action["headers"]
scrubbed = sorted(k for k, v in headers.items() if v == "<redacted:header>")
if scrubbed:
    raise RuntimeError(
        f"header(s) {', '.join(scrubbed)} hold the recorder's redaction placeholder; "
        "parameterize the macro and pass the real value at run time"
    )
await _page(state).set_extra_http_headers(dict(headers))
executed += 1
""",
    "set_dialog_policy": """
policy = action["policy"]
if policy not in ("accept", "dismiss", "manual"):
    raise RuntimeError(f"policy must be accept|dismiss|manual, got {policy!r}")
state["dialog_policy"] = policy
state["dialog_prompt_text"] = action.get("prompt_text")
_install_dialog_policy(state)
executed += 1
""",
}

#: The fall-through. Kept as a constant so a test can assert the chain still
#: fails loudly on a kind it genuinely cannot run.
EXPORT_UNSUPPORTED = 'raise RuntimeError(f"unsupported macro action in exported CLI: {kind!r}")'


def exported_action_kinds() -> frozenset[str]:
    """Action kinds the generated script can dispatch."""
    return frozenset(EXPORT_DISPATCH)


def _indent_block(body: str, indent: str) -> str:
    return "\n".join(f"{indent}{line}" if line.strip() else "" for line in body.strip("\n").splitlines())


def render_dispatch_chain(indent: str) -> str:
    """The ``elif`` chain, at *indent*, ending in the unsupported-kind raise.

    The caller emits the leading ``if kind in _LIFECYCLE_SKIP:`` branch, so
    every branch here is an ``elif``.
    """
    parts: list[str] = []
    for kind, body in EXPORT_DISPATCH.items():
        parts.append(f'{indent}elif kind == "{kind}":')
        parts.append(_indent_block(body, indent + "    "))
    parts.append(f"{indent}else:")
    parts.append(f"{indent}    {EXPORT_UNSUPPORTED}")
    return "\n".join(parts)
