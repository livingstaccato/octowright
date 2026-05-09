# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright selftest`` — list registered MCP tools."""

from __future__ import annotations

import os

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli
from octowright.server import recordings_dir, registered_tool_names
from octowright.server.profiles import active_filter


@cli.command()
def selftest() -> None:
    """List registered tools and exit."""
    setup_telemetry()
    try:
        names = registered_tool_names()
        click.echo(f"recordings dir: {recordings_dir()}")
        raw_profile = os.environ.get("OCTOWRIGHT_PROFILE", "").strip()
        if active_filter() is None:
            click.echo(f"active profile: {raw_profile or 'all'} (no filter; full tool surface)")
        else:
            click.echo(f"active profile: {raw_profile} (filter active)")
        click.echo(f"{len(names)} tools registered:")
        for name in names:
            click.echo(f"  - {name}")
    finally:
        shutdown_telemetry()
