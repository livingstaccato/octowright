# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Marker + availability gate for the optional terminal-session suite, plus the
shared plugin-registration fixture the MCP-tool tests need.

Concerns, handled here so individual test files stay clean:

* **User-config isolation** — this suite is not under ``tests/``, so it inherits
  none of the core suite's conftest and used to resolve plugin enablement (and
  every other user-config path) out of the developer's real
  ``~/.config/octowright``. Fixed at module scope rather than in a hook, for
  reasons that only make sense next to the code; see the comment block below.
* **Marker** — every test in this directory gets the registered ``terminal``
  marker (see the root ``pyproject.toml``), so ``-m terminal`` selects the suite
  and ``-m 'not terminal'`` excludes it.
* **Availability** — these tests import uterm-backed modules directly, so where
  ``provide-uterm`` is absent (a core install, or any checkout that has not
  synced the ``terminal`` dependency group) they would error at *collection* rather than
  skip. A marker can't prevent that — markers are applied during collection,
  which is where the import fails — so we ignore the directory entirely instead.
  Note what that costs: pytest then reports a clean pass over ZERO tests, which
  is why CI asserts uterm is importable before running this suite rather than
  trusting a green check (see ``ci/run_terminal_plugin_tests.sh``).

  There are **two** ways this suite can be unrunnable and they need different
  handling. ``is_available()`` answers "uterm is missing", which presumes
  ``octowright_terminal`` itself imported — and after the extraction that
  package is *absent* on a core install and in every core CI leg
  (``--all-groups --no-group terminal``). Importing it unconditionally to ask
  the question is therefore the very failure the question exists to avoid: a
  bare ``pytest`` at the repo root (no ``testpaths`` is declared, so it walks
  into this directory) died with an ``ImportError`` in this file. Both imports
  are guarded, and either absence ignores the directory the same way.
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

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# --- user-config isolation, before any octowright import ---------------------
#
# Duplicated from the core suite's ``tests/conftest.py``, which does the same
# thing and carries the full reasoning. Nothing was factored out because there
# is no seam that would not be a third file neither suite owns: the two are
# different distributions run by different commands (``pytest tests/`` against
# ``ci/run_terminal_plugin_tests.sh``), and ``tests/conftest.py`` governs
# ``tests/`` only -- it is not an ancestor of this directory, so this suite
# inherited none of it and read the developer's real config instead.
#
# The PLACEMENT differs from core's, and that difference is the load-bearing
# part. Core does this in ``pytest_configure``; this file cannot. pytest imports
# an argument directory's conftest BEFORE calling ``pytest_configure``
# (measured), and ``from octowright.server import plugin_state`` below pulls in
# ``octowright.server._state``, which resolves
# ``plugin_discovery.enabled_names()`` into a process-wide singleton at ITS
# import time. By the time the hook fired, the answer would already be fixed --
# read out of the developer's real ``~/.config/octowright/plugins.yaml``.
# Verified on a machine with the terminal plugin enabled (a documented,
# supported setup): importing ``octowright_terminal.tools`` there resolves
# ``_enabled_plugins == ['terminal']``, so this suite behaved differently than
# it does anywhere else, in the one direction hardest to notice -- enabled is
# what these tests want, so it passed for the wrong reason.
#
# Reach is the whole user-config tree, not just plugins.yaml: XDG_CONFIG_HOME
# (APPDATA on Windows) also relocates PROFILES_DIR, SCENARIOS_DIR, GOLDENS_DIR,
# MACROS_DIR, UPLOAD_STAGING_DIR and ADVISOR_STATE_PATH. Profiles hold live
# session cookies, so pointing them at a throwaway dir is the point, not a
# side effect.
os.environ.pop("OCTOWRIGHT_PLUGINS", None)
_TEST_CONFIG_HOME = tempfile.mkdtemp(prefix="octowright-terminal-test-config-")
os.environ["XDG_CONFIG_HOME"] = _TEST_CONFIG_HOME
os.environ["APPDATA"] = _TEST_CONFIG_HOME

# Cleanup is ``atexit`` rather than ``pytest_unconfigure`` for the same reason
# the setup is at module scope: the directory exists from the moment this file
# is imported, which is BEFORE pytest guarantees any hook of ours will run. An
# import error in the lines just below aborts the session with no
# ``pytest_unconfigure`` at all -- observed while writing this, leaving exactly
# the orphan the cleanup exists to prevent. ``atexit`` still fires on that path.
#
# What neither mechanism can cover is pytest-timeout's ``thread`` method, which
# ends a wedged run with ``os._exit(1)`` (see "Test-run bounds" in CLAUDE.md):
# no atexit handler, no hook. So a wedged run still orphans its tree, and this
# only bounds the ordinary case. ``ignore_errors`` because a failed cleanup must
# never be how an otherwise-green run reports red.
atexit.register(shutil.rmtree, _TEST_CONFIG_HOME, ignore_errors=True)

from octowright.plugins.registry import PluginRegistry  # noqa: E402
from octowright.plugins.session_launch import PluginContext  # noqa: E402
from octowright.server import plugin_state  # noqa: E402

_HERE = Path(__file__).parent

try:
    from octowright_terminal.availability import is_available
    from octowright_terminal.plugin import plugin as terminal_plugin
except ImportError:  # the plugin distribution itself is not installed
    terminal_plugin = None
    collect_ignore_glob = ["test_*.py"]
else:
    if not is_available():  # installed, but uterm is not
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
