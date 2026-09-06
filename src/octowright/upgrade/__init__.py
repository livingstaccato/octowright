# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Post-upgrade "what's new" notice.

Split into a package purely so the curated highlights can live as data:
``core.py`` holds every behaviour that ``upgrade.py`` held before, and
``highlights/<version>.json`` holds the 118 titled entries, which as a Python
dict literal would be roughly 1,160 lines against the repository's 777-LOC
ceiling. This file holds ONLY re-exports and ``__all__``, never logic, so
``from octowright import upgrade`` and ``upgrade.HIGHLIGHTS`` keep working
exactly as before. A caller needing a private helper imports it from
``octowright.upgrade.core`` directly rather than growing this surface.
"""

from __future__ import annotations

from octowright.upgrade.core import (
    HIGHLIGHTS,
    HIGHLIGHTS_DIR,
    UPGRADE_STATE_PATH,
    Highlight,
    UpgradeNotice,
    announce_upgrade_if_changed,
    compute_upgrade,
    load_last_seen,
    render_banner,
    save_last_seen,
)

__all__ = [
    "HIGHLIGHTS",
    "HIGHLIGHTS_DIR",
    "UPGRADE_STATE_PATH",
    "Highlight",
    "UpgradeNotice",
    "announce_upgrade_if_changed",
    "compute_upgrade",
    "load_last_seen",
    "render_banner",
    "save_last_seen",
]
