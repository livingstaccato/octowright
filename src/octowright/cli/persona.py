# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright persona`` — manage personas (identity + browser-profile containers)."""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli


@cli.group()
def persona() -> None:
    """Manage personas (identity + browser-profile containers)."""


@persona.command("list")
def persona_list_cmd() -> None:
    """List all personas with engines and last-used timestamps."""
    from octowright import personas as _p

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
    from octowright import personas as _p

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
    from octowright import personas as _p

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
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt (for scripts).")
def persona_delete_cmd(name: str, yes: bool) -> None:
    """Delete an entire persona (all engines + metadata).

    Irreversible, and what it destroys is not regenerable: an engine profile
    holds the live session cookies, localStorage and IndexedDB for every site
    that persona logged into. `octowright cleanup` already defaults to a
    dry-run for recordings, which regenerate; this deletes credentials, so it
    confirms first.

    This command cannot tell whether a live browser currently has the profile
    open -- the CLI is a separate process from the daemon with no access to
    the pool, the same reason `octowright cleanup` does not prune profiles.
    The `persona_delete` MCP tool can check, and refuses while a session holds
    the persona; prefer it when the daemon is running.
    """
    from octowright.engine_profiles import delete_persona
    from octowright.personas import persona_dir

    setup_telemetry()
    try:
        # Existence is deliberately NOT pre-checked here: `delete_persona` already
        # raises with the canonical message, and duplicating that check would give
        # the same condition two error texts that can drift apart. The prompt is
        # simply skipped when there is nothing to describe.
        target = persona_dir(name)
        if not yes and target.exists():
            engines = sorted(child.name for child in target.iterdir() if child.is_dir())
            click.echo(f"About to permanently delete persona {name!r}:")
            click.echo(f"  {target}")
            if engines:
                click.echo(f"  engine profiles: {', '.join(engines)}")
                click.echo("  this includes saved logins (cookies, localStorage, IndexedDB)")
            click.echo("  a live browser using this persona cannot be detected from the CLI")
            click.confirm("Delete it?", abort=True)

        path = delete_persona(name)
        click.echo(f"deleted {path}")
    finally:
        shutdown_telemetry()
