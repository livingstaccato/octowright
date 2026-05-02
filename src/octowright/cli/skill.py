# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import click

from ..skill_distribution import (
    SKILL_NAME,
    doctor_distributed_assets,
    install_distributed_assets,
    render_json,
    render_table,
    status_distributed_assets,
)
from ._root import cli


@cli.group()
def skill() -> None:
    """Install and inspect packaged skill assets."""


@skill.command("install")
@click.argument("name", default=SKILL_NAME)
@click.option(
    "--target",
    type=click.Choice(["codex", "claude", "all"]),
    default="all",
    show_default=True,
    help="Install destination.",
)
@click.option("--force", is_flag=True, help="Overwrite existing installed assets.")
@click.option("--dry-run", is_flag=True, help="Print planned operations without writing files.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def skill_install(name: str, target: str, force: bool, dry_run: bool, as_json: bool) -> None:
    """Install a packaged skill and plugin manifests."""
    if name != SKILL_NAME:
        raise click.ClickException(f"unknown skill {name!r}; expected {SKILL_NAME!r}")
    results = install_distributed_assets(target=target, dry_run=dry_run, force=force)
    click.echo(render_json(results) if as_json else render_table(results))


@skill.command("status")
@click.argument("name", default=SKILL_NAME)
@click.option(
    "--target",
    type=click.Choice(["codex", "claude", "all"]),
    default="all",
    show_default=True,
    help="Status destination.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def skill_status(name: str, target: str, as_json: bool) -> None:
    """Show installed-version and hash-match status for packaged skill assets."""
    if name != SKILL_NAME:
        raise click.ClickException(f"unknown skill {name!r}; expected {SKILL_NAME!r}")
    results = status_distributed_assets(target=target)
    click.echo(render_json(results) if as_json else render_table(results))


@skill.command("doctor")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON output.")
def skill_doctor(as_json: bool) -> None:
    """Run a quick diagnostics pass for distributed skill assets."""
    results = doctor_distributed_assets()
    click.echo(render_json(results) if as_json else render_table(results))
