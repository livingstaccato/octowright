# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Input tools: click / type / fill / press_key + file uploads."""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.browser.inspect import browser_brief, browser_page_outline
from octowright.server.profiles import annotate_next_actions_for_profile

# Re-exported for backwards compatibility: callers that imported
# ``validate_upload_path`` from this module continue to work, but the
# canonical home is now ``octowright.session.upload_paths`` so the same
# allowlist applies whether the path comes in via an MCP tool or a macro
# replay that calls the session method directly.
from octowright.session import DEFAULT_PREVIEW_CHARS
from octowright.session.upload_paths import validate_upload_path  # noqa: F401


async def _with_outline(instance_id: str, result: dict[str, Any], response_mode: str | None) -> dict[str, Any]:
    if response_mode == "outline":
        result["outline"] = await browser_page_outline(instance_id)
    return result


def _truncate_text_value(text: str, *, max_chars: int | None, full: bool) -> dict[str, Any]:
    cap = None if full else (max_chars if max_chars is not None else DEFAULT_PREVIEW_CHARS)
    if cap is not None and cap < 0:
        raise ValueError("max_chars must be >= 0")
    if cap is not None and len(text) > cap:
        return {"text": text[:cap], "truncated": True, "text_size": len(text), "cap": cap}
    return {"text": text, "truncated": False, "text_size": len(text)}


