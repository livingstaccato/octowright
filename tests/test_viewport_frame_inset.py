# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The launch-time chrome measurement that makes a viewport warning possible.

``viewport_status`` can only tell drift from browser chrome if it knows how
big the chrome is. It learns that once, at launch, because that is the only
moment the number is unambiguous: Playwright welds the OS window to a fixed
viewport, so ``outer - inner`` is then the chrome and nothing else.

These tests cover the measurement itself. The verdicts it feeds live in
tests/test_session_ops_mixin_actions.py.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.browser_pool.launch_helpers import _measure_frame_inset


def _session() -> Any:
    session = MagicMock()
    session.viewport_frame_inset_w = None
    session.viewport_frame_inset_h = None
    return session


def _page(**evaluate: Any) -> Any:
    page = MagicMock()
    page.evaluate = AsyncMock(**evaluate)
    return page


@pytest.mark.anyio
async def test_the_chrome_around_a_headed_window_is_recorded() -> None:
    """The numbers a real Linux/Wayland chromium reports: 8px of border, 85px of bar."""
    session = _session()

    await _measure_frame_inset(session, _page(return_value={"dw": 8, "dh": 85}))

    assert session.viewport_frame_inset_w == 8
    assert session.viewport_frame_inset_h == 85


@pytest.mark.anyio
async def test_headless_records_a_real_zero_rather_than_an_unknown() -> None:
    """Headless has no window and no chrome, and that is a measurement, not a failure."""
    session = _session()

    await _measure_frame_inset(session, _page(return_value={"dw": 0, "dh": 0}))

    assert session.viewport_frame_inset_w == 0
    assert session.viewport_frame_inset_h == 0


@pytest.mark.anyio
async def test_a_page_that_cannot_be_evaluated_leaves_the_inset_unknown() -> None:
    """A diagnostic measurement must never be able to fail a launch."""
    session = _session()

    await _measure_frame_inset(session, _page(side_effect=RuntimeError("execution context destroyed")))

    assert session.viewport_frame_inset_w is None
    assert session.viewport_frame_inset_h is None


@pytest.mark.anyio
async def test_a_negative_inset_is_rejected_rather_than_stored() -> None:
    """A window cannot be smaller than its own content area.

    Storing a negative inset would inflate the computed content area past the
    window and invent a mismatch, so an impossible reading is treated as no
    reading at all.
    """
    session = _session()

    await _measure_frame_inset(session, _page(return_value={"dw": -4, "dh": -12}))

    assert session.viewport_frame_inset_w is None
    assert session.viewport_frame_inset_h is None


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
