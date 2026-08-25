# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Register optional MCP tool groups whose dependencies may be absent.

Kept out of ``server/__init__`` (which must stay an import-only export surface,
enforced by ``tests/test_macros.py::test_package_init_files_are_export_surfaces_only``)
so the availability conditional can live at module top level.

NOTE (session-kind-plugins step 5, task 4): the terminal ``@mcp.tool`` functions
now live in ``octowright_terminal.tools`` (moved out of the deleted
``octowright.server.terminal`` package) and resolve their pool through the
plugin registry (``plugin_state.pool_for("terminal")``) rather than the
``terminal_pool`` global this module still gates on. That leaves a real gap:
this import still fires whenever the extra is installed, regardless of whether
``OCTOWRIGHT_PLUGINS`` names ``terminal`` -- so the tools can register without
the plugin registry ever holding a "terminal" entry, and a call then raises
``TerminalPoolUnavailableError`` instead of working. Reconciling this gate with
real plugin activation (or retiring it in favour of ``_plugin_activation``) is
out of this task's scope -- it is the terminal-pool deletion work tracked
separately in this plan.
"""

from __future__ import annotations

from octowright.server._state import terminal_pool

# Terminal tools require the `octowright[terminal]` extra (uterm). When it is
# absent, terminal_pool is None and we skip the import so core never loads uterm.
# Importing the module triggers @mcp.tool registration via decorator side effects.
if terminal_pool is not None:
    from octowright_terminal import tools as _terminal_tools  # noqa: F401
