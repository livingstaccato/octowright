# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Registry dispatch for the HTTP session routes.

Lives beside the routes rather than inside ``sessions.py`` because "resolve a
session across every registered kind" is one responsibility with its own tests,
and ``sessions.py`` is already the largest module in this package.

Core keeps no parallel session table: a plugin's ``SessionPool`` is the single
registry for its kind, so every lookup here iterates the registered pools.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from octowright.http import state


def iter_plugin_sessions() -> Iterator[Any]:
    """Yield every live session across every registered plugin pool."""
    for pool in state.plugin_registry.pools().values():
        yield from pool.iter_sessions()


def find_plugin_session(instance_id: str) -> tuple[str, Any] | None:
    """Resolve ``instance_id`` across registered pools.

    Returns ``(kind, session)`` or ``None``. Instance ids are unique across
    all pools — core enforces that at launch commit — so the first match is
    the only match.
    """
    for kind, pool in state.plugin_registry.pools().items():
        session = pool.maybe_get(instance_id)
        if session is not None:
            return kind, session
    return None
