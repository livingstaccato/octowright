# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Input tools: click / type / fill / press_key + ARIA-locator variants + file uploads."""

from __future__ import annotations

from typing import Any

from .._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Click an element by CSS selector. Use this for buttons, links, checkboxes, "
        "and any clickable element. Prefer browser_click_by when you have an aria role, "
        "label, or data-testid (it's more resilient to DOM changes). "
        "Don't use this to enter text — use browser_fill (instant) or browser_type (per-keystroke)."
    ),
)
async def browser_click(instance_id: str, selector: str) -> dict[str, Any]:
    await pool.get(instance_id).click(selector)
    return {"ok": True}


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
    return await pool.get(instance_id).set_input_files(selector, paths)
