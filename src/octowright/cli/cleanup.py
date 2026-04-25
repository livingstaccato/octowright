# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright cleanup`` — prune old recordings/screenshots/videos/traces."""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from ._root import cli


@cli.command()
@click.option("--days", default=30.0, type=float, help="Files older than this many days are eligible.")
@click.option("--apply", is_flag=True, help="Actually delete (default is dry-run).")
def cleanup(days: float, apply: bool) -> None:
    """Prune old recordings/screenshots/videos/traces under RECORDINGS_DIR."""
    from .. import recording_cleanup as _rc
    from ..defaults import RECORDINGS_DIR

    setup_telemetry()
    try:
        stale = _rc.find_stale_files(RECORDINGS_DIR, days)
        summary = _rc.cleanup_stale(stale, dry_run=not apply)

        kinds = ("recording", "screenshot", "video", "trace", "other")
        by_kind_count: dict[str, int] = {k: 0 for k in kinds}
        by_kind_bytes: dict[str, int] = {k: 0 for k in kinds}
        for s in stale:
            by_kind_count[s.kind] += 1
            by_kind_bytes[s.kind] += s.size_bytes
        total_bytes = sum(s.size_bytes for s in stale)

        click.echo(f"recordings dir: {RECORDINGS_DIR}")
        click.echo(f"cutoff: files older than {days} day(s)")
        click.echo(f"found {len(stale)} file(s), {total_bytes} byte(s) total")
        for k in kinds:
            click.echo(f"  {k:12s} {by_kind_count[k]:6d} files  {by_kind_bytes[k]:12d} bytes")
        if apply:
            click.echo(f"removed {summary['removed_count']} file(s), freed {summary['removed_bytes']} byte(s)")
            if summary["errors"]:
                click.echo(f"{len(summary['errors'])} error(s):", err=True)
                for err in summary["errors"]:
                    click.echo(f"  {err['path']}: {err['error']}", err=True)
        else:
            click.echo("(dry-run, pass --apply to actually delete)")
    finally:
        shutdown_telemetry()
