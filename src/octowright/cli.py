from __future__ import annotations

import sys

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

    from .pool import BrowserPool
    from . import runner

    pool = BrowserPool()
    setup_telemetry()
    try:
        result = asyncio.run(runner.run_suite(
            macros_dir=macros_dir, kind=kind, tag=tag,
            out_path=out_path, pool=pool,
        ))
        asyncio.run(pool.shutdown())
        click.echo(f"{result['passed']}/{result['total']} passed")
        click.echo(f"report: {result['report_path']}")
        raise SystemExit(0 if result["failed"] == 0 else 1)
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
    import yaml as _yaml
    setup_telemetry()
    try:
        p_dir = _p.persona_dir(name)
        p_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = p_dir / "profile.yaml"
        if yaml_path.exists():
            click.echo(f"refusing to overwrite {yaml_path}", err=True)
            raise SystemExit(1)
        doc: dict[str, object] = {"name": _p._slug(name)}
        if display_name:
            doc["display_name"] = display_name
        if default_url:
            doc["default_url"] = default_url
        yaml_path.write_text(_yaml.safe_dump(doc))
        click.echo(f"created {yaml_path}")
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


def main() -> None:
    cli.main(standalone_mode=True)


if __name__ == "__main__":
    sys.exit(main())
