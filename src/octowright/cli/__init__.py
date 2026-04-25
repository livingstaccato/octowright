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
from . import cleanup as _cleanup  # noqa: F401
from . import init_cmd as _init_cmd  # noqa: F401
from . import migrate as _migrate  # noqa: F401
from . import persona as _persona  # noqa: F401
from . import scenario as _scenario  # noqa: F401
from . import selftest as _selftest  # noqa: F401
from . import serve as _serve  # noqa: F401
from . import takeover as _takeover_cmd  # noqa: F401
from . import test_cmd as _test_cmd  # noqa: F401
from ._root import cli, main

# `_format_watch_event` is re-exported because `tests/test_cli.py` imports it
# directly via `from octowright.cli import _format_watch_event`.
from .watch import _format_watch_event

__all__ = [
    "_format_watch_event",
    "cli",
    "main",
]
