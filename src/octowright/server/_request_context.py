# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Ambient per-request context for tool wrappers.

The SDK used to publish the in-flight request through a contextvar
(``mcp.server.lowlevel.server.request_ctx``), which let a decorator read the
request's ``_meta`` and its ``ServerSession`` without the tool declaring a
``ctx`` parameter. MCP 2.0 removed that contextvar: a handler now receives its
context only by declaring an injected ``Context`` argument.

Two octowright features are deliberately ambient and must NOT leak into the
client-facing tool schema:

* the progress heartbeat (``_heartbeat``) needs the follower-injected
  ``progressToken`` plus the session to send pings on;
* idempotent dispatch (``_idempotency``) needs the request's idempotency key
  and the owning session's identity.

Adding a ``ctx`` parameter to all ~125 tools to recover that would change every
signature and risk the argument surfacing to clients. Instead we re-establish
the contextvar ourselves from a ``ServerMiddleware``, which the SDK runs around
every request with the full ``ServerRequestContext`` in hand.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Any

_request_ctx: ContextVar[Any] = ContextVar("octowright_request_ctx")


def current_request_context() -> Any:
    """The in-flight ``ServerRequestContext``, or None outside a request."""
    return _request_ctx.get(None)


def current_request_meta() -> Any:
    """The in-flight request's ``_meta``, or None when absent."""
    ctx = current_request_context()
    return None if ctx is None else getattr(ctx, "meta", None)


def current_session() -> Any:
    """The in-flight request's ``ServerSession``, or None outside a request."""
    ctx = current_request_context()
    return None if ctx is None else getattr(ctx, "session", None)


def current_meta_value(*keys: str) -> Any:
    """First present value among ``keys`` in the request's ``_meta``.

    MCP 2.0 made ``_meta`` a plain dict (``RequestParamsMeta`` is a TypedDict),
    where 1.x handed over a pydantic model — so the spec-defined fields are now
    snake_case dict keys (``progress_token``, not ``progressToken``) and
    non-spec keys survive verbatim instead of landing in ``model_extra``.
    Reading it the old way returns None on every request, which would silently
    disable the progress heartbeat and idempotent dispatch rather than fail
    loudly, so several spellings are accepted and attribute access is kept as a
    fallback for any SDK that hands back an object.
    """
    meta = current_request_meta()
    if meta is None:
        return None
    for key in keys:
        if isinstance(meta, Mapping):
            if key in meta:
                return meta[key]
            continue
        value = getattr(meta, key, None)
        if value is not None:
            return value
        extra = getattr(meta, "model_extra", None) or {}
        if key in extra:
            return extra[key]
    return None


class RequestContextMiddleware:
    """Publish each request's context so ambient tool wrappers can read it.

    Implements the SDK's ``ServerMiddleware`` protocol. The token is reset in a
    ``finally`` so a nested or concurrent request can never inherit a stale
    context.
    """

    async def __call__(self, ctx: Any, call_next: Callable[[Any], Awaitable[Any]]) -> Any:
        token = _request_ctx.set(ctx)
        try:
            return await call_next(ctx)
        finally:
            _request_ctx.reset(token)


__all__ = [
    "RequestContextMiddleware",
    "current_meta_value",
    "current_request_context",
    "current_request_meta",
    "current_session",
]
