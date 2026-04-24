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


def main() -> None:
    cli.main(standalone_mode=True)


if __name__ == "__main__":
    sys.exit(main())
