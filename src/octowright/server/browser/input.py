# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Input tools: click / type / fill / press_key + ARIA-locator variants + file uploads."""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp, pool
from octowright.server.browser.inspect import browser_brief

# Re-exported for backwards compatibility: callers that imported
# ``validate_upload_path`` from this module continue to work, but the
# canonical home is now ``octowright.session.upload_paths`` so the same
# allowlist applies whether the path comes in via an MCP tool or a macro
# replay that calls the session method directly.
from octowright.session.upload_paths import validate_upload_path  # noqa: F401


@mcp.tool(
    structured_output=False,
    description=(
        "Click an element by CSS selector. Use this for buttons, links, checkboxes, "
        "and any clickable element. Prefer browser_click_by when you have an aria role, "
        "label, or data-testid (it's more resilient to DOM changes). "
        "Don't use this to enter text — use browser_fill (instant) or browser_type (per-keystroke)."
    ),
)
async def browser_click(instance_id: str, selector: str, response_mode: str | None = None) -> dict[str, Any]:
    session = pool.get(instance_id)
    await session.click(selector)
    res: dict[str, Any] = {"ok": True}
    if response_mode == "brief":
        res["brief"] = await browser_brief(instance_id)
    return res


@mcp.tool(
    structured_output=False,
    description=(
        "Type text into a selector ONE KEYSTROKE AT A TIME (with optional delay_ms). "
        "Use this when the page reacts to per-keystroke events (autocomplete, masked "
        "inputs, app-level keystroke handlers). For ordinary form fields prefer "
        "browser_fill — it's much faster because it sets the value in one shot. "
        "Don't use this to press a single non-text key like Enter or Escape — use browser_press_key."
    ),
)
async def browser_type(instance_id: str, selector: str, text: str, delay_ms: int | None = None) -> dict[str, Any]:
    await pool.get(instance_id).type_text(selector, text, delay_ms)
    return {"ok": True}


@mcp.tool(
    structured_output=False,
    description=(
        "Fill an <input> or <textarea> by setting its value in one shot. "
        "USE THIS BY DEFAULT for form fields — it's ~10x faster than browser_type and "
        "fires a synthetic input event. Switch to browser_type only when the page needs "
        "per-keystroke events (autocomplete, masked inputs)."
    ),
)
async def browser_fill(instance_id: str, selector: str, value: str) -> dict[str, Any]:
    await pool.get(instance_id).fill(selector, value)
    return {"ok": True}


@mcp.tool(
    structured_output=False,
    description=(
        "Press a single keyboard key on an instance. `key` uses Playwright key names: "
        "'Enter', 'Tab', 'Escape', 'ArrowDown', 'Control+a', 'Meta+v', etc. "
        "Use this for submit-via-Enter, keyboard shortcuts, modifiers. "
        "Do NOT use this to enter text — that's browser_fill or browser_type."
    ),
)
async def browser_press_key(instance_id: str, key: str) -> dict[str, Any]:
    await pool.get(instance_id).press_key(key)
    return {"ok": True}


@mcp.tool(
    structured_output=False,
    description=(
        "Click an element matched by an ARIA role, label, visible text, or data-testid. "
        "More resilient than CSS selectors. Provide exactly one of role/label/text/test_id. "
        "When role is used, role_name narrows to an accessible name (e.g. 'Submit')."
    ),
)
async def browser_click_by(
    instance_id: str,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).click_by(
        role=role,
        role_name=role_name,
        role_exact=role_exact,
        label=label,
        text=text,
        test_id=test_id,
        timeout_ms=timeout_ms,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Fill an input matched by ARIA role, label, or data-testid. Provide value plus "
        "exactly one of role/label/test_id."
    ),
)
async def browser_fill_by(
    instance_id: str,
    value: str,
    role: str | None = None,
    role_name: str | None = None,
    label: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).fill_by(
        value,
        role=role,
        role_name=role_name,
        label=label,
        test_id=test_id,
        timeout_ms=timeout_ms,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Read the inner text of an element matched by role, label, text, or data-testid. "
        "Useful for assertions that need a value rather than just a boolean match."
    ),
)
async def browser_get_text_by(
    instance_id: str,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).get_text_by(
        role=role,
        role_name=role_name,
        role_exact=role_exact,
        label=label,
        text=text,
        test_id=test_id,
        timeout_ms=timeout_ms,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Upload one or more files into an <input type=file> element. `paths` is a list "
        "of absolute file paths on this machine."
    ),
)
async def browser_set_input_files(
    instance_id: str,
    selector: str,
    paths: list[str],
) -> dict[str, Any]:
    # Tool-level shape check stays here; per-path allowlist validation
    # now lives in BrowserSession.set_input_files so macro replay can't
    # bypass it. See octowright.session.upload_paths.validate_upload_path.
    if not isinstance(paths, list) or not paths:
        raise ValueError("paths must be a non-empty list of file paths")
    return await pool.get(instance_id).set_input_files(selector, paths)


@mcp.tool(
    structured_output=False,
    description=(
        "Hover the mouse over an element by CSS selector. Use this to trigger hover-reveal "
        "menus, tooltips, CSS :hover states, or any interaction that requires the cursor "
        "to be positioned over an element without clicking. For clicking, use browser_click."
    ),
)
async def browser_hover(instance_id: str, selector: str) -> dict[str, Any]:
    await pool.get(instance_id).hover(selector)
    return {"ok": True}


@mcp.tool(
    structured_output=False,
    description=(
        "Select one option in a <select> dropdown by value, visible label text, or 0-based index. "
        "Provide exactly one of: value (the option's `value` attribute), label (the option's visible "
        "text), or index (0-based position). Returns the list of selected option values. "
        "For custom dropdown widgets that are NOT a native <select>, use browser_click instead."
    ),
)
async def browser_select_option(
    instance_id: str,
    selector: str,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    return await pool.get(instance_id).select_option(selector, value=value, label=label, index=index)


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
        "with a synthetic DataTransfer. Both selectors must match exactly one visible element."
    ),
)
async def browser_drag(
    instance_id: str,
    source_selector: str,
    target_selector: str,
) -> dict[str, Any]:
    await pool.get(instance_id).drag(source_selector, target_selector)
    return {"ok": True}
