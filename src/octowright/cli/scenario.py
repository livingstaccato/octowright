# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright scenario`` — start / list browser scenarios."""

from __future__ import annotations

from typing import Any, cast

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli
from octowright.cli.watch import _format_watch_event
from octowright.mcp_types import TestSuiteCaseResult


@cli.group()
def scenario() -> None:
    """Start / list browser scenarios."""


@scenario.command("list")
def scenario_list_cmd() -> None:
    """List scenario specs on disk."""
    from octowright import scenarios as _s

    setup_telemetry()
    try:
        for row in _s.list_scenarios():
            click.echo(f"{row['name']:30s}  {row['form']:6s}  {row['path']}")
    finally:
        shutdown_telemetry()


@scenario.command("start")
@click.argument("name")
@click.option("--test", "test_mode", is_flag=True, help="Run verify macros after start; emit pass/fail and exit.")
@click.option("--out", "out_path", default=None, help="JUnit XML output path (used with --test).")
@click.option("--watch", is_flag=True, help="Stream participant events as they happen.")
def scenario_start_cmd(name: str, test_mode: bool, out_path: str | None, watch: bool) -> None:
    """Start a scenario and hold its browsers open until Ctrl-C (or --test exit)."""
    import asyncio as _asyncio
    import signal

    from octowright import scenarios as _s
    from octowright.browser_pool import BrowserPool

    setup_telemetry()

    async def _run() -> int:
        pool = BrowserPool()
        spool = _s.ScenarioPool()
        try:
            live = await spool.start(name=name, browser_pool=pool)
            click.echo(f"scenario_id: {live.scenario_id}")
            for p in live.participants:
                click.echo(
                    f"  [{p['role']:10s}] {p['persona']:15s} {p['kind']:10s} {p['instance_id']}  {p.get('url', '')}"
                )

            if test_mode:
                exit_code = await _run_verify_and_report(
                    pool=pool,
                    live=live,
                    out_path=out_path,
                )
                await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
                return exit_code

            click.echo("\nbrowsers open; Ctrl-C to tear down and exit.")
            if watch:
                click.echo("(--watch: streaming events; Ctrl-C to stop)\n")
            stop = _asyncio.get_running_loop().create_future()

            def _handle(*_: object) -> None:
                if not stop.done():
                    stop.set_result(None)

            _asyncio.get_running_loop().add_signal_handler(signal.SIGINT, _handle)
            _asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _handle)

            async def _watcher() -> None:
                cursors: dict[str, int] = {}
                while not stop.done():
                    try:
                        result = spool.tail(scenario_id=live.scenario_id, since_cursors=cursors)
                    except Exception:
                        return
                    cursors = result["cursors"]
                    for ev in result["events"]:
                        line = _format_watch_event(cast("dict[str, Any]", ev))
                        if line is not None:
                            click.echo(line)
                    await _asyncio.sleep(1.0)

            if watch:
                watcher_task: _asyncio.Task[None] | None = _asyncio.create_task(_watcher())
            else:
                watcher_task = None

            await stop
            if watcher_task is not None:
                watcher_task.cancel()
                try:
                    await watcher_task
                except _asyncio.CancelledError:
                    pass
            await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
            return 0
        finally:
            await pool.shutdown()

    try:
        exit_code = _asyncio.run(_run())
    finally:
        shutdown_telemetry()
    raise SystemExit(exit_code)


async def _run_verify_and_report(*, pool: Any, live: Any, out_path: str | None) -> int:
    """Run each participant's role verify macro, write JUnit XML, return 0/1."""
    from datetime import UTC, datetime
    from pathlib import Path

    from octowright import macros as _m
    from octowright import runner as _r

    if not live.spec.verify:
        click.echo(f"scenario {live.name!r} has no verify macros", err=True)
        return 2

    results: list[TestSuiteCaseResult] = []
    for p in live.participants:
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
            continue
        start = datetime.now(UTC)
        try:
            session = pool.get(p["instance_id"])
            await _m.run_macro(session=session, name=macro, args={})
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

    target = Path(out_path) if out_path else _r._default_report_path()
    _r._write_junit(results, target, kind="scenario")
    passed = sum(1 for r in results if r["ok"])
    click.echo(f"\n{passed}/{len(results)} verify passed")
    click.echo(f"report: {target}")
    return 0 if passed == len(results) else 1
