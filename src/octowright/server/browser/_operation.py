# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One DRY boundary for a COMPLETE browser tool workflow.

Task 5 decorated every session method with ``@gated_operation`` so a direct
Python call is always serialized. This helper defines a wider boundary
around a whole MCP-tool call — a composite like ``browser_click(...,
response_mode="outline")`` dispatches a click AND builds an outline
response, and both must stay under ONE observable root operation instead of
two separate gate acquisitions with a window between them where a
concurrent caller could interleave. Reentrancy (same-task, Task 2) is what
makes this safe: the composite's own nested ``session.click(...)`` /
``browser_page_outline(...)`` calls re-enter the SAME lease this context
manager already holds rather than queueing behind it.

``operation_name`` must always be a source-code string literal (never a
variable, never an f-string) — this is what lets a static scanner prove
every Page/Frame/locator access in these modules runs under a named,
grep-able operation boundary instead of requiring call-graph analysis.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, LiteralString

if TYPE_CHECKING:
    from octowright.browser_pool import BrowserPool
    from octowright.session import BrowserSession

__all__ = ["browser_operation"]


@asynccontextmanager
async def browser_operation(
    browser_pool: BrowserPool,
    instance_id: str,
    operation_name: LiteralString,
) -> AsyncIterator[BrowserSession]:
    session = browser_pool.get(instance_id)
    async with session.operation(operation_name):
        yield session
