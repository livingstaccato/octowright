# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared helpers for HTTP route handlers (body parsing, pagination, etc.)."""

from __future__ import annotations

import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse


async def _read_json_body(request: Request) -> tuple[Any, JSONResponse | None]:
    """Read and JSON-decode the request body. An empty body decodes to ``{}``.

    Returns ``(payload, None)`` on success or ``(None, error_response)`` on
    decode failure. Empty bodies are treated as ``{}`` so callers that have no
    parameters (e.g. ``POST /api/scenarios/foo/start``) need not send anything.
    """
    raw = await request.body()
    if not raw:
        return {}, None
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as e:
        return None, JSONResponse(
            {"error": f"invalid JSON body: {e}"},
            status_code=400,
        )


def _parse_since(request: Request) -> tuple[int | None, JSONResponse | None]:
    """Parse the ``since`` query param. Returns (since, error_response_or_None).

    Non-int → 400. Negative ints are clamped to 0; tail_log/_paginate both
    treat negatives as ``OSError: Invalid argument`` (seek to negative
    offset) or surprising slice behavior, so we normalize at the boundary.
    """
    raw = request.query_params.get("since")
    if raw is None:
        return 0, None
    try:
        value = int(raw)
    except ValueError:
        return None, JSONResponse(
            {"error": f"invalid since={raw!r}, must be int"},
            status_code=400,
        )
    return max(0, value), None


def _paginate(items: list[dict[str, Any]], since: int) -> tuple[list[dict[str, Any]], int, int]:
    """Slice ``items[since:]`` and return (slice, next_cursor, total).

    Negative or out-of-range ``since`` is clamped into [0, total].
    """
    total = len(items)
    if since < 0:
        since = 0
    if since > total:
        since = total
    return items[since:], total, total


def _parse_bool(raw: str) -> bool | None:
    """Parse a query-string bool. Accept the usual truthy/falsy spellings."""
    s = raw.strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None
