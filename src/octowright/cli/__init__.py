# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``octowright`` CLI package.

Subcommand modules register themselves against the click group defined in
``_root`` via ``@cli.command``/``@cli.group`` decorators applied at import
time. Importing this package is enough to make every subcommand reachable.

The console-script entry point declared in pyproject.toml is
``octowright.cli:main``.
"""

from __future__ import annotations

# Subcommand imports trigger @cli.command / @cli.group registration via
# decorator side effects. Order does not matter; F401 ignored intentionally.
from octowright.cli import cleanup as _cleanup  # noqa: F401
from octowright.cli import init_cmd as _init_cmd  # noqa: F401
from octowright.cli import persona as _persona  # noqa: F401
from octowright.cli import scenario as _scenario  # noqa: F401
from octowright.cli import selftest as _selftest  # noqa: F401
from octowright.cli import serve as _serve  # noqa: F401
from octowright.cli import skill as _skill  # noqa: F401
from octowright.cli import takeover as _takeover_cmd  # noqa: F401
from octowright.cli import test_cmd as _test_cmd  # noqa: F401
from octowright.cli._root import cli, main

# `_format_watch_event` is re-exported because `tests/test_cli.py` imports it
# directly via `from octowright.cli import _format_watch_event`.
from octowright.cli.watch import _format_watch_event

__all__ = [
    "_format_watch_event",
    "cli",
    "main",
]
