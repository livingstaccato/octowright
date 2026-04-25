# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from .server import mcp, recordings_dir, registered_tool_names


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def cli(ctx: click.Context) -> None:
    """octowright — MCP server that drives multiple headed Playwright browsers in parallel."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


@cli.command()
def serve() -> None:
    """Run the MCP stdio server (default)."""
    setup_telemetry()
    try:
        mcp.run()
    finally:
        shutdown_telemetry()


@cli.command()
@click.argument("macros_dir", required=False)
@click.option("--kind", default="webkit", help="Browser engine to use for tests.")
@click.option("--tag", default=None, help="Only run macros tagged with [tag].")
@click.option("--out", "out_path", default=None, help="JUnit XML output path.")
def test(macros_dir: str | None, kind: str, tag: str | None, out_path: str | None) -> None:
    """Run all test macros in a directory. Outputs JUnit XML."""
    import asyncio

    from . import runner
    from .pool import BrowserPool

    setup_telemetry()

    async def _run() -> dict[str, Any]:
        # Pool + suite + shutdown all share one event loop. Calling asyncio.run
        # twice creates separate loops and the playwright objects on the pool
        # can't be torn down by a fresh loop ("Event loop is closed").
        pool = BrowserPool()
        try:
            return await runner.run_suite(
                macros_dir=macros_dir,
                kind=kind,
                tag=tag,
                out_path=out_path,
                pool=pool,
            )
        finally:
            await pool.shutdown()

    try:
        result = asyncio.run(_run())
        click.echo(f"{result['passed']}/{result['total']} passed")
        click.echo(f"report: {result['report_path']}")
        raise SystemExit(0 if result["failed"] == 0 else 1)
    finally:
        shutdown_telemetry()


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing sample persona/scenario/macro files.")
def init(force: bool) -> None:
    """Scaffold the standard layout: profile/scenario/macro dirs + samples + MCP registration block."""
    from . import scaffold
    from .defaults import PROFILES_DIR, SCENARIOS_DIR
    from .macros import MACROS_DIR

    setup_telemetry()
    try:
        report = scaffold.scaffold_all(
            profiles_dir=PROFILES_DIR,
            macros_dir=MACROS_DIR,
            scenarios_dir=SCENARIOS_DIR,
            force=force,
        )
        scaffold.render_report(report)
    finally:
        shutdown_telemetry()


@cli.command()
def selftest() -> None:
    """List registered tools and exit."""
    setup_telemetry()
    try:
        names = registered_tool_names()
        click.echo(f"recordings dir: {recordings_dir()}")
        click.echo(f"{len(names)} tools registered:")
        for name in names:
            click.echo(f"  - {name}")
    finally:
        shutdown_telemetry()


@cli.group()
def persona() -> None:
    """Manage personas (identity + browser-profile containers)."""


@persona.command("list")
def persona_list_cmd() -> None:
    """List all personas with engines and last-used timestamps."""
    from . import personas as _p

    setup_telemetry()
    try:
        for row in _p.list_personas():
            engines = ",".join(row["engines"]) or "-"
            dn = row.get("display_name") or ""
            click.echo(f"{row['name']:20s}  engines={engines:30s}  {dn}")
    finally:
        shutdown_telemetry()


@persona.command("show")
@click.argument("name")
def persona_show_cmd(name: str) -> None:
    """Print the full profile.yaml for a persona."""
    from . import personas as _p

    setup_telemetry()
    try:
        p = _p.load_persona(name)
        click.echo(f"name:          {p.name}")
        click.echo(f"display_name:  {p.display_name}")
        click.echo(f"default_url:   {p.default_url}")
        click.echo(f"default_macros: {p.default_macros}")
        click.echo(f"credentials:   {list(p.credentials.keys())}")
        click.echo(f"app:           {p.app}")
    finally:
        shutdown_telemetry()


@persona.command("create")
@click.argument("name")
@click.option("--display", "display_name", default=None)
@click.option("--url", "default_url", default=None)
def persona_create_cmd(name: str, display_name: str | None, default_url: str | None) -> None:
    """Scaffold a new persona dir + stub profile.yaml."""
    from . import personas as _p

    setup_telemetry()
    try:
        try:
            pdir = _p.create_persona(
                name,
                display_name=display_name,
                default_url=default_url,
            )
        except FileExistsError as e:
            click.echo(str(e), err=True)
            raise SystemExit(1) from e
        click.echo(f"created {pdir}")
    finally:
        shutdown_telemetry()


@persona.command("delete")
@click.argument("name")
def persona_delete_cmd(name: str) -> None:
    """Delete an entire persona (all engines + metadata)."""
    from .profiles import delete_persona

    setup_telemetry()
    try:
        path = delete_persona(name)
        click.echo(f"deleted {path}")
    finally:
        shutdown_telemetry()


@cli.command("migrate-profiles")
def migrate_profiles_cmd() -> None:
    """One-shot: migrate legacy profiles/<kind>/<name>/ to profiles/<name>/<kind>/."""
    from . import personas as _p

    setup_telemetry()
    try:
        summary = _p.migrate_legacy_layout()
        click.echo(f"moved {summary['moved']} engine-profile dir(s) across {summary['personas']} persona(s)")
    finally:
        shutdown_telemetry()


@cli.group()
def scenario() -> None:
    """Start / list browser scenarios."""


@scenario.command("list")
def scenario_list_cmd() -> None:
    """List scenario specs on disk."""
    from . import scenarios as _s

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

    from . import scenarios as _s
    from .pool import BrowserPool

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
                        line = _format_watch_event(ev)
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


# Watch event fields that don't add information for the human reader.
_WATCH_HIDDEN_FIELDS = frozenset(
    {"ts", "action", "instance_id", "persona", "role", "kind", "label", "profile", "user_data_dir", "viewport"}
)
# Field-name salience order: when an event has one of these, that's the "headline" arg.
_WATCH_HEADLINE_FIELDS = ("url", "selector", "text", "key", "name", "pattern", "expression", "policy", "path")


def _format_watch_event(ev: dict[str, Any]) -> str | None:
    """One-line scenario-watch event format.

    `[HH:MM:SS] persona/role  action  headline   …extras` — or None to skip.
    """
    action = ev.get("action", "?")
    if action == "console":
        return None
    ts = ev.get("ts", "")[11:19] or "--:--:--"
    persona = ev.get("persona", "?")
    role = ev.get("role", "?")

    headline = ""
    for field in _WATCH_HEADLINE_FIELDS:
        if field in ev and ev[field] is not None:
            val = ev[field]
            rendered = val if isinstance(val, str) else repr(val)
            if len(rendered) > 60:
                rendered = rendered[:57] + "…"
            headline = rendered
            break

    extras_pairs = [
        f"{k}={v!r}"
        for k, v in ev.items()
        if k not in _WATCH_HIDDEN_FIELDS and k not in _WATCH_HEADLINE_FIELDS and v is not None
    ]
    extras = "  " + " ".join(extras_pairs) if extras_pairs else ""

    tag = f"{persona}/{role}"
    return f"[{ts}] {tag:<22}  {action:<14} {headline}{extras}"


async def _run_verify_and_report(*, pool: Any, live: Any, out_path: str | None) -> int:
    """Run each participant's role verify macro, write JUnit XML, return 0/1."""
    from datetime import UTC, datetime
    from pathlib import Path

    from . import macros as _m
    from . import runner as _r

    if not live.spec.verify:
        click.echo(f"scenario {live.name!r} has no verify macros", err=True)
        return 2

    results: list[dict[str, Any]] = []
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


def main() -> None:
    # click handles its own SystemExit when standalone_mode=True; this never returns normally.
    cli.main(standalone_mode=True)


if __name__ == "__main__":
    main()
