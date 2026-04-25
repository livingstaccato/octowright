# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Golden snapshot tools."""

from __future__ import annotations

from typing import Any

from .. import goldens as goldens_mod
from ._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Save the current page's accessibility tree as a named golden snapshot. "
        "Later calls to golden_assert will compare the live tree against this one."
    ),
)
async def golden_save(
    instance_id: str,
    name: str,
    description: str | None = None,
) -> dict[str, Any]:
    session = pool.get(instance_id)
    tree = await session.snapshot()
    url = session.page.url
    path = goldens_mod.save_golden(name=name, tree=tree, url=url, description=description)
    return {"saved": True, "name": name, "path": str(path)}


@mcp.tool(
    structured_output=False,
    description=(
        "Compare the current page's accessibility tree against a saved golden. "
        "Raises RuntimeError with a diff summary on mismatch."
    ),
)
async def golden_assert(instance_id: str, name: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await session.snapshot()
    expected = goldens_mod.load_golden(name)["tree"]
    diffs = goldens_mod.diff_trees(expected, actual)
    if diffs:
        raise RuntimeError({"golden": name, "diffs": diffs[:20], "diff_count": len(diffs)})
    return {"ok": True, "diffs": 0}


@mcp.tool(structured_output=False, description="List saved goldens.")
def golden_list() -> list[dict[str, Any]]:
    return goldens_mod.list_goldens()


@mcp.tool(structured_output=False, description="Delete a saved golden by name.")
def golden_delete(name: str) -> dict[str, Any]:
    path = goldens_mod.delete_golden(name)
    return {"deleted": True, "name": name, "path": str(path)}
