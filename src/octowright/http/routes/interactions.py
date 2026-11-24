# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Guarded, session-ID-only interaction sidecar reader."""

from __future__ import annotations

import asyncio
import re

from starlette.requests import Request
from starlette.routing import Route

from octowright.http.discovery import _resolve_log_path
from octowright.http.exposure import guard_sensitive_http
from octowright.http.json_response import SafeJSONResponse
from octowright.interaction_storage import read_interactions


async def session_interactions(request: Request) -> SafeJSONResponse:
    sid = request.path_params["id"]
    if re.fullmatch(r"[A-Za-z0-9_]{1,64}", sid) is None:
        return SafeJSONResponse({"error": "unknown session"}, status_code=404)
    log_path = _resolve_log_path(sid)
    if log_path is None:
        return SafeJSONResponse({"error": "unknown session"}, status_code=404)
    try:
        cursor = int(request.query_params.get("cursor", "0"))
        limit = int(request.query_params.get("limit", "1000"))
        payload = await asyncio.to_thread(read_interactions, log_path, cursor=cursor, limit=limit)
    except ValueError:
        return SafeJSONResponse({"error": "invalid interaction pagination or sidecar"}, status_code=400)
    except OSError:
        return SafeJSONResponse({"error": "interaction sidecar unavailable"}, status_code=400)
    return SafeJSONResponse(payload)


def routes() -> list[Route]:
    return [Route("/api/sessions/{id}/interactions", guard_sensitive_http(session_interactions), methods=["GET"])]
