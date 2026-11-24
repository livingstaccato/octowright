# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Post-input outline failures preserve the scroll delivery result."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.server.browser import input as browser_input
from tests._operation_gate_fakes import OperationAwareFake

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.parametrize("failure", [RuntimeError("page navigated"), asyncio.CancelledError()])
async def test_scroll_outline_failure_preserves_delivery_or_cancellation(
    monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    session = OperationAwareFake()
    session.scroll = AsyncMock(return_value={"ok": True, "settled": True, "y": 120})
    monkeypatch.setattr(browser_input, "pool", MagicMock(get=MagicMock(return_value=session)))
    monkeypatch.setattr(browser_input, "browser_page_outline", AsyncMock(side_effect=failure))

    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await browser_input.browser_scroll("i", delta_y=120, response_mode="outline")
    else:
        result = await browser_input.browser_scroll("i", delta_y=120, response_mode="outline")
        assert result == {
            "ok": True,
            "settled": True,
            "y": 120,
            "outline": None,
            "outline_error": "Outline unavailable after scroll input was delivered.",
        }
    session.scroll.assert_awaited_once_with(delta_x=0, delta_y=120, selector=None)
