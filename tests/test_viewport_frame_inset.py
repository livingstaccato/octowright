# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The chrome measurement that makes a viewport warning possible.

``viewport_status`` can only tell drift from browser chrome if it knows how big
the chrome is. It learns that whenever Playwright has just WELDED the window to
the viewport -- at launch, and again after every ``set_viewport_size`` -- which
is the only kind of moment the number is unambiguous.

These tests cover the measurement itself. The verdicts it feeds live in
tests/test_session_ops_mixin_actions.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.viewport_ops import SessionViewportMixin
from tests._operation_gate_fakes import OperationAwareFake


class _ViewportFake(OperationAwareFake, SessionViewportMixin):
    """Real-gate fake so ``measure_frame_inset`` (``@gated_operation``) runs
    through an actual SessionOperationGate, not a bare mixin missing
    ``operation()``."""


def _session(**evaluate: Any) -> Any:
    session = _ViewportFake()
    session.page = MagicMock()
    session.page.evaluate = AsyncMock(**evaluate)
    session.viewport_frame_inset_w = None
    session.viewport_frame_inset_h = None
    return session


@pytest.mark.anyio
async def test_the_chrome_around_a_headed_window_is_recorded() -> None:
    """The numbers a real Linux/Wayland chromium reports: 8px of border, 85px of bar."""
    session = _session(return_value={"dw": 8, "dh": 85})

    await session.measure_frame_inset()

    assert session.viewport_frame_inset_w == 8
    assert session.viewport_frame_inset_h == 85


@pytest.mark.anyio
async def test_headless_records_a_real_zero_rather_than_an_unknown() -> None:
    """Headless has no window and no chrome, and that is a measurement, not a failure."""
    session = _session(return_value={"dw": 0, "dh": 0})

    await session.measure_frame_inset()

    assert session.viewport_frame_inset_w == 0
    assert session.viewport_frame_inset_h == 0


@pytest.mark.anyio
async def test_a_page_that_cannot_be_evaluated_leaves_the_inset_unknown() -> None:
    """A diagnostic measurement must never be able to fail a launch or a resize."""
    session = _session(side_effect=RuntimeError("execution context destroyed"))

    await session.measure_frame_inset()

    assert session.viewport_frame_inset_w is None
    assert session.viewport_frame_inset_h is None


@pytest.mark.anyio
async def test_a_negative_inset_is_rejected_and_the_previous_one_kept() -> None:
    """A window cannot be smaller than its own content area.

    This is not hypothetical. Resizing a session that launched FLUID produces
    exactly this reading, because Playwright does not move the OS window for a
    no_viewport context: the emulated viewport jumps to the requested size
    while the window stays where it was, so ``outer - inner`` goes negative.
    Measured: -255 x 380 after resizing a fluid session to 1200x800.

    Keeping the previous inset is the right answer -- the chrome did not
    change, only the viewport did -- and it is what lets viewport_status go on
    to report the genuine mismatch that the resize created.
    """
    session = _session(return_value={"dw": -255, "dh": 380})
    session.viewport_frame_inset_w = 24
    session.viewport_frame_inset_h = 112

    await session.measure_frame_inset()

    assert session.viewport_frame_inset_w == 24
    assert session.viewport_frame_inset_h == 112


@pytest.mark.anyio
async def test_an_explicit_page_overrides_the_sessions_own() -> None:
    """The launch pipeline measures against the page it has just created."""
    session = _session(return_value={"dw": 1, "dh": 1})
    other = MagicMock()
    other.evaluate = AsyncMock(return_value={"dw": 8, "dh": 85})

    await session.measure_frame_inset(other)

    assert (session.viewport_frame_inset_w, session.viewport_frame_inset_h) == (8, 85)
    session.page.evaluate.assert_not_awaited()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
