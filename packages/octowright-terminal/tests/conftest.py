# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Marker + availability gate for the optional terminal-session suite, plus the
shared plugin-registration fixture the MCP-tool tests need.

Concerns, handled here so individual test files stay clean:

* **Marker** — every test under ``tests/terminal/`` gets the registered
  ``terminal`` marker (see ``pyproject.toml``), so ``-m terminal`` selects the
  suite and ``-m 'not terminal'`` excludes it.
* **Availability** — these tests import uterm-backed modules directly, so on a
  core install (no ``provide-uterm``, i.e. the ``octowright[terminal]`` extra is
  absent) they would error at *collection* rather than skip. A marker can't
  prevent that — markers are applied during collection, which is where the
  import fails — so we ignore the directory entirely instead, keeping
  ``make test`` green without the extra. With it installed, every test runs.
* **Plugin registration** — the ``@mcp.tool`` functions in
  ``octowright_terminal.tools`` resolve their pool through
  ``plugin_state.pool_for("terminal")`` rather than a core global (see
  ``tests/plugins/reference/tools.py`` for the pattern this mirrors). The real
  daemon populates that registry via ``OCTOWRIGHT_PLUGINS`` + plugin activation
  at startup; this autouse fixture does the same thing directly for every test
  in this suite, so any test that calls a ``terminal_*`` tool (directly, or
  indirectly through ``_pool()``) gets a working pool without repeating the
  setup in each file. It is cheap for tests that don't need it: ``create_pool``
  only builds objects, no PTY is spawned until a test actually launches one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from octowright_terminal.availability import is_available
from octowright_terminal.plugin import plugin as terminal_plugin

from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from octowright.server import plugin_state

_HERE = Path(__file__).parent

if not is_available():
    collect_ignore_glob = ["test_*.py"]


def pytest_collection_modifyitems(items: list) -> None:
    """Auto-apply the ``terminal`` marker to every test collected from this dir."""
    for item in items:
        if _HERE in Path(item.fspath).parents:
            item.add_marker("terminal")


@pytest.fixture(autouse=True)
def _activated_terminal_plugin(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind="terminal", recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    pool = terminal_plugin.create_pool(ctx)
    registry.register(terminal_plugin, pool=pool, adapter=None, discovered=None)
    previous = plugin_state.registry()
    plugin_state.set_registry(registry)
    try:
        yield
    finally:
        plugin_state.set_registry(previous)
