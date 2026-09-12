# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-kind plugin API.

Core supports exactly one built-in session kind (browsers). Every other kind
arrives through this package: a third-party distribution declares an
``octowright.session_kinds`` entry point, an operator enables it by name, and
core loads it into a registry of per-kind session pools.

See **Terminal Sessions (plugin)** in ``AGENTS.md`` for the reference plugin
(``packages/octowright-terminal``), and ``OCTOWRIGHT_PLUGINS`` in
``docs/env-vars.md`` for how a daemon chooses which plugins to load.
"""

from __future__ import annotations
