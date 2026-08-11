# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared helpers for HTTP route handlers (body parsing, pagination, etc.)."""

from __future__ import annotations

import json
import os
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

# Falsey tokens that keep an OCTOWRIGHT_* byte-limit knob OFF.
_OFF_TOKENS = {"", "0", "off", "never", "none", "disabled", "false", "no"}


def _max_request_body_bytes() -> int:
    """``OCTOWRIGHT_MAX_REQUEST_BODY_BYTES`` — route-level request-body ceiling.

    OFF (returns 0) by default for back-compat. A positive byte count rejects
    larger JSON bodies with 413 before they are fully materialized. A
    non-positive / falsey / unparsable value keeps it off.
    """
    raw = os.environ.get("OCTOWRIGHT_MAX_REQUEST_BODY_BYTES", "").strip().lower()
    if raw in _OFF_TOKENS:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def _body_too_large(limit: int) -> JSONResponse:
    return JSONResponse(
        {"error": f"request body exceeds the {limit}-byte limit"},
        status_code=413,
    )


async def _read_body_capped(request: Request) -> tuple[bytes, JSONResponse | None]:
    """Read the raw body, enforcing ``OCTOWRIGHT_MAX_REQUEST_BODY_BYTES`` when set.

    Rejects early on an honest oversized ``Content-Length``, and streams +
    counts so a lying/absent ``Content-Length`` can't smuggle a body past the
    cap. Off by default → a plain ``await request.body()``.
    """
    limit = _max_request_body_bytes()
    if limit <= 0:
        return await request.body(), None
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                return b"", _body_too_large(limit)
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return b"", _body_too_large(limit)
        chunks.append(chunk)
    return b"".join(chunks), None


async def _read_json_body(request: Request) -> tuple[Any, JSONResponse | None]:
    """Read and JSON-decode the request body. An empty body decodes to ``{}``.

    Returns ``(payload, None)`` on success or ``(None, error_response)`` on
    decode failure (or a 413 when the body exceeds the configured cap). Empty
    bodies are treated as ``{}`` so callers that have no parameters (e.g.
    ``POST /api/scenarios/foo/start``) need not send anything.
    """
    raw, too_large = await _read_body_capped(request)
    if too_large is not None:
        return None, too_large
    if not raw:
        return {}, None
    content_type = (request.headers.get("content-type") or "").lower()
    if not content_type.startswith("application/json"):
        return None, JSONResponse(
            {"error": "content-type must be application/json for JSON request bodies"},
            status_code=415,
        )
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