def _get_text_by_full_action(
    instance_id: str,
    *,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    text: str | None = None,
    text_exact: bool = False,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> list[dict[str, Any]]:
    args: dict[str, Any] = {"instance_id": instance_id, "full": True}
    for key, value in (
        ("role", role),
        ("role_name", role_name),
        ("label", label),
        ("text", text),
        ("test_id", test_id),
        ("timeout_ms", timeout_ms),
    ):
        if value is not None:
            args[key] = value
    # Exact flags are only emitted when set, so the suggested follow-up call
    # stays as short as the original — but a set flag MUST survive, or the
    # retry would silently widen back to substring matching.
    for flag_name, flag in (("role_exact", role_exact), ("label_exact", label_exact), ("text_exact", text_exact)):
        if flag:
            args[flag_name] = True
    return annotate_next_actions_for_profile([{"tool": "browser_get_text_by", "args": args}])


@mcp.tool(
    structured_output=False,
    description=(
        "Click an element. Provide a CSS `selector` OR one ARIA locator "
        "(role / label / text / test_id) — not both.\n"
        "ARIA locators are preferred: they survive DOM restructuring because "
        "they describe intent, not structure. Use `selector` as a fallback "
        "for elements that have no accessible name.\n"
        "  role      — ARIA role ('button', 'link', 'checkbox', …); "
        "combine with role_name to target by accessible name.\n"
        "  label     — element associated with an <label> or aria-label.\n"
        "  text      — element whose visible text matches (SUBSTRING by default, so "
        "'Ada' also matches 'Ada Lovelace (old)'; pass text_exact=True for whole-string "
        "matching, and label_exact=True for the same on label. Both also make the match "
        "CASE-SENSITIVE, so text='submit' with text_exact=True does not match "
        "'Submit').\n"
        "  test_id   — element with a matching data-testid attribute.\n"
        "  selector  — CSS / XPath selector (fallback for ARIA-less elements).\n"
        "timeout_ms bounds the wait for the element, on BOTH the ARIA and selector "
        "paths (default 15000). Lower it when a click is expected to fail and you are "
        "probing — the default costs 15s per failed attempt.\n"
        "Set no_wait_after=True when the click intentionally starts a navigation whose "
        "load lifecycle is not expected to settle.\n"
        "Pass response_mode='outline' to get a compact browser_page_outline in the "
        "same call, or response_mode='brief' for the older aria-based brief snapshot."
    ),
)
async def browser_click(
    instance_id: str,
    selector: str | None = None,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    text: str | None = None,
    text_exact: bool = False,
    test_id: str | None = None,
    timeout_ms: int | None = None,
    no_wait_after: bool = False,
    response_mode: str | None = None,
) -> dict[str, Any]:
    if not (selector or role or label or text or test_id):
        raise ValueError("provide a selector or at least one ARIA locator (role/label/text/test_id)")
    async with browser_operation(pool, instance_id, "browser_click") as session:
        if role or label or text or test_id:
            await session.click_by(
                role=role,
                role_name=role_name,
                role_exact=role_exact,
                label=label,
                label_exact=label_exact,
                text=text,
                text_exact=text_exact,
                test_id=test_id,
                timeout_ms=timeout_ms,
                no_wait_after=no_wait_after,
            )
        elif selector:
            # Forward the timeout here too. It reached click_by above and was
            # dropped on this line, so an agent that set timeout_ms on a
            # selector click silently got the 15s default.
            await session.click(selector, timeout_ms=timeout_ms, no_wait_after=no_wait_after)
        else:
            raise ValueError("provide a selector or at least one ARIA locator (role/label/text/test_id)")
        res: dict[str, Any] = {"ok": True}
        if response_mode == "outline":
            res["outline"] = await browser_page_outline(instance_id)
        if response_mode == "brief":
            res["brief"] = await browser_brief(instance_id)
        return res


@mcp.tool(
    structured_output=False,
    description=(
        "Type text into an element ONE KEYSTROKE AT A TIME (with optional delay_ms). "
        "Use this when the page reacts to per-keystroke events (autocomplete, masked "
        "inputs, app-level keystroke handlers). For ordinary form fields prefer "
        "browser_fill — it's much faster because it sets the value in one shot. "
        "Don't use this to press a single non-text key like Enter or Escape — use browser_press_key. "
        "key_mode='keys' presses PHYSICAL keys with Shift genuinely held, which you MUST use for a "
        "canvas-based target — a KVM/BMC console (e.g. AMI H5Viewer), a canvas terminal, anything "
        "drawing its own text instead of using a real DOM input. Those read code+shiftKey rather than "
        "the key/text payload the default mode sends, so Shift is silently dropped and every shifted "
        "character arrives as its unshifted twin (TYPE=Ab*: becomes type=ab8;) with no error. "
        "key_mode='keys' assumes a US QWERTY layout and is slower (one round trip per character), so "
        "leave it off for ordinary DOM inputs, which the default mode types correctly. "
        "Pass response_mode='outline' to get a compact browser_page_outline in the same call."
    ),
)
async def browser_type(
    instance_id: str,
    selector: str,
    text: str,
    delay_ms: int | None = None,
    response_mode: str | None = None,
    key_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_type") as session:
        await session.type_text(selector, text, delay_ms, key_mode=key_mode)
        return await _with_outline(instance_id, {"ok": True}, response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Fill an <input> or <textarea>. Provide `value` plus a CSS `selector` OR "
        "one ARIA locator (role / label / test_id).\n"
        "ARIA locators are preferred — more resilient to DOM changes.\n"
        "  label    — element associated with a <label> or aria-label (matched as a "
        "SUBSTRING by default, so label='Email' also matches 'Email (optional)'; pass "
        "label_exact=True for whole-string matching, and role_exact=True for the same "
        "on role. Both also make the match CASE-SENSITIVE, so label='email' with "
        "label_exact=True does not match 'Email').\n"
        "  role     — ARIA role ('textbox', 'searchbox', …); "
        "combine with role_name for specificity.\n"
        "  test_id  — data-testid attribute.\n"
        "  selector — CSS / XPath selector (fallback).\n"
        "timeout_ms bounds the wait for the element, on BOTH the ARIA and selector "
        "paths (default 15000).\n"
        "USE THIS BY DEFAULT for form fields — ~10x faster than browser_type "
        "because it sets the value in one shot. Switch to browser_type only "
        "when the page needs per-keystroke events. Pass response_mode='outline' "
        "to get a compact browser_page_outline in the same call."
    ),
)
async def browser_fill(
    instance_id: str,
    value: str,
    selector: str | None = None,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    test_id: str | None = None,
    timeout_ms: int | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    if not (selector or role or label or test_id):
        raise ValueError("provide a selector or at least one ARIA locator (role/label/test_id)")
    async with browser_operation(pool, instance_id, "browser_fill") as session:
        if role or label or test_id:
            await session.fill_by(
                value,
                role=role,
                role_name=role_name,
                role_exact=role_exact,
                label=label,
                label_exact=label_exact,
                test_id=test_id,
                timeout_ms=timeout_ms,
            )
        elif selector:
            await session.fill(selector, value, timeout_ms=timeout_ms)
        else:
            raise ValueError("provide a selector or at least one ARIA locator (role/label/test_id)")
        res: dict[str, Any] = {"ok": True}
        if response_mode == "outline":
            res["outline"] = await browser_page_outline(instance_id)
        return res


@mcp.tool(
    structured_output=False,
    description=(
        "Press a single keyboard key on an instance. `key` uses Playwright key names: "
        "'Enter', 'Tab', 'Escape', 'ArrowDown', 'Control+a', 'Meta+v', etc. "
        "Use this for submit-via-Enter, keyboard shortcuts, modifiers. "
        "Do NOT use this to enter text — that's browser_fill or browser_type. "
        "Pass response_mode='outline' to get a compact browser_page_outline in the same call."
    ),
)
async def browser_press_key(instance_id: str, key: str, response_mode: str | None = None) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_press_key") as session:
        await session.press_key(key)
        res: dict[str, Any] = {"ok": True}
        if response_mode == "outline":
            res["outline"] = await browser_page_outline(instance_id)
        return res


@mcp.tool(
    structured_output=False,
    description=(
        "Read the inner text of an element matched by role, label, text, or data-testid. "
        "Useful for assertions that need a value rather than just a boolean match. "
        "Returned text is capped by default to bound token cost; pass max_chars=N "
        "for a custom cap or full=True to disable truncation."
    ),
)
async def browser_get_text_by(
    instance_id: str,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    label_exact: bool = False,
    text: str | None = None,
    text_exact: bool = False,
    test_id: str | None = None,
    timeout_ms: int | None = None,
    max_chars: int | None = None,
    full: bool = False,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_get_text_by") as session:
        result = await session.get_text_by(
            role=role,
            role_name=role_name,
            role_exact=role_exact,
            label=label,
            label_exact=label_exact,
            text=text,
            text_exact=text_exact,
            test_id=test_id,
            timeout_ms=timeout_ms,
        )
    out = _truncate_text_value(str(result.get("text") or ""), max_chars=max_chars, full=full)
    if out["truncated"]:
        out["next_actions"] = _get_text_by_full_action(
            instance_id,
            role=role,
            role_name=role_name,
            role_exact=role_exact,
            label=label,
            label_exact=label_exact,
            text=text,
            text_exact=text_exact,
            test_id=test_id,
            timeout_ms=timeout_ms,
        )
    return out


@mcp.tool(
    structured_output=False,
    description=(
        "Upload one or more files into an <input type=file> element. `paths` is a list "
        "of absolute file paths on this machine. Pass response_mode='outline' to get a "
        "compact browser_page_outline in the same call."
    ),
)
async def browser_set_input_files(
    instance_id: str,
    selector: str,
    paths: list[str],
    response_mode: str | None = None,
) -> dict[str, Any]:
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of file paths")
    async with browser_operation(pool, instance_id, "browser_set_input_files") as session:
        result = await session.set_input_files(selector, paths)
        return await _with_outline(instance_id, dict(result), response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Hover the mouse over an element by CSS selector. Use this to trigger hover-reveal "
        "menus, tooltips, CSS :hover states, or any interaction that requires the cursor "
        "to be positioned over an element without clicking. For clicking, use browser_click. "
        "Pass response_mode='outline' to get a compact browser_page_outline in the same call."
    ),
)
async def browser_hover(instance_id: str, selector: str, response_mode: str | None = None) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_hover") as session:
        await session.hover(selector)
        return await _with_outline(instance_id, {"ok": True}, response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Select one option in a <select> dropdown by value, visible label text, or 0-based index. "
        "Provide exactly one of: value (the option's `value` attribute), label (the option's visible "
        "text), or index (0-based position). Returns the list of selected option values. "
        "For custom dropdown widgets that are NOT a native <select>, use browser_click instead. "
        "Pass response_mode='outline' to get a compact browser_page_outline in the same call."
    ),
)
async def browser_select_option(
    instance_id: str,
    selector: str,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_select_option") as session:
        result = await session.select_option(selector, value=value, label=label, index=index)
        return await _with_outline(instance_id, dict(result), response_mode)


@mcp.tool(
    structured_output=False,
    description=(
        "Drag an element from source_selector and drop it onto target_selector. "
        "Uses Playwright's drag_and_drop, which drives a synthetic mouse-down → move → up "
        "sequence. Works for most drag-and-drop UIs that respond to mouse events — sortable "
        "lists, Kanban boards backed by mouse handlers, custom resize handles, etc. "
        "DOES NOT fire HTML5 native drag events (dragstart / dragover / drop) on "
        'draggable="true" elements — that\'s a Playwright limitation that affects '
        "headed and headless equally, not an octowright one. If the target relies on "
        "the HTML5 DnD API (e.g. Trello, react-dnd default backend, native-DnD demos), "
        "this tool will appear to succeed but no drop will register. Workaround: use "
        "browser_evaluate to dispatch DragEvent('dragstart'/'dragover'/'drop') manually "
        "with a synthetic DataTransfer. Both selectors must match exactly one visible element. "
        "Pass response_mode='outline' to get a compact browser_page_outline in the same call."
    ),
)
async def browser_drag(
    instance_id: str,
    source_selector: str,
    target_selector: str,
    response_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_drag") as session:
        await session.drag(source_selector, target_selector)
        return await _with_outline(instance_id, {"ok": True}, response_mode)
