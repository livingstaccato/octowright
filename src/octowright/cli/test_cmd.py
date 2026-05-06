# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright test`` — run all test macros in a directory.

Filename note: this lives at ``test_cmd.py`` (not ``test.py``) so pytest's
test-discovery does not accidentally collect it as a test module.
"""

from __future__ import annotations

from typing import Any

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli


@cli.command()
@click.argument("macros_dir", required=False)
@click.option("--kind", default="webkit", help="Browser engine to use for tests.")
@click.option("--tag", default=None, help="Only run macros tagged with [tag].")
@click.option("--out", "out_path", default=None, help="JUnit XML output path.")
@click.option(
    "--max-parallel", default=1, type=click.IntRange(min=1), show_default=True, help="Maximum tests to run at once."
)
def test(macros_dir: str | None, kind: str, tag: str | None, out_path: str | None, max_parallel: int) -> None:
    """Run all test macros in a directory. Outputs JUnit XML."""
    import asyncio

    from octowright import runner
    from octowright.browser_pool import BrowserPool

    setup_telemetry()

    async def _run() -> dict[str, Any]:
        # Pool + suite + shutdown all share one event loop. Calling asyncio.run
        # twice creates separate loops and the playwright objects on the pool
        # can't be torn down by a fresh loop ("Event loop is closed").
        pool = BrowserPool()
        try:
            return await runner.run_suite(
                macros_dir=macros_dir,
                kind=kind,
                tag=tag,
                out_path=out_path,
                pool=pool,
                max_parallel=max_parallel,
            )
        finally:
            await pool.shutdown()

    try:
        result = asyncio.run(_run())
        click.echo(f"{result['passed']}/{result['total']} passed")
        click.echo(f"report: {result['report_path']}")
        raise SystemExit(0 if result["failed"] == 0 else 1)
    finally:
        shutdown_telemetry()
