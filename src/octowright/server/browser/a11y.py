# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Accessibility-oriented browser tools (keyboard WAI-ARIA drag-and-drop)."""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.browser.views import _with_outline


@mcp.tool(
    description=(
        "Keyboard (WAI-ARIA APG) drag-and-drop: focus the source, press grab_key, "
        "send navigation keys, press drop_key, then poll a verify check. This is the "
        "ACCESSIBLE counterpart to browser_drag, which drives a synthetic mouse and "
        "cannot operate widgets that only implement the keyboard pattern. "
        "One atomic attempt per call -- it never retries and never switches navigation "
        "strategy; that is the caller's orchestration to own. "
        "Exactly one of verify_js / verify_selector_appears / verify_selector_gone / "
        "verify_text_contains is REQUIRED: with no check the call would report success "
        "without having confirmed anything. "
        "Returns a structured result on ordinary failure rather than raising, so the "
        "caller can read stage_reached ('failed_grab' | 'navigated' | 'dropped' | "
        "'verified' | 'failed_verify') and decide what to do. It raises only when the "
        "result would be meaningless (selector matches nothing, frame detached). "
        "If verification fails, release_key is pressed so the widget is not left stuck "
        "in grab mode. "
        "Pass response_mode='outline' to get a compact browser_page_outline in the same call."
    ),
)
async def browser_a11y_dragdrop(
    instance_id: str,
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
    response_mode: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_a11y_dragdrop") as session:
        result = await session.a11y_dragdrop(
            source_selector=source_selector,
            nav_key=nav_key,
            nav_direction=nav_direction,
            nav_key_sequence=nav_key_sequence,
            max_nav_steps=max_nav_steps,
            grab_key=grab_key,
            drop_key=drop_key,
            release_key=release_key,
            grabbed_predicate_js=grabbed_predicate_js,
            verify_js=verify_js,
            verify_selector_appears=verify_selector_appears,
            verify_selector_gone=verify_selector_gone,
            verify_text_contains=verify_text_contains,
            verify_timeout_ms=verify_timeout_ms,
            verify_poll_ms=verify_poll_ms,
        )
        return await _with_outline(instance_id, result, response_mode)
