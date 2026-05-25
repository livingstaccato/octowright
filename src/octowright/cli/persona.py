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
def persona_delete_cmd(name: str) -> None:
    """Delete an entire persona (all engines + metadata)."""
    from octowright.engine_profiles import delete_persona

    setup_telemetry()
    try:
        path = delete_persona(name)
        click.echo(f"deleted {path}")
    finally:
        shutdown_telemetry()
