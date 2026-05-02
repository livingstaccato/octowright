# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Scenario tools: list / start / status / stop / run_macro / participants / run_as_test / tail."""

from __future__ import annotations

import asyncio as _asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .. import _format as fmt
from .. import macros as macro_mod
from .. import runner as runner_mod
from .. import scenarios as scenario_mod
from ._state import mcp, pool, scenario_pool


@mcp.tool(structured_output=False, description="List scenario specs on disk (YAML or Python).")
def scenario_list() -> list[dict[str, Any]]:
    return scenario_mod.list_scenarios()


@mcp.tool(
    structured_output=False,
    description=(
        "DRY-RUN -- does NOT launch any browsers. Returns the resolved per-participant "
        "launch_kwargs and startup_macros that scenario_start would use, so you can verify "
        "a scenario's wiring before committing to N browser windows. Use this whenever you're "
        "about to call scenario_start for the first time on a new spec."
    ),
)
def scenario_plan(name: str) -> dict[str, Any]:
    spec = scenario_mod.load_scenario(name)
    participants: list[dict[str, Any]] = []
    for p in spec.participants:
        participants.append(
            {
                "persona": p.persona,
                "kind": p.kind,
                "role": p.role,
                "launch_kwargs": scenario_mod.resolve_launch_kwargs(p),
                "startup_macros": scenario_mod.resolve_startup_macros(p),
            }
        )
    return {
        "name": spec.name,
        "description": spec.description,
        "summary": fmt.participant_summary(participants) or "0 participants",
        "participants": participants,
        "fixtures": dict(spec.fixtures),
        "teardown_macro": spec.teardown_macro,
        "verify": dict(spec.verify),
        "would_launch": len(participants),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Start a scenario. Launches every participant in parallel, applies shared fixtures, "
        "runs startup_macros per-participant. Browsers stay open; returns the participant table."
    ),
)
async def scenario_start(name: str) -> dict[str, Any]:
    live = await scenario_pool.start(name=name, browser_pool=pool)
    return {
        "scenario_id": live.scenario_id,
        "name": live.name,
        "participants": live.participants,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Start a scenario from a template. Templates support simple {{key}} substitution "
        "using the provided `args`. Returns the participant table."
    ),
)
async def scenario_spawn_template(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = scenario_mod.load_scenario_template(name, args or {})
    live = await scenario_pool.start(spec=spec, browser_pool=pool)
    return {
        "scenario_id": live.scenario_id,
        "name": live.name,
        "participants": live.participants,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "List live scenarios and their participants. Returns {summary, count, scenarios}: "
        "`summary` is a one-line gist (e.g. 'scenario \\'mini\\' (2 participants): "
        "player[dante]/webkit · monitor[ops]/firefox'); `scenarios` is the structured data."
    ),
)
def scenario_status() -> dict[str, Any]:
    live = scenario_pool.list_live()
    return {
        "summary": fmt.scenario_summary(live),
        "count": len(live),
        "scenarios": live,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Stop a live scenario: run teardown_macro per participant (if any), close every "
        "participant browser. Returns close + teardown error summary."
    ),
)
async def scenario_stop(scenario_id: str) -> dict[str, Any]:
    return await scenario_pool.stop(scenario_id=scenario_id, browser_pool=pool)


@mcp.tool(
    structured_output=False,
    description=(
        "Broadcast a macro across participants of a live scenario. Optionally role-filter. "
        "Returns per-participant results."
    ),
)
async def scenario_run_macro(
    scenario_id: str,
    macro: str,
    role: str | None = None,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await scenario_pool.run_macro(
        scenario_id=scenario_id,
        macro=macro,
        browser_pool=pool,
        role=role,
        args=args,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Block until all participants (optionally filtered by role) satisfy a condition. "
        "Condition options: `selector` (all must see selector), `text` (all must see text), "
        "or `url` (all must be at this URL regex). If none supplied, waits for 'networkidle'."
    ),
)
async def scenario_wait_for_sync(
    scenario_id: str,
    role: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    url: str | None = None,
    timeout_ms: int | None = None,
) -> dict[str, Any]:
    return await scenario_pool.wait_for_sync(
        scenario_id=scenario_id,
        browser_pool=pool,
        role=role,
        selector=selector,
        text=text,
        url=url,
        timeout_ms=timeout_ms,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "List participants of a live scenario, optionally filtered by role. Returns {summary, count, participants}."
    ),
)
def scenario_participants(scenario_id: str, role: str | None = None) -> dict[str, Any]:
    live = scenario_pool.get(scenario_id)
    matched = [p for p in live.participants if role is None or p["role"] == role]
    return {
        "summary": fmt.participant_summary(matched) or "0 participants",
        "count": len(matched),
        "participants": matched,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Run the scenario's verify macros as a test suite and return pass/fail. "
        "Requires the scenario spec to declare `verify: {role: macro_name}`. "
        "Writes JUnit XML to out_path if supplied."
    ),
)
async def scenario_run_as_test(
    scenario_id: str,
    out_path: str | None = None,
) -> dict[str, Any]:
    live = scenario_pool.get(scenario_id)
    if not live.spec.verify:
        raise RuntimeError(f"scenario {live.name!r} declares no verify macros")

    results: list[dict[str, Any]] = []

    async def _run(p: dict[str, Any]) -> None:
        macro = live.spec.verify.get(p["role"])
        if not macro:
            results.append(
                {
                    "name": f"{p['role']}:{p['persona']}",
                    "ok": False,
                    "error": f"no verify macro for role {p['role']!r}",
                    "duration": 0.0,
                }
            )
            return
        start = datetime.now(UTC)
        try:
            session = pool.get(p["instance_id"])
            await macro_mod.run_macro(session=session, name=macro, args={})
            ok, err = True, None
        except Exception as e:
            ok, err = False, repr(e)
        duration = (datetime.now(UTC) - start).total_seconds()
        results.append(
            {
                "name": f"{p['role']}:{p['persona']}",
                "ok": ok,
                "error": err,
                "duration": duration,
            }
        )

    await _asyncio.gather(*(_run(p) for p in live.participants))
    passed = sum(1 for r in results if r["ok"])
    report_path = Path(out_path) if out_path else runner_mod._default_report_path()
    runner_mod._write_junit(results, report_path, kind="scenario")
    return {
        "scenario_id": scenario_id,
        "name": live.name,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "report_path": str(report_path),
        "results": results,
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Return new JSONL events appended to each participant's recording log "
        "since the supplied cursors. Pass {} (or omit) to read from the beginning; "
        "pass the previous call's `cursors` for incremental polling. Cursors are "
        "byte offsets keyed by instance_id."
    ),
)
def scenario_tail(
    scenario_id: str,
    since_cursors: dict[str, int] | None = None,
) -> dict[str, Any]:
    return scenario_pool.tail(scenario_id=scenario_id, since_cursors=since_cursors)
