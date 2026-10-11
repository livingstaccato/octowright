# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported CLI's per-action dispatch, as data rather than as a literal chain.

Carrying the ``if/elif kind == ...`` chain inline in ``script_export``'s
template string means branches get added by hand as gaps are noticed, which is
how such a chain once covered 13 of the 29 kinds ``macros.runtime._ACTION_MAP``
then held, while ending in ``raise RuntimeError("unsupported macro action in exported
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
kept in the generated script's ``state`` dict — see ``STATE_HELPERS`` in
``script_export_helpers``.
"""

from __future__ import annotations

from typing import Any

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
check = _credential_check(state, index, action, trusted)
if check is None:
    await _target(state).fill(action["selector"], action.get("value", ""))
else:
    await _credential_input(
        action,
        checked_fill(
            _ScriptSession(state),
            _target(state).locator(action["selector"]).first,
            action.get("value", ""),
            check,
            action.get("timeout_ms") or _PLAYWRIGHT_DEFAULT_TIMEOUT_MS,
        ),
    )
executed += 1
""",
    "type": """
check = _credential_check(state, index, action, trusted)
if check is None:
    await _target(state).type(action["selector"], action.get("text", ""), delay=action.get("delay_ms") or 0)
else:
    await _credential_input(
        action,
        checked_type(
            _ScriptSession(state),
            _target(state).locator(action["selector"]).first,
            action.get("text", ""),
            check,
            delay_ms=action.get("delay_ms"),
            timeout_ms=_PLAYWRIGHT_DEFAULT_TIMEOUT_MS,
        ),
    )
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
    # ``equals`` null is the truthy check, as the session records and replays
    # it (``equals is not None``); keying on the key's presence demanded that a
    # recorded truthy check return null. Python ``==``, so True == 1 as live.
    "expect_js": """
result = await _target(state).evaluate(action["expression"])
if action.get("equals") is not None and result != action["equals"]:
    raise RuntimeError(f"JS assertion failed: expected {action['equals']!r}, got {result!r}")
if action.get("equals") is None and not result:
    raise RuntimeError(f"JS assertion failed: got {result!r}")
executed += 1
""",
    # The whole script is one run, so since="run" counts from zero.
    "expect_network_clean": """
require_settled = strict_option(kind, action.get("require_settled", False))
window = state["network"].window(action.get("since", "run"))
settle = action.get("settle_timeout_ms")
settle = NETWORK_SETTLE_TIMEOUT_MS if settle is None else int(settle)
in_flight = await settle_network(state["network"].pending, settle) if settle > 0 else state["network"].pending()
observation = {**state["network"].judge(window, bool(action.get("http_errors")), in_flight), "in_flight": in_flight}
untracked = state["network"].since(window)[3]
if untracked > 0:
    observation["in_flight_untracked"] = untracked
refusal = strict_refusal(kind, observation, required=require_settled)
if refusal is not None:
    raise RuntimeError(refusal)
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
require_match = strict_option(kind, action.get("require_match", False))
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
refusal = strict_refusal(kind, {**summary, "selector": selector}, required=require_match)
if refusal is not None:
    raise RuntimeError(refusal)
_report_assertion(state, index, kind, {**summary, "selector": selector})
executed += 1
""",
    "click_by": """
await _locator(_target(state), action).click(timeout=action.get("timeout_ms"))
executed += 1
""",
    "fill_by": """
check = _credential_check(state, index, action, trusted)
if check is None:
    await _locator(_target(state), action).fill(action.get("value", ""), timeout=action.get("timeout_ms"))
else:
    await _credential_input(
        action,
        checked_fill(
            _ScriptSession(state),
            _locator(_target(state), action),
            action.get("value", ""),
            check,
            action.get("timeout_ms") or _PLAYWRIGHT_DEFAULT_TIMEOUT_MS,
        ),
    )
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
_refuse_redirected_credential_headers(action, headers)


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


#: Kinds the generated script skips rather than dispatches, as replay does.
EXPORT_SKIPPED = frozenset({"launch", "close", "snapshot"})


def unrunnable_steps(actions: list[Any]) -> list[tuple[int, Any]]:
    """``(index, kind)`` of each top-level step the generated script would
    reach and fail on with ``EXPORT_UNSUPPORTED``.

    Top level only, because the script walks only the top level: a container
    step (``if_selector``, ``try``, ``try_each``, ``macro_call``) is itself the
    unrunnable one, whatever it holds.
    """
    runnable = exported_action_kinds() | EXPORT_SKIPPED
    found: list[tuple[int, Any]] = []
    for index, action in enumerate(actions):
        kind = action.get("action") if isinstance(action, dict) else None
        if kind not in runnable:
            found.append((index, kind))
    return found


def refuse_unrunnable_steps(name: str, macro: dict[str, Any]) -> None:
    """Refuse to export a macro with a step the script cannot dispatch: the
    script would fail on it only after running every step before it against
    the target."""
    actions = macro.get("actions")
    found = unrunnable_steps(actions if isinstance(actions, list) else [])
    if found:
        where = ", ".join(f"step {index} ({kind})" for index, kind in found)
        raise ValueError(f"macro {name!r} cannot be exported: the script cannot run {where}")


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
