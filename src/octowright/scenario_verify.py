# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Run a live scenario's per-role verify macros -- one loop for the MCP tool and the CLI.

``scenario_run_as_test`` and ``octowright scenario start --test`` each had
their own copy, and the CLI's lacked the capability check: a plugin participant
(a terminal, which has no ``run_macro``) became a browser test target and
failed through ``pool.get``'s ``KeyError`` or a "no verify macro" case, so the
CLI exited 1 where the tool passed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from octowright.mcp_types import TestSuiteCaseResult
from octowright.plugins.contract import SupportsMacros
from octowright.scenario_kinds import adapter_for


async def _verify_one(p: dict[str, Any], verify: dict[str, str], browser_pool: Any) -> TestSuiteCaseResult | None:
    # Skip by capability, not by kind name -- mirrors ScenarioPool.run_macro.
    # A kind with no run_macro (terminal today, any future capability-less
    # plugin kind) is not a test target at all: skipped cleanly, with no test
    # case appended.
    adapter = adapter_for(p.get("kind") or "", browser_pool=browser_pool)
    if not isinstance(adapter, SupportsMacros):
        return None
    name = f"{p['role']}:{p['persona']}"
    macro = verify.get(p["role"])
    if not macro:
        return {"name": name, "ok": False, "error": f"no verify macro for role {p['role']!r}", "duration": 0.0}
    start = datetime.now(UTC)
    try:
        await adapter.run_macro(p["instance_id"], name=macro, args={})
        ok, err = True, None
    except Exception as e:
        ok, err = False, repr(e)
    return {"name": name, "ok": ok, "error": err, "duration": (datetime.now(UTC) - start).total_seconds()}


async def run_verify_cases(live: Any, *, browser_pool: Any) -> list[TestSuiteCaseResult]:
    """One case per macro-capable participant, in participant order; run concurrently."""
    verify = live.spec.verify or {}
    outcomes = await asyncio.gather(*(_verify_one(p, verify, browser_pool) for p in live.participants))
    return [r for r in outcomes if r is not None]
