# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright takeover`` — detect & disable competing Playwright MCP plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli


def _takeover_default_project_config() -> Path:
    """Indirection so tests can monkeypatch the resolved project path."""
    return Path.cwd() / ".mcp.json"


def _takeover_default_global_config() -> Path:
    """Indirection so tests can monkeypatch the resolved global path."""
    return Path.home() / ".claude.json"


def _takeover_render_findings(detections: list[Any]) -> None:
    if not detections:
        click.echo("No competing playwright MCP plugins detected — octowright is already the one.")
        return
    from octowright import takeover as _t

    click.echo(_t.summarise(detections))
    for d in detections:
        click.echo(f"  [{d.scope:7s}] {d.server_name}  ({d.config_path})")
        click.echo(f"             reason: {d.reason}")
        if d.command:
            click.echo(f"             command: {d.command}")


def _takeover_apply_one(detection: Any, *, backup: bool) -> None:
    from octowright import takeover as _t

    result = _t.apply_takeover(detection, backup=backup)
    if not result.get("disabled"):
        click.echo(
            f"  FAILED [{detection.scope}] {detection.server_name}: {result.get('error', 'unknown error')}",
            err=True,
        )
        return
    click.echo(
        f"  disabled [{detection.scope}] {detection.server_name} -> {result['new_key_name']} in {result['config_path']}"
    )
    if result.get("backup_path"):
        click.echo(f"             backup: {result['backup_path']}")
    click.echo(
        f"             to re-enable: rename `{result['new_key_name']}` back to "
        f"`{detection.server_name}` in {result['config_path']}"
    )


@cli.command()
@click.option("--apply", "do_apply", is_flag=True, help="Actually modify config files (default = check only).")
@click.option(
    "--scope",
    type=click.Choice(["session", "project", "global"]),
    default=None,
    help="Where to apply (required with --apply, or chosen interactively).",
)
@click.option("--name", default=None, help="Specific server name to disable (default = all detected).")
@click.option("--no-backup", is_flag=True, help="Skip the .bak file (not recommended).")
def takeover(do_apply: bool, scope: str | None, name: str | None, no_backup: bool) -> None:
    """Detect competing Playwright MCP plugins and optionally take over."""
    from octowright import takeover as _t

    setup_telemetry()
    try:
        project_cfg = _takeover_default_project_config()
        global_cfg = _takeover_default_global_config()

        detections = _t.detect_competing_servers(
            project_config=project_cfg,
            global_config=global_cfg,
        )

        if not do_apply:
            _takeover_render_findings(detections)
            if detections:
                click.echo("")
                click.echo(
                    "Re-run with `--apply --scope={session,project,global}` to disable them, "
                    "or just `--apply` to choose interactively."
                )
            return

        if not detections:
            click.echo("Nothing to take over — no competing plugins detected.")
            return

        # --apply path: pick a scope.
        if scope is None:
            choice = click.prompt(
                "Take over for which scope? (s)ession / (p)roject / (g)lobal / (c)ancel",
                type=click.Choice(["s", "p", "g", "c"]),
                default="c",
                show_choices=False,
            )
            resolved = {"s": "session", "p": "project", "g": "global", "c": "cancel"}[choice]
            if resolved == "cancel":
                click.echo("cancelled")
                return
            scope = resolved

        if scope == "session":
            click.echo(
                "session-only takeover acknowledged; no config changes. "
                "octowright will take precedence for THIS conversation only — "
                "tell the assistant to prefer octowright tools when both are available."
            )
            return

        # project / global: filter detections + apply each.
        targets = [d for d in detections if d.scope == scope]
        if name is not None:
            targets = [d for d in targets if d.server_name == name]
        if not targets:
            if name:
                click.echo(f"No matching detections in {scope} for name={name!r}.")
            else:
                click.echo(f"No matching detections in {scope}.")
            return

        click.echo(f"applying takeover ({scope}, {len(targets)} entr{'y' if len(targets) == 1 else 'ies'}):")
        for d in targets:
            _takeover_apply_one(d, backup=not no_backup)
    finally:
        shutdown_telemetry()
