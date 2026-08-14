# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The page-side binding the viewport pill talks to.

The pill's init script is added to the context ONCE and re-run on every
document, with the viewport values baked into its text at launch. That makes
the script a snapshot: anything changed afterwards by ``resize`` or
``viewport_sync`` is undone by the next navigation. The binding is the live
channel that fixes it, so what it reports has to be the session's current
state rather than another copy of the launch values.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool.pool import BrowserPool


async def _binding(session: Any) -> Any:
    """Extract the function the pool exposes to the page."""
    pool = BrowserPool()
    context = MagicMock()
    context.expose_binding = AsyncMock()
    await pool._expose_viewport_binding(context, session)
    name, function = context.expose_binding.await_args.args
    assert name == "__octowright_viewport_action"
    return function


def _session(**fields: Any) -> Any:
    session = MagicMock()
    for name, value in fields.items():
        setattr(session, name, value)
    return session


@pytest.mark.anyio
async def test_state_reports_what_the_session_is_now_not_what_it_launched_as() -> None:
    """A fluid session that was resized is fixed, and must say so.

    ``mismatch`` is only evaluated for fixed sessions, so a pill still showing
    "fluid" after a resize is a pill that cannot warn -- about drift the resize
    itself caused, since Playwright pins the viewport without moving the OS
    window.
    """
    session = _session(
        viewport_mode="fixed",
        viewport_width=1200,
        viewport_height=800,
        viewport_frame_inset_w=24,
        viewport_frame_inset_h=112,
    )

    state = await (await _binding(session))(None, {"action": "state"})

    assert state == {
        "mode": "fixed",
        "width": 1200,
        "height": 800,
        "inset_w": 24,
        "inset_h": 112,
    }


@pytest.mark.anyio
async def test_state_passes_an_unmeasured_inset_through_as_null() -> None:
    """The pill has to be able to tell "no chrome" from "chrome unknown"."""
    session = _session(
        viewport_mode="fluid",
        viewport_width=None,
        viewport_height=None,
        viewport_frame_inset_w=None,
        viewport_frame_inset_h=None,
    )

    state = await (await _binding(session))(None, {"action": "state"})

    assert state["inset_w"] is None
    assert state["inset_h"] is None


@pytest.mark.anyio
async def test_an_unknown_action_is_rejected() -> None:
    """The binding is reachable from page JavaScript; it takes a fixed menu."""
    with pytest.raises(ValueError, match="unknown viewport action"):
        await (await _binding(_session()))(None, {"action": "evaluate-this"})


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
