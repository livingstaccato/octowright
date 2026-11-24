# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Independent live rendering and metadata recording controls."""

from __future__ import annotations

from typing import Any

from pydantic import StrictBool

from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation


@mcp.tool(
    structured_output=False,
    description=(
        "Configure live interaction indicators and interaction metadata recording independently. "
        "Provide show and/or record as booleans. Both default off at launch. "
        "Returns requested host show/record, truncated, applied_frames and pending_frames; "
        "pending frames may be retried with the same settings. Live markers are captured in video pixels."
    ),
)
async def browser_set_interaction_visuals(
    instance_id: str, show: StrictBool | None = None, record: StrictBool | None = None
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_set_interaction_visuals") as session:
        return await session.set_interaction_visuals(show=show, record=record)
