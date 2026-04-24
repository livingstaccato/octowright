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
