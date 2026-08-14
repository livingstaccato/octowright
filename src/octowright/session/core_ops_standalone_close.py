# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-owned close coordinator for a ``BrowserSession`` built without a pool.

Every production session gets a ``_pool_close_requester`` from
``launch_pipeline._build_session_object`` and routes ``close()`` through the
pool's durable ``_coordinate_close`` (see ``browser_pool/lifecycle.py``). No
production code constructs ``BrowserSession`` any other way, so this module
is reachable only from tests that build a session directly. It mirrors the
pool coordinator's reservation/outcome/cancellation shape -- FIFO close
reservation, a detached teardown task the caller's own cancellation cannot
reach, and a shared terminal outcome -- without a pool registry, manifest, or
event bus to update.

Split out of ``core_ops_mixin.py`` (imported lazily from ``close()``) to keep
that file under the repository's LOC ceiling.
"""

from __future__ import annotations

import asyncio
from typing import Any

from provide.telemetry import get_logger

from octowright.session.operation_gate import SessionClosedError

log = get_logger(__name__)


async def close_standalone(session: Any) -> None:
    gate = session._operation_gate
    reservation = await gate.reserve_close("browser_close", preflight=lambda: None)
    task = getattr(session, "_standalone_close_task", None)
    if task is None:
        task = asyncio.create_task(_run_standalone_teardown(session, gate, reservation))
        session._standalone_close_task = task
        task.add_done_callback(_observe_standalone_close_task)
    await reservation.wait()


async def _run_standalone_teardown(session: Any, gate: Any, reservation: Any) -> None:
    error: BaseException | None = None
    try:
        async with gate.close_operation(reservation):
            try:
                await session._teardown_after_close_cutoff()
            except BaseException as exc:
                error = exc
    except SessionClosedError:
        # An external close already invalidated this reservation before our
        # ticket was granted -- nothing left to prepare, but the standalone
        # session still owns resources that need tearing down (it has no
        # pool coordinator to have already done this for it).
        try:
            await session._teardown_after_close_cutoff()
        except BaseException as exc:
            error = exc
    except BaseException as exc:
        error = exc
    if error is None:
        gate.complete_close(reservation, None)
    else:
        gate.fail_close(reservation, error)


def _observe_standalone_close_task(task: asyncio.Task[None]) -> None:
    """Retrieve an unexpected exception from the detached teardown task.

    ``close_standalone`` never awaits this task directly (a caller's own
    cancellation must not reach it), so any exception it raises would
    otherwise be reported as "Task exception was never retrieved" -- its body
    already funnels every failure into ``fail_close``, so reaching here means
    something broke outside that contract.
    """
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc is not None:
        log.error("octowright.session.standalone_close_task_crashed", error=repr(exc))
