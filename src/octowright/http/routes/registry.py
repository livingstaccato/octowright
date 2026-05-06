# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.http.routes import demos, events, health, media, meta, scenarios, sessions


def all_routes() -> list[Any]:
    routes: list[Any] = []
    routes.extend(health.routes())
    routes.extend(demos.routes())
    routes.extend(sessions.routes())
    routes.extend(events.routes())
    routes.extend(media.routes())
    routes.extend(scenarios.routes())
    routes.extend(meta.routes())
    return routes
