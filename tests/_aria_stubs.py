# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Test doubles for the credential-classification call on an aria snapshot.

``octowright.session.aria_redaction.aria_snapshot`` reads the values of a
document's credential fields before it snapshots, so a locator double has to
model that call as well as ``aria_snapshot()``. Failing to model it makes the
scrubber fail closed -- which is the correct production behaviour, and the
reason this helper exists rather than a looser production check.

Some locator doubles already stub ``first.evaluate`` for the record-time
``_is_password_input`` probe, which uses the same Playwright entry point.
The dispatcher below keys on the production JS constant by identity, so the
two probes stay distinguishable without matching on source text.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from octowright.session.aria_redaction import _CREDENTIAL_VALUES_JS


def credential_aware_evaluate(credentials: list[str] | None = None, other: Any = None) -> AsyncMock:
    """An ``evaluate`` double that answers the credential scan and one other probe."""
    values = list(credentials or [])

    async def _dispatch(expression: Any, *_args: Any, **_kwargs: Any) -> Any:
        if expression is _CREDENTIAL_VALUES_JS:
            return values
        return other

    return AsyncMock(side_effect=_dispatch)


def stub_credential_scan(locator: Any, values: list[str] | None = None) -> Any:
    """Teach *locator* to answer the credential-value scan with *values*."""
    # wait_for: a fill/type waits for its element to be attached first.
    locator.first = SimpleNamespace(evaluate=credential_aware_evaluate(values), wait_for=AsyncMock())
    return locator


# When the running test began (``time.monotonic()``); set by ``tests/conftest``
# at the start of every test's setup. Bounds how much of a step's budget the
# test can have spent before the action under assertion was called.
_test_started_at: float | None = None


def mark_test_start() -> None:
    global _test_started_at
    _test_started_at = time.monotonic()


class LeftOfBudget:
    """Equals a timeout that is what is left of *budget*: a fill/type step shares one deadline.

    Its element's attached-wait gets the whole budget and the action what is
    left, so the action's timeout is under the budget, never above it. How far
    under is wall time the step spent before the action, which a slow runner
    makes unbounded by any constant (a macOS runner spent 125ms against the
    100ms this once allowed). The step began after the test did and its action
    ran before this comparison, so the time since the test started is a bound
    it cannot exceed; *slack_ms* is the floor when no start was recorded.
    """

    def __init__(self, budget: float, slack_ms: float = 100) -> None:
        self.budget = budget
        self.slack_ms = slack_ms

    def _slack(self) -> float:
        if _test_started_at is None:
            return self.slack_ms
        # +1: the product rounds what is left to whole milliseconds.
        return max(self.slack_ms, (time.monotonic() - _test_started_at) * 1000 + 1)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, (int, float)) and self.budget - self._slack() < other <= self.budget

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"<left of {self.budget}ms>"


class FakeAriaLocator:
    """Locator double that returns a fixed tree and a fixed credential list."""

    def __init__(self, aria: str, credentials: list[str] | None = None) -> None:
        self._aria = aria
        self.first = SimpleNamespace(evaluate=credential_aware_evaluate(credentials), wait_for=AsyncMock())

    async def aria_snapshot(self) -> str:
        return self._aria
