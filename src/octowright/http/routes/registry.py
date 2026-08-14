# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.http.routes import (
    events,
    health,
    mcp_events,
    media,
    meta,
    pairing,
    scenarios,
    screencast,
    sessions,
    sessions_recording,
)


def all_routes(*, mcp_token: str = "") -> list[Any]:
    routes: list[Any] = []
    routes.extend(health.routes())
    routes.extend(pairing.routes())
    routes.extend(sessions.routes())
    routes.extend(sessions_recording.routes())
    routes.extend(events.routes())
    routes.extend(mcp_events.routes(mcp_token=mcp_token))
    routes.extend(screencast.routes())
    routes.extend(media.routes())
    routes.extend(scenarios.routes())
    routes.extend(meta.routes())
    return routes
