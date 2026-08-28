# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright cleanup`` — prune old recordings/screenshots/videos/traces."""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli


@cli.command()
@click.option("--days", default=30.0, type=float, help="Files older than this many days are eligible.")
@click.option("--apply", is_flag=True, help="Actually delete (default is dry-run).")
@click.option(
    "--browsers",
    is_flag=True,
    help=(
        "Also reap stray playwright-managed browser processes "
        "(ms-playwright/{chromium,firefox,webkit}) left behind by crashed "
        "daemons or recording scripts. Affects every octowright session on "
        "this machine, not just yours."
    ),
)
def cleanup(days: float, apply: bool, browsers: bool) -> None:
    """Prune old recordings/screenshots/videos/traces under RECORDINGS_DIR.

    Macro artifacts live under the same root and are never pruned: they are
    curated rather than incidental, so age does not make them disposable.

    Pass ``--browsers`` to also reap orphaned playwright browser processes
    so they don't pile up in the Dock between sessions.
    """
    from octowright import recording_cleanup as _rc
    from octowright.defaults import RECORDINGS_DIR

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

        if browsers:
            _report_browser_reap(apply=apply)
    finally:
        shutdown_telemetry()


def _report_browser_reap(*, apply: bool) -> None:
    """Run the all-scope browser reap and echo a per-stage summary."""
    from octowright.process_reaper import reap_orphan_browsers

    click.echo("")
    click.echo("browsers: scanning for stray ms-playwright/* processes")
    summary = reap_orphan_browsers(scope="all", dry_run=not apply)
    if apply:
        click.echo(f"  killed:      {len(summary['killed']):4d} process(es) {summary['killed']}")
        if summary["still_alive"]:
            click.echo(f"  still alive: {len(summary['still_alive']):4d} {summary['still_alive']}", err=True)
        for err in summary["errors"]:
            click.echo(f"    pid={err['pid']} stage={err['stage']}: {err['error']}", err=True)
        return
    click.echo(f"  would kill:  {len(summary['still_alive']):4d} process(es) {summary['still_alive']}")
    click.echo("  (dry-run, pass --apply to actually kill)")
