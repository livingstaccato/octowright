# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Root click group for the octowright CLI.

Subcommand modules import ``cli`` from here and decorate themselves against
it so they appear as ``octowright <subcommand>``.
"""

from __future__ import annotations

import logging

import click

# `provide.telemetry._otel.has_otel()` emits a `_logger.debug` whenever the
# probe finds opentelemetry isn't installed. On runner profiles where the
# default logging level resolves to DEBUG (we've seen this on linux arm64
# GH-Actions), that diagnostic message leaks into the CLI's stdout/stderr
# and pollutes output that callers parse (e.g. `scenario list`). Pinning
# the specific module logger to WARNING silences the probe's diagnostic
# without dropping anything semantically meaningful — `has_otel()`'s
# decision is reflected in the return value, not in the log line.
logging.getLogger("provide.telemetry._otel").setLevel(logging.WARNING)


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def cli(ctx: click.Context) -> None:
    """octowright — MCP server that drives multiple headed Playwright browsers in parallel."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


def main() -> None:
    # click handles its own SystemExit when standalone_mode=True; this never returns normally.
    cli.main(standalone_mode=True)
