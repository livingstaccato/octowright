# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``browser_capture_and_close``: one-shot capture-then-teardown tool.

Split out of ``inspect.py`` to keep that module under the repository's LOC
ceiling. The title/URL/screenshot/optional-ARIA capture runs as a
``preparation`` callback INSIDE the pool's close coordinator (Task 8,
``browser_pool.lifecycle.close_with_preparation``) rather than as ordinary
calls sandwiched before a separate ``pool.close()`` -- so a concurrent
navigation can never race the capture, a protected-browser refusal has ZERO
capture side effects (the protection preflight runs before the reservation
even exists), and an external closure that beats the preparation ticket
fails the whole call with ``SessionClosedError`` instead of handing back a
dict missing the promised fields.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from octowright._paths import reject_unsafe_path
from octowright.browser_pool.errors import ProtectedBrowserCloseError
from octowright.browser_pool.lifecycle import close_with_preparation
from octowright.defaults import RECORDINGS_DIR, SNAPSHOT_TIMEOUT_SECONDS
from octowright.mcp_types import BrowserCaptureAndCloseResult, BrowserToolAction
from octowright.server._state import mcp, pool
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.session import DEFAULT_PREVIEW_CHARS

if TYPE_CHECKING:
    from octowright.session import BrowserSession

# Module-level alias so tests can monkeypatch the snapshot timeout cheaply,
# matching ``inspect.SNAPSHOT_TIMEOUT_S``.
SNAPSHOT_TIMEOUT_S = SNAPSHOT_TIMEOUT_SECONDS


def _capture_snapshot_compact_actions(instance_id: str) -> list[BrowserToolAction]:
    return annotate_next_actions_for_profile(
        [
            {"tool": "browser_page_outline", "args": {"instance_id": instance_id}},
            {
                "tool": "browser_read_markdown",
                "args": {"instance_id": instance_id, "response_mode": "summary"},
            },
            {"tool": "browser_snapshot", "args": {"instance_id": instance_id, "selector": "main"}},
        ]
    )


def snapshot_timeout_fields(instance_id: str) -> dict[str, Any]:
    return {
        "snapshot_timed_out": True,
        "timeout_s": SNAPSHOT_TIMEOUT_S,
        "hint": (
            "aria snapshot timed out on a heavy DOM — use browser_page_outline, "
            "browser_read_markdown(response_mode='summary'), or browser_snapshot with "
            "a scoped selector (e.g. selector='main')"
        ),
        "actions": _capture_snapshot_compact_actions(instance_id),
    }


async def _capture_before_close(
    session: BrowserSession,
    *,
    instance_id: str,
    target: Path,
    snapshot: bool,
) -> dict[str, Any]:
    # Re-enters the coordinator's own task (exact-task reentrancy, Task 2):
    # the close ticket already owns the gate under this exact root operation
    # name, so this never queues -- it does NOT let a direct outside caller
    # bypass admission, since the observable root stays the reservation's
    # "browser_capture_and_close" name either way.
    async with session.operation("browser_capture_and_close"):
        title = await session.page.title()
        # url + aria follow the active frame (like browser_snapshot); the screenshot
        # stays page-level since it captures the rendered viewport, and title is page-only.
        frame_target = session._target()
        url = frame_target.url
        await session.screenshot(target)
        captured: dict[str, Any] = {"title": title, "url": url, "screenshot_path": str(target)}
        if snapshot:
            try:
                aria_full = await asyncio.wait_for(
                    frame_target.locator("html").aria_snapshot(),
                    timeout=SNAPSHOT_TIMEOUT_S,
                )
                captured["aria"] = aria_full[:DEFAULT_PREVIEW_CHARS]
            except TimeoutError:
                captured.update(snapshot_timeout_fields(instance_id))
        return captured


@mcp.tool(
    structured_output=False,
    description=(
        "ONE-SHOT TEARDOWN: Captures a screenshot and page title, then closes the browser. "
        "Use this as the final step of a task to ensure resources are freed. "
        "If snapshot=True, also includes an aria-tree snapshot. "
        "If the browser is protected, pass force=True to confirm before any capture side effects run. "
        "Returns {title, url, screenshot_path, aria (optional), closed: true}; protected refusal returns {error}."
    ),
)
async def browser_capture_and_close(
    instance_id: str,
    screenshot_path: str | None = None,
    snapshot: bool = True,
    force: bool = False,
) -> BrowserCaptureAndCloseResult:
    session = pool.get(instance_id)

    # Pure path parsing/containment -- no lease needed, so it runs before any
    # reservation and a rejected path never touches the browser.
    target = Path(screenshot_path) if screenshot_path else session.log_path.with_suffix(".png")
    target = reject_unsafe_path(target, RECORDINGS_DIR, label=f"screenshot_path {str(target)!r}")

    async def _prepare(prep_session: BrowserSession) -> dict[str, Any]:
        return await _capture_before_close(prep_session, instance_id=instance_id, target=target, snapshot=snapshot)

    try:
        # The protection check IS the reservation preflight (see
        # ``lifecycle.reserve_close_browser``): a protected refusal raises
        # here, before ``_prepare``/``_capture_before_close`` ever runs, so a
        # refused call has zero capture side effects.
        outcome = await close_with_preparation(
            pool,
            instance_id,
            force=force,
            reason="agent_close",
            operation_name="browser_capture_and_close",
            preparation=_prepare,
            expected_session=session,
        )
    except ProtectedBrowserCloseError as exc:
        return {"error": str(exc)}

    # A successful (non-raising) close_with_preparation means the coordinator
    # saw no error, so the preparation callback ran to completion and
    # ``prepared`` is always the full dict from ``_capture_before_close`` --
    # never a partial one (external closure racing the ticket instead fails
    # the call with SessionClosedError, never lands here).
    assert isinstance(outcome.prepared, dict), "capture-and-close preparation must return a dict on success"  # nosec B101
    captured = cast(dict[str, Any], outcome.prepared)
    return cast(BrowserCaptureAndCloseResult, {**captured, "closed": True})
