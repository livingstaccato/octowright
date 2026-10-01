# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright test`` — run all test macros in a directory.

Filename note: this lives at ``test_cmd.py`` (not ``test.py``) so pytest's
test-discovery does not accidentally collect it as a test module.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli
from octowright.mcp_types import TestSuiteResult


@cli.command()
@click.option("--kind", default="webkit", help="Browser engine to use for tests.")
@click.option("--tag", default=None, help="Only run macros tagged with [tag].")
@click.option(
    "--out",
    "out_path",
    default=None,
    help=(
        "JUnit XML output path; must sit under OCTOWRIGHT_RECORDINGS. Default: <artifacts>/octowright-report.xml "
        "with --artifacts, else a timestamped file directly under OCTOWRIGHT_RECORDINGS."
    ),
)
@click.option(
    "--max-parallel",
    default=1,
    type=click.IntRange(min=1),
    show_default=True,
    help="Maximum tests to run at once. Must be 1 with --persona.",
)
@click.option(
    "--persona",
    default=None,
    help="Launch every browser as this persona (profile, trust, credentials). Runs tests one at a time.",
)
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
@click.option(
    "--record-video",
    is_flag=True,
    default=False,
    help=(
        "Record each browser and print 'video: <path>' per video, failed runs included. With --artifacts, "
        "copied there as <sequence-stem>.webm (or <macro>.webm); later pages get -2, -3, ..."
    ),
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
    record_video: bool,
) -> None:
    """Run all `[test]`-tagged macros from MACROS_DIR, or one sequence file. Outputs JUnit XML."""
    from octowright import runner
    from octowright.browser_pool import BrowserPool

    if sequence_path and tag:
        raise click.UsageError("--sequence and --tag are exclusive")
    if persona and max_parallel > 1:
        # Every test would open the persona's one persistent profile; a second
        # browser on an open profile fails (Chromium's SingletonLock).
        raise click.UsageError("--persona runs tests one at a time; drop --max-parallel or set it to 1")
    artifacts = Path(artifacts_dir) if artifacts_dir else None
    if artifacts is not None and out_path is None:
        out_path = str(artifacts / "octowright-report.xml")
    # Filled by the runner as each browser closes -- also on a run that raises,
    # which is why it is a list the runner appends to rather than a result key.
    videos: list[Path] | None = [] if record_video else None
    # Only with --record-video: a suite uses --artifacts for nothing else, so
    # without the flag its call is exactly what it always was.
    suite_video_kwargs: dict[str, Any] = {"artifacts": artifacts, "videos": videos} if record_video else {}

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
                    videos=videos,
                )
            return await runner.run_suite(
                kind=kind,
                tag=tag,
                out_path=out_path,
                pool=pool,
                max_parallel=max_parallel,
                persona=persona,
                redact_errors=redact_errors,
                **suite_video_kwargs,
            )
        finally:
            await pool.shutdown()

    try:
        _run_and_report(_run, redact_errors=redact_errors, videos=videos)
    finally:
        shutdown_telemetry()


def _run_and_report(
    run: Callable[[], Coroutine[Any, Any, TestSuiteResult]], *, redact_errors: bool, videos: list[Path] | None
) -> None:
    """Run the suite and print its summary, report and video lines; exits the process."""
    import asyncio

    from octowright import runner
    from octowright.request_errors import InvalidRequestError
    from octowright.sequences import SequenceError

    try:
        result = asyncio.run(run())
    except SequenceError as exc:
        # Sequence errors name the step and argument, never a resolved value.
        click.echo(f"sequence refused: {exc}", err=True)
        raise SystemExit(1) from None
    except InvalidRequestError as exc:
        # A refused path (the report outside OCTOWRIGHT_RECORDINGS), raised
        # before any browser launched. The message names the path only.
        click.echo(f"test run refused: {exc}", err=True)
        raise SystemExit(1) from None
    except BaseException as exc:
        # BaseException: a Ctrl-C'd run's browser was still closed and its
        # videos collected, and those are worth printing on the way out too.
        _echo_videos(videos)
        if not redact_errors or not isinstance(exc, Exception):
            raise
        click.echo(f"test run failed: {runner.redact_error(exc)}", err=True)
        raise SystemExit(1) from None
    click.echo(f"{result['passed']}/{result['total']} passed")
    click.echo(f"report: {result['report_path']}")
    _echo_videos(videos)
    raise SystemExit(0 if result["failed"] == 0 else 1)


def _echo_videos(videos: list[Path] | None) -> None:
    for video in videos or []:
        click.echo(f"video: {video}")
