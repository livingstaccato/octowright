# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The exported CLI's per-action dispatch, as data rather than as a literal chain.

``script_export`` used to carry the ``if/elif kind == ...`` chain inline in its
template string. Branches were then added by hand as gaps were noticed, which
is how the chain came to cover 13 of ``macros.runtime._ACTION_MAP``'s 29 kinds
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

#: Runtime helpers the dispatch bodies below call. Rendered into the exported
#: script once, above the action loop.
STATE_HELPERS = '''
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
'''

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
    "set_input_files": """
await _target(state).set_input_files(action["selector"], action.get("paths") or action.get("files") or [])
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
index = int(action["index"])
if not 0 <= index < len(state["pages"]):
    raise RuntimeError(f"no page at index {index}")
await state["pages"].pop(index).close()
if not state["pages"]:
    raise RuntimeError("closed the last remaining page")
state["index"] = min(state["index"], len(state["pages"]) - 1)
state["frame"] = None
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
