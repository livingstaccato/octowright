# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Register optional MCP tool groups whose dependencies may be absent.

Kept out of ``server/__init__`` (which must stay an import-only export surface,
enforced by ``tests/test_macros.py::test_package_init_files_are_export_surfaces_only``)
so the availability conditional can live at module top level.
"""

from __future__ import annotations

from octowright.server._state import terminal_pool

# Terminal tools require the `octowright[terminal]` extra (uterm). When it is
# absent, terminal_pool is None and we skip the import so core never loads uterm.
# Importing the module triggers @mcp.tool registration via decorator side effects.
if terminal_pool is not None:
    from octowright.server import terminal as _terminal  # noqa: F401
