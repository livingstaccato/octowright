# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Plugin registry accessor for plugin tool modules.

A plugin's ``@mcp.tool`` functions need their pool at call time, but they are
imported *before* ``create_pool`` runs (the loader registers tools first so a
tool failure never has to tear a pool down). So they look the pool up through
this seam instead of closing over it.

The registry itself lives in :mod:`octowright.plugins.state`, not here.
Importing ``octowright.server`` runs its ``__init__``, which imports every tool
submodule to trigger registration -- Playwright included -- so a reader below
the tool layer (``scenario_kinds``, reached from ``_validate_participant_kind``)
could not use this module without paying for all of it.

These are re-exported *functions*, not a copied value: ``set_registry`` rebinds
the global in ``plugins.state`` and ``registry`` reads it there, so this path
and the direct one always agree. Rebinding a name here instead would give the
two callers different registries -- the exact bug this seam exists to prevent.
"""

from __future__ import annotations

from octowright.plugins.state import pool_for, registry, set_registry

__all__ = ["pool_for", "registry", "set_registry"]
