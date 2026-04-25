# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright migrate-profiles`` — one-shot legacy profile layout migration."""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from ._root import cli


@cli.command("migrate-profiles")
def migrate_profiles_cmd() -> None:
    """One-shot: migrate legacy profiles/<kind>/<name>/ to profiles/<name>/<kind>/."""
    from .. import personas as _p

    setup_telemetry()
    try:
        summary = _p.migrate_legacy_layout()
        click.echo(f"moved {summary['moved']} engine-profile dir(s) across {summary['personas']} persona(s)")
    finally:
        shutdown_telemetry()
