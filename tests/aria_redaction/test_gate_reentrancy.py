# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The scrubber re-enters its caller's lease; the caller must not fork a Task.

The credential scan takes the session's operation lease, which the gate grants
by ``asyncio.Task`` identity. A caller that wraps the scrubber in
``asyncio.wait_for`` runs it via ``ensure_future`` -- a *different* task, which
the gate treats as a stranger and queues behind the lease the caller is still
holding. The call then blocks until the queue timeout instead of returning.

A mocked gate cannot catch that, so these use the real one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from octowright.session import aria_redaction as ar
from tests._aria_stubs import credential_aware_evaluate
from tests._operation_gate_fakes import OperationAwareFake


class _Session(OperationAwareFake):
    instance_id = "aria-gate"


def _locator(aria: str = "- button: Save") -> SimpleNamespace:
    return SimpleNamespace(
        aria_snapshot=AsyncMock(return_value=aria),
        first=SimpleNamespace(evaluate=credential_aware_evaluate()),
    )


async def test_scrubber_reenters_a_held_lease() -> None:
    """The common case: a tool holds its lease and calls the scrubber inside."""
    session = _Session()
    async with session.operation("browser_snapshot"):
        aria = await ar.aria_snapshot(session, _locator())
    assert aria == "- button: Save"


async def test_forking_a_task_around_the_scrubber_would_stall() -> None:
    """Pins the hazard itself, so the rule stays discoverable.

    ``asyncio.wait_for`` runs its argument in a new Task. Under a held lease
    that task is not the owner, so it waits -- and with the lease held for the
    duration, it never gets in. This is why ``inspect_capture`` uses
    ``asyncio.timeout`` instead.
    """
    session = _Session()
    async with session.operation("browser_capture_and_close"):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                asyncio.ensure_future(ar.aria_snapshot(session, _locator())),
                timeout=0.25,
            )


async def test_asyncio_timeout_keeps_the_same_task() -> None:
    """The pattern inspect_capture actually uses stays on the owning task."""
    session = _Session()
    async with session.operation("browser_capture_and_close"):
        async with asyncio.timeout(5):
            aria = await ar.aria_snapshot(session, _locator())
    assert aria == "- button: Save"
