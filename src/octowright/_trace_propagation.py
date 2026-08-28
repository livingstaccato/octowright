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

* :func:`build_tracing_http_client` — builds the bridge's ``httpx2.AsyncClient``
  that installs a request event hook on the httpx client. Every outbound
  request gets its current OTel context injected (``opentelemetry.propagate``).
* :class:`TraceContextExtractionMiddleware` — ASGI middleware to wrap the
  leader's ``/mcp`` mount. Extracts the W3C context from incoming headers and
  attaches it for the duration of the request, so any span the leader opens
  (per-RPC spans on the server side, per-tool spans inside ``@mcp.tool``
  handlers) becomes a child of the follower's span.

Both are safe when OTel is not installed — ``propagate`` is a stdlib import in
``opentelemetry.api`` which ships with the OTel SDK extra; without the extra
the module gracefully no-ops.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx2

from octowright._tracing import get_tracer

try:
    from opentelemetry import context as _otel_context
    from opentelemetry import propagate as _otel_propagate

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - OTel SDK is a soft dep
    _OTEL_AVAILABLE = False


async def _inject_traceparent_hook(request: httpx2.Request) -> None:
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


def build_tracing_http_client(
    *,
    headers: dict[str, str] | None = None,
    on_session_id: Callable[[str], None] | None = None,
    timeout: float = 30.0,
) -> httpx2.AsyncClient:
    """Build the httpx2 client the follower bridge hands to the MCP transport.

    MCP 2.0's ``streamable_http_client`` takes a ready-made ``httpx2.AsyncClient``
    instead of the 1.x ``httpx_client_factory``, and no longer yields a
    ``get_session_id`` callable alongside the streams. Both of the things the
    bridge needs therefore hang off this client:

    * a request hook injecting W3C ``traceparent`` so leader-side spans chain
      under the follower's ``bridge.forward_rpc`` span;
    * a response hook capturing ``mcp-session-id``, which the bridge records in
      its state file — the leader's pid-liveness reaper matches sessions by
      ``(follower_pid, remote_session_id)``, so losing it would silently disable
      that reaper.

    Mirrors ``create_mcp_http_client``'s defaults (follow_redirects, 30s).
    """

    async def _capture_session_id(response: httpx2.Response) -> None:
        if on_session_id is None:
            return
        try:
            value = response.headers.get("mcp-session-id")
        except Exception:
            return
        if value:
            on_session_id(value)

    response_hooks = [] if on_session_id is None else [_capture_session_id]
    kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "timeout": httpx2.Timeout(timeout),
        "event_hooks": {"request": [_inject_traceparent_hook], "response": response_hooks},
    }
    if headers is not None:
        kwargs["headers"] = headers
    return httpx2.AsyncClient(**kwargs)


# ASGI middleware (Starlette-compatible) for the leader side.


def _build_propagation_carrier(headers: Any) -> dict[str, str]:
    """Decode ASGI header bytes into a W3C-propagator-friendly dict.

    W3C trace context allows ``tracestate`` to appear as multiple headers
    — per spec they must be combined into a single comma-separated string.
    A plain dict comprehension would drop all but the last. Comma-join
    collisions instead so the propagator sees the full carrier.
    """
    carrier: dict[str, str] = {}
    for raw_k, raw_v in headers:
        name = raw_k.decode("latin-1")
        value = raw_v.decode("latin-1")
        if name in carrier:
            carrier[name] = f"{carrier[name]}, {value}"
        else:
            carrier[name] = value
    return carrier


def _extract_context(carrier: dict[str, str]) -> Any:
    """Run the OTel propagator extract, swallowing broken-propagator errors."""
    try:
        return _otel_propagate.extract(carrier)
    except Exception:
        return None


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
        ctx = _extract_context(_build_propagation_carrier(scope.get("headers", [])))
        if ctx is None:
            await self.app(scope, receive, send)
            return
        await self._dispatch_with_context(ctx, scope, receive, send)

    async def _dispatch_with_context(
        self,
        ctx: Any,
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        # Initialize token + span_cm BEFORE the attach so the finally below
        # can safely check them. Attach + span open outside any try/finally
        # leaks the token onto the asyncio task (no detach ever runs) if
        # attach raises partway through, or if ``span_cm.__enter__`` raises
        # after attach succeeds.
        token: Any = None
        span_cm: Any = None
        span_ended = False

        def _end_span_once() -> None:
            nonlocal span_ended
            if span_ended or span_cm is None:
                return
            span_ended = True
            try:
                span_cm.__exit__(None, None, None)
            except Exception:
                pass

        try:
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
            tracer = get_tracer("octowright")
            span_cm = tracer.start_as_current_span("octowright.mcp.request")
            _set_request_span_attrs(span_cm.__enter__(), scope)

            async def _send_wrapper(message: MutableMapping[str, Any]) -> None:
                if not span_ended and message.get("type") == "http.response.start":
                    _end_span_once()
                await send(message)

            await self.app(scope, receive, _send_wrapper)
        finally:
            # End the span if no http.response.start was ever sent (e.g.
            # the app raised before producing a response). The OTel context
            # token outlives the span on purpose so any background work
            # the app dispatched still chains correctly. Both legs of the
            # cleanup are gated on actually having something to clean up —
            # attach() may have raised before producing a token.
            _end_span_once()
            if token is not None:
                _otel_context.detach(token)


def _set_request_span_attrs(sp: Any, scope: MutableMapping[str, Any]) -> None:
    """Set method/path on the leader-side request span, swallowing SDK errors."""
    try:
        sp.set_attribute("method", scope.get("method") or "")
        sp.set_attribute("path", scope.get("path") or "")
    except Exception:
        pass
