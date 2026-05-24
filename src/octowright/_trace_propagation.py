# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""W3C trace-context propagation across the follower→leader HTTP-MCP bridge.

The follower's ``BridgeSupervisor`` calls ``streamablehttp_client(...)`` which
opens an httpx ``AsyncClient`` under the hood. Without help, OTel spans on the
follower side and on the leader side appear as two disconnected trees — same
``service.name`` only correlates by timestamp.

This module adds two seams so the two sides chain via the W3C ``traceparent``
header:

* :func:`tracing_httpx_client_factory` — returns an ``McpHttpClientFactory``
  that installs a request event hook on the httpx client. Every outbound
  request gets its current OTel context injected (``opentelemetry.propagate``).
* :class:`TraceContextExtractionMiddleware` — ASGI middleware to wrap the
  leader's ``/mcp`` mount. Extracts the W3C context from incoming headers and
  attaches it for the duration of the request, so any span the leader opens
  (per-RPC spans on the FastMCP side, per-tool spans inside ``@mcp.tool``
  handlers) becomes a child of the follower's span.

Both are safe when OTel is not installed — ``propagate`` is a stdlib import in
``opentelemetry.api`` which ships with the OTel SDK extra; without the extra
the module gracefully no-ops.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx

from octowright._tracing import _tracer as _tracing_get_tracer

try:
    from opentelemetry import context as _otel_context
    from opentelemetry import propagate as _otel_propagate

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - OTel SDK is a soft dep
    _OTEL_AVAILABLE = False


async def _inject_traceparent_hook(request: httpx.Request) -> None:
    """httpx event hook: inject the current OTel context into request headers."""
    if not _OTEL_AVAILABLE:
        return
    # propagate.inject mutates the carrier in place; httpx Headers is dict-like
    # enough for the default W3CTraceContextPropagator (which only does setdefault).
    try:
        _otel_propagate.inject(request.headers)
    except Exception:
        # Telemetry must never break a transport. Drop the propagation rather
        # than fail the outbound request.
        pass


def tracing_httpx_client_factory() -> Callable[..., httpx.AsyncClient]:
    """Return an MCP httpx-client factory whose clients inject W3C traceparent.

    Drop-in replacement for ``mcp.shared._httpx_utils.create_mcp_http_client``
    matching its signature so callers can pass it as ``httpx_client_factory=``
    to ``streamablehttp_client``.
    """

    def _factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        # Mirror create_mcp_http_client's defaults: follow_redirects=True, 30s timeout.
        kwargs: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": timeout if timeout is not None else httpx.Timeout(30.0),
            "event_hooks": {"request": [_inject_traceparent_hook]},
        }
        if headers is not None:
            kwargs["headers"] = headers
        if auth is not None:
            kwargs["auth"] = auth
        return httpx.AsyncClient(**kwargs)

    return _factory


# ASGI middleware (Starlette-compatible) for the leader side.


class TraceContextExtractionMiddleware:
    """ASGI middleware that extracts W3C trace context from incoming requests.

    Attaches the extracted context for the duration of ``__call__`` so any
    span the wrapped app opens chains under the upstream span. No-ops when
    OTel is unavailable.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        if not _OTEL_AVAILABLE or scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        # W3C trace context allows ``tracestate`` to appear as multiple
        # headers — per spec they must be combined into a single
        # comma-separated string. A plain dict comprehension would drop
        # all but the last. Comma-join collisions instead so the
        # propagator sees the full carrier.
        carrier: dict[str, str] = {}
        for raw_k, raw_v in scope.get("headers", []):
            name = raw_k.decode("latin-1")
            value = raw_v.decode("latin-1")
            if name in carrier:
                carrier[name] = f"{carrier[name]}, {value}"
            else:
                carrier[name] = value
        try:
            ctx = _otel_propagate.extract(carrier)
        except Exception:
            ctx = None
        if ctx is None:
            await self.app(scope, receive, send)
            return
        token = _otel_context.attach(ctx)
        # Emit a per-request leader-side span so even when no @mcp.tool
        # runs (initialize, ping, malformed body) the trace still shows
        # a leader leg — proves the chain at the HTTP layer and gives
        # operators a visible "request landed here" anchor.
        #
        # The span MUST end as soon as response headers are sent
        # (``http.response.start``) — never on body completion. For
        # streamable-HTTP MCP the response is an SSE stream that stays
        # open for the duration of the follower's connection (potentially
        # minutes). Keeping the span open that long fills the OTel
        # batch-exporter buffer (default 2048 spans) with one entry per
        # concurrent follower and silently drops spans when it overflows.
        tracer = _tracing_get_tracer("octowright")
        span_cm = tracer.start_as_current_span("octowright.mcp.request")
        sp = span_cm.__enter__()
        try:
            sp.set_attribute("method", scope.get("method") or "")
            sp.set_attribute("path", scope.get("path") or "")
        except Exception:
            pass
        span_ended = False

        def _end_span_once() -> None:
            nonlocal span_ended
            if span_ended:
                return
            span_ended = True
            try:
                span_cm.__exit__(None, None, None)
            except Exception:
                pass

        async def _send_wrapper(message: MutableMapping[str, Any]) -> None:
            if not span_ended and message.get("type") == "http.response.start":
                _end_span_once()
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        finally:
            # End the span if no http.response.start was ever sent (e.g.
            # the app raised before producing a response). The OTel context
            # token outlives the span on purpose so any background work
            # the app dispatched still chains correctly.
            _end_span_once()
            _otel_context.detach(token)
