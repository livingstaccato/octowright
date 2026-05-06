# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Root click group for the octowright CLI.

Subcommand modules import ``cli`` from here and decorate themselves against
it so they appear as ``octowright <subcommand>``.
"""

from __future__ import annotations

import click


@click.group(invoke_without_command=True, context_settings={"help_option_names": ["-h", "--help"]})
@click.pass_context
def cli(ctx: click.Context) -> None:
    """octowright — MCP server that drives multiple headed Playwright browsers in parallel."""
    if ctx.invoked_subcommand is None:
        # Late import to avoid a circular: serve.py imports `cli` from here
        # at module import time, so we can't import serve at module top-level.
        from octowright.cli.serve import serve

        ctx.invoke(serve)


def main() -> None:
    # click handles its own SystemExit when standalone_mode=True; this never returns normally.
    cli.main(standalone_mode=True)
