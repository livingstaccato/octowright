# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio

import pytest

from octowright.session.timeouts import (
    SessionCallTimeoutError,
    bounded,
    unbounded_call_timeout_seconds,
)


async def test_returns_the_value_when_the_call_completes() -> None:
    async def quick() -> int:
        return 7

    assert await bounded(quick(), operation="browser_evaluate") == 7


async def test_raises_a_typed_error_when_the_call_hangs() -> None:
    async def wedged() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(SessionCallTimeoutError, match="browser_evaluate"):
        await bounded(wedged(), operation="browser_evaluate", timeout=0.01)


async def test_the_error_names_the_budget_so_the_message_is_actionable() -> None:
    async def wedged() -> None:
        await asyncio.sleep(3600)

    with pytest.raises(SessionCallTimeoutError, match=r"0\.01"):
        await bounded(wedged(), operation="browser_title", timeout=0.01)


def test_budget_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hang guard that defaults off would not have caught the incident."""
    monkeypatch.delenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", raising=False)
    assert unbounded_call_timeout_seconds() > 0


@pytest.mark.parametrize("token", ["0", "off", "never", "none", "disabled", "false", "no"])
def test_falsey_tokens_restore_unbounded_behaviour(token: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", token)
    assert unbounded_call_timeout_seconds() == 0.0


@pytest.mark.parametrize("raw", ["", "abc", "-5", "nan"])
def test_unparsable_or_nonpositive_falls_back_to_the_default(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never silently unbound on a typo — that is the failure mode being fixed."""
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", raw)
    assert unbounded_call_timeout_seconds() > 0


async def test_a_disabled_budget_does_not_wrap() -> None:
    async def quick() -> int:
        return 3

    assert await bounded(quick(), operation="browser_evaluate", timeout=0.0) == 3
