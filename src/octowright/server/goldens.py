# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Golden snapshot tools."""

from __future__ import annotations

from typing import Any

from octowright import goldens as goldens_mod
from octowright.server._state import mcp, pool


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


class GoldenMismatchError(AssertionError):
    """Raised by `golden_assert` when the live tree differs from the golden.

    Carries the diff list as `.diffs` (first 20 entries) so MCP clients can
    surface the cause; the JSON-RPC error payload includes `str(exc)`.
    """

    def __init__(self, name: str, diffs: list[Any]):
        self.golden = name
        self.diffs = diffs[:20]
        self.diff_count = len(diffs)
        super().__init__(f"golden {name!r} mismatch: {self.diff_count} diff(s); first: {self.diffs[:1]}")


@mcp.tool(
    structured_output=False,
    description=(
        "Compare the current page's accessibility tree against a saved golden. "
        "RAISES GoldenMismatchError on any drift (the JSON-RPC error carries "
        "the first diff). Returns {ok: true, diffs: 0} only on exact match. "
        "Use golden_verify_loop instead when you want to poll without raising."
    ),
)
async def golden_assert(instance_id: str, name: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await session.snapshot()
    expected = goldens_mod.load_golden(name)["tree"]
    diffs = goldens_mod.diff_trees(expected, actual)
    if diffs:
        raise GoldenMismatchError(name, diffs)
    return {"ok": True, "diffs": 0}


@mcp.tool(
    structured_output=False,
    description=(
        "Compare the current page's accessibility tree against a saved golden. "
        "Returns a dict with 'ok' and 'diffs' instead of raising. "
        "Useful for loops that need to wait for a specific state."
    ),
)
async def golden_verify_loop(instance_id: str, name: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    actual = await session.snapshot()
    expected = goldens_mod.load_golden(name)["tree"]
    diffs = goldens_mod.diff_trees(expected, actual)
    if not diffs:
        return {"ok": True, "diffs": 0}
    return {
        "ok": False,
        "diff_count": len(diffs),
        "diffs": diffs[:10],
    }


@mcp.tool(structured_output=False, description="List saved goldens.")
def golden_list() -> list[dict[str, Any]]:
    return goldens_mod.list_goldens()


@mcp.tool(structured_output=False, description="Delete a saved golden by name.")
def golden_delete(name: str) -> dict[str, Any]:
    path = goldens_mod.delete_golden(name)
    return {"deleted": True, "name": name, "path": str(path)}
