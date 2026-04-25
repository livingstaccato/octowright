# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright init`` — scaffold the standard layout.

Filename note: this lives at ``init_cmd.py`` (not ``init.py``) to avoid
shadowing the package's own ``__init__.py`` during import resolution.
"""

from __future__ import annotations

import click
from provide.telemetry import setup_telemetry, shutdown_telemetry

from ._root import cli


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing sample persona/scenario/macro files.")
def init(force: bool) -> None:
    """Scaffold the standard layout: profile/scenario/macro dirs + samples + MCP registration block."""
    from .. import scaffold
    from ..defaults import PROFILES_DIR, SCENARIOS_DIR
    from ..macros import MACROS_DIR

    setup_telemetry()
    try:
        report = scaffold.scaffold_all(
            profiles_dir=PROFILES_DIR,
            macros_dir=MACROS_DIR,
            scenarios_dir=SCENARIOS_DIR,
            force=force,
        )
        scaffold.render_report(report)
    finally:
        shutdown_telemetry()
