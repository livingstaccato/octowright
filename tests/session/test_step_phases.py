# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A fill/type step's timeout says which of its phases spent the budget.

A fill shares one budget between waiting for its element, reading its role
metadata, classifying it for redaction and the fill itself. A Windows Firefox
run held the lease 21 s against a 15 s budget and failed with
``Page.fill: Timeout 1ms exceeded`` -- the action got nothing, and nothing said
which earlier phase had taken it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from octowright.session.core_locator_mixin import SessionLocatorMixin
from octowright.session.core_page_mixin import SessionPageMixin
from octowright.session.step_phases import StepPhases
from tests._aria_stubs import credential_aware_evaluate
from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _PageFake(OperationAwareFake, SessionPageMixin):
    pass


class _LocatorFake(OperationAwareFake, SessionLocatorMixin):
    pass


def _subject(*, slow_aria_s: float = 0.0) -> tuple[_PageFake, MagicMock]:
    subj = _PageFake()
    subj._last_mcp_navigation = None
    subj.page = MagicMock()
    subj.pages = [subj.page]
    subj.recorder = MagicMock()

    async def _aria(*_: Any, **__: Any) -> str:
        await asyncio.sleep(slow_aria_s)
        return ""

    locator = MagicMock()
    locator.aria_snapshot = AsyncMock(side_effect=_aria)
    first = MagicMock()
    first.evaluate = credential_aware_evaluate(other={"type": "text", "ac": ""})
    first.wait_for = AsyncMock()
    locator.first = first
    target = MagicMock()
    target.locator = MagicMock(return_value=locator)
    target.fill = AsyncMock(side_effect=PlaywrightTimeoutError("Page.fill: Timeout 1ms exceeded."))
    target.type = AsyncMock(side_effect=PlaywrightTimeoutError("Page.type: Timeout 1ms exceeded."))
    subj._target = lambda: target  # type: ignore[attr-defined]
    return subj, target


def _ms(message: str, phase: str) -> int:
    match = re.search(rf"\b{re.escape(phase)} (\d+)ms", message)
    assert match, f"{phase!r} not named in {message!r}"
    return int(match.group(1))


@pytest.mark.anyio
async def test_a_fill_timeout_names_the_phase_that_spent_the_budget() -> None:
    subj, _ = _subject(slow_aria_s=0.15)

    with pytest.raises(PlaywrightTimeoutError) as caught:
        await subj.fill("#pw", "secret")

    message = str(caught.value)
    assert message.startswith("Page.fill: Timeout 1ms exceeded."), "Playwright's own message comes first"
    assert "step budget 15000ms" in message
    # The metadata phase sleeps 150ms. Not ">= 150": Windows' ~15.6ms timer
    # granularity measured it as 140 (#270). What the test means is that
    # metadata clearly spent the budget, so it is bounded loosely below and
    # compared with the phase that did no work -- which keeps an absolute
    # ceiling, so a phase misattributing the sleep to attach still fails.
    metadata, attach = _ms(message, "metadata"), _ms(message, "attach")
    assert metadata >= 100
    assert attach < 50
    assert metadata > attach
    assert "fill (timed out)" in message
    assert "secret" not in message


@pytest.mark.anyio
async def test_a_type_timeout_names_its_phases_too() -> None:
    subj, _ = _subject()

    with pytest.raises(PlaywrightTimeoutError, match=r"step budget 15000ms.*attach \d+ms.*type \(timed out\)"):
        await subj.type_text("#name", "abc", None)


@pytest.mark.anyio
async def test_a_fill_by_timeout_names_its_phases() -> None:
    subj = _LocatorFake()
    subj.recorder = MagicMock()
    locator = MagicMock()
    locator.wait_for = AsyncMock()
    locator.evaluate = AsyncMock(return_value={"type": "text", "ac": ""})
    locator.fill = AsyncMock(side_effect=PlaywrightTimeoutError("Locator.fill: Timeout 1ms exceeded."))
    subj._locator = AsyncMock(return_value=locator)  # type: ignore[method-assign]

    with pytest.raises(PlaywrightTimeoutError, match=r"step budget 2000ms.*attach \d+ms.*fill \(timed out\)"):
        await subj.fill_by("v", timeout_ms=2000, role="textbox")


@pytest.mark.anyio
async def test_a_step_that_succeeds_raises_nothing_and_an_other_error_passes_unchanged() -> None:
    subj, target = _subject()
    target.fill = AsyncMock()
    await subj.fill("#pw", "secret")

    boom = RuntimeError("detached")
    target.fill = AsyncMock(side_effect=boom)
    with pytest.raises(RuntimeError) as caught:
        await subj.fill("#pw", "secret")
    assert caught.value is boom


def test_the_explained_error_keeps_playwrights_type_and_name() -> None:
    original = PlaywrightTimeoutError("Page.fill: Timeout 1ms exceeded.")
    original._name = "TimeoutError"
    phases = StepPhases(15000, "attach", "fill")

    explained = phases.explain(original)

    assert type(explained) is PlaywrightTimeoutError
    assert explained.name == "TimeoutError"
    assert explained.message == str(explained)
