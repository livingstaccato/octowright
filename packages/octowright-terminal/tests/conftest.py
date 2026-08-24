# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Marker + availability gate for the optional terminal-session suite.

Two concerns, handled here so individual test files stay clean:

* **Marker** — every test under ``tests/terminal/`` gets the registered
  ``terminal`` marker (see ``pyproject.toml``), so ``-m terminal`` selects the
  suite and ``-m 'not terminal'`` excludes it.
* **Availability** — these tests import uterm-backed modules directly, so on a
  core install (no ``provide-uterm``, i.e. the ``octowright[terminal]`` extra is
  absent) they would error at *collection* rather than skip. A marker can't
  prevent that — markers are applied during collection, which is where the
  import fails — so we ignore the directory entirely instead, keeping
  ``make test`` green without the extra. With it installed, every test runs.
"""

from __future__ import annotations

from pathlib import Path

from octowright_terminal.availability import is_available

_HERE = Path(__file__).parent

if not is_available():
    collect_ignore_glob = ["test_*.py"]


def pytest_collection_modifyitems(items: list) -> None:
    """Auto-apply the ``terminal`` marker to every test collected from this dir."""
    for item in items:
        if _HERE in Path(item.fspath).parents:
            item.add_marker("terminal")
