# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright doctor`` — check the machine before blaming octowright."""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from octowright.cli._root import cli
from octowright.doctor import Check

_MARK = {"ok": "PASS", "warn": "WARN", "fail": "FAIL", "skip": "SKIP"}
_COLOR = {"ok": "green", "warn": "yellow", "fail": "red", "skip": "cyan"}


@cli.command()
@click.option(
    "--skip-engines",
    is_flag=True,
    help="Skip the browser probes (they launch a real headless browser per engine).",
)
@click.option(
    "--engine-timeout",
    default=None,
    type=float,
    help="Seconds one engine probe may take before it is declared hung (default 30).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the checks as JSON instead of a table.")
@click.option(
    "--fix",
    is_flag=True,
    help="Reap orphaned Playwright drivers and browsers. Only processes whose parent is already gone.",
)
def doctor(skip_engines: bool, engine_timeout: float | None, as_json: bool, fix: bool) -> None:
    """Diagnose engines, stray processes, the daemon, and storage.

    The engine probes are the point: each drives a real headless browser
    through launch → page → goto → evaluate using raw Playwright and no
    octowright code, so a failure tells you the ENGINE is broken rather than
    sending you into octowright's launch pipeline. A local WebKit that could
    not navigate to about:blank once cost hours to find that way; this reports
    it by name in about fifteen seconds.

    Exits 1 if any check failed, so CI can gate on it.
    """
    from octowright import doctor as _doctor

    # The followers check probes /api/health over httpx, whose INFO-level
    # "HTTP Request: ..." line would otherwise land on stdout -- noise in the
    # table and FATAL under --json, where the output contract is a single
    # parseable document. Silenced here rather than in doctor.py: which streams
    # carry what is a CLI presentation concern, and a library function should
    # not mutate global logging state on its caller's behalf.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    setup_telemetry()
    try:
        checks = asyncio.run(
            _doctor.run_checks(
                engines=not skip_engines,
                engine_timeout=engine_timeout or _doctor.DEFAULT_ENGINE_TIMEOUT_SECONDS,
            )
        )
        fixed = _doctor.reap(dry_run=not fix) if fix else None

        if as_json:
            payload: dict[str, Any] = {
                "status": _doctor.worst_status(checks),
                "checks": [{"name": c.name, "status": c.status, "detail": c.detail, "data": c.data} for c in checks],
            }
            if fixed is not None:
                payload["fixed"] = fixed
            click.echo(_json.dumps(payload, indent=2, default=str))
        else:
            _render(checks)
            if fixed is not None:
                killed = len(fixed["drivers"]) + len(fixed["browsers"]["killed"])
                click.echo(f"\nreaped {killed} orphaned process(es)")

        raise SystemExit(1 if _doctor.worst_status(checks) == "fail" else 0)
    finally:
        shutdown_telemetry()


def _render(checks: list[Check]) -> None:
    width = max((len(c.name) for c in checks), default=0)
    for check in checks:
        click.echo(
            f"{click.style(_MARK[check.status], fg=_COLOR[check.status])}  {check.name.ljust(width)}  {check.detail}"
        )
