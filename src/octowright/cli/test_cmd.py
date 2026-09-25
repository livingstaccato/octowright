# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright test`` — run all test macros in a directory.

Filename note: this lives at ``test_cmd.py`` (not ``test.py``) so pytest's
test-discovery does not accidentally collect it as a test module.
"""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli
from octowright.mcp_types import TestSuiteResult


@cli.command()
@click.option("--kind", default="webkit", help="Browser engine to use for tests.")
@click.option("--tag", default=None, help="Only run macros tagged with [tag].")
@click.option("--out", "out_path", default=None, help="JUnit XML output path.")
@click.option(
    "--max-parallel", default=1, type=click.IntRange(min=1), show_default=True, help="Maximum tests to run at once."
)
@click.option("--persona", default=None, help="Launch every browser as this persona (profile, trust, credentials).")
@click.option(
    "--sequence",
    "sequence_path",
    default=None,
    type=click.Path(dir_okay=False, exists=True),
    help="Run this sequence file in one browser instead of discovering [test] macros.",
)
@click.option(
    "--artifacts",
    "artifacts_dir",
    default=None,
    type=click.Path(file_okay=False),
    help='Directory under OCTOWRIGHT_RECORDINGS for {"artifact": ...} arguments and the report.',
)
@click.option(
    "--redact-errors",
    is_flag=True,
    default=False,
    help="Record failures as macro, step and action only; never exception text.",
)
def test(
    kind: str,
    tag: str | None,
    out_path: str | None,
    max_parallel: int,
    persona: str | None,
    sequence_path: str | None,
    artifacts_dir: str | None,
    redact_errors: bool,
) -> None:
    """Run all `[test]`-tagged macros from MACROS_DIR, or one sequence file. Outputs JUnit XML."""
    import asyncio
    from pathlib import Path

    from octowright import runner
    from octowright.browser_pool import BrowserPool
    from octowright.sequences import SequenceError

    if sequence_path and tag:
        raise click.UsageError("--sequence and --tag are exclusive")
    artifacts = Path(artifacts_dir) if artifacts_dir else None
    if artifacts is not None and out_path is None:
        out_path = str(artifacts / "octowright-report.xml")

    setup_telemetry()

    async def _run() -> TestSuiteResult:
        # Pool + suite + shutdown all share one event loop. Calling asyncio.run
        # twice creates separate loops and the playwright objects on the pool
        # can't be torn down by a fresh loop ("Event loop is closed").
        pool = BrowserPool()
        try:
            if sequence_path:
                return await runner.run_sequence_file(
                    sequence=Path(sequence_path),
                    kind=kind,
                    persona=persona,
                    artifacts=artifacts,
                    redact_errors=redact_errors,
                    out_path=out_path,
                    pool=pool,
                )
            return await runner.run_suite(
                kind=kind,
                tag=tag,
                out_path=out_path,
                pool=pool,
                max_parallel=max_parallel,
                persona=persona,
                redact_errors=redact_errors,
            )
        finally:
            await pool.shutdown()

    try:
        try:
            result = asyncio.run(_run())
        except SequenceError as exc:
            # Sequence errors name the step and argument, never a resolved value.
            click.echo(f"sequence refused: {exc}", err=True)
            raise SystemExit(1) from None
        except Exception as exc:
            if not redact_errors:
                raise
            click.echo(f"test run failed: {runner.redact_error(exc)}", err=True)
            raise SystemExit(1) from None
        click.echo(f"{result['passed']}/{result['total']} passed")
        click.echo(f"report: {result['report_path']}")
        raise SystemExit(0 if result["failed"] == 0 else 1)
    finally:
        shutdown_telemetry()
