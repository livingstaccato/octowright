# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Route assembly: each handler module exposes ``routes()``; we concatenate."""

from __future__ import annotations

from typing import Any

from . import events, health, media, meta, scenarios, sessions


def all_routes() -> list[Any]:
    """Return the full route list (Starlette Route + WebSocketRoute objects).

    Order is mostly cosmetic since Starlette uses path matching, but
    ``/api/sessions`` (collection) comes before ``/api/sessions/{id}`` so
    debugging is easier when reading the bound list.
    """
    routes: list[Any] = []
    routes.extend(health.routes())
    routes.extend(sessions.routes())
    routes.extend(events.routes())
    routes.extend(media.routes())
    routes.extend(scenarios.routes())
    routes.extend(meta.routes())
    return routes


__all__ = ["all_routes"]
