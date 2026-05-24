# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tests for octowright._trace_propagation and the bridge end-to-end RPC duration.

The propagation seams are best exercised with a real (in-memory) OTel SDK,
since the behavior we care about is "spans flow through the right shape of
parent/child tree" rather than "the helpers are call-safe under noop"
(``test_tracing.py`` covers the noop side).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from octowright import _trace_propagation as tp


@pytest.fixture
def anyio_backend() -> str:
    # Pin to asyncio. Without this fixture, pytest-anyio parametrizes
    # ``@pytest.mark.anyio`` tests across asyncio AND trio; trio is not
    # an installed/intended runtime for the bridge.
    return "asyncio"


@pytest.fixture
def in_memory_tracer(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[InMemorySpanExporter, TracerProvider]:
    """Install a fresh in-memory TracerProvider for the test.

    OTel only allows ``set_tracer_provider`` once per process — subsequent
    calls warn and are ignored — and ``provide.telemetry.get_tracer`` falls
    back to a noop when the global provider isn't an SDK provider. To
    bypass both traps and keep tests isolated from each other, we
    monkeypatch ``octowright._tracing._tracer`` (the seam the middleware
    uses) and ``opentelemetry.trace.get_tracer`` (the seam OTel's
    propagator API uses) to return tracers from our fresh provider.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    # Reroute the middleware's tracer resolution at the seam.
    monkeypatch.setattr(tp, "_tracing_get_tracer", lambda _name: provider.get_tracer("octowright"))
    # Also reroute the global OTel API so spans inside the wrapped app
    # (which use ``opentelemetry.trace.get_tracer("inside")``) land in
    # the same exporter.
    real_get_tracer = trace.get_tracer

    def fake_get_tracer(name: str, *args: Any, **kwargs: Any) -> Any:
        # Strip args/kwargs we don't care about so SDK semantics still apply.
        del args, kwargs
        return provider.get_tracer(name)

    monkeypatch.setattr(trace, "get_tracer", fake_get_tracer)
    # ``trace.get_current_span`` must keep working — restore on teardown
    # is automatic because monkeypatch is per-test.
    del real_get_tracer  # silence linter; restoration is monkeypatch's job
    return exporter, provider


# ---------------------------------------------------------------------------
# tracing_httpx_client_factory
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_httpx_factory_injects_traceparent_when_inside_span(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """An httpx request made inside an OTel span must carry a traceparent header."""
    _exporter, provider = in_memory_tracer
    tracer = provider.get_tracer("test")

    captured: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={"ok": True})

    factory = tp.tracing_httpx_client_factory()
    # MockTransport bypasses the network; the event hook still fires.
    transport = httpx.MockTransport(handler)
    client = factory()
    # Swap the transport on the already-built client so the event hook is
    # preserved. (httpx applies hooks regardless of transport.)
    client._transport = transport  # type: ignore[attr-defined]

    with tracer.start_as_current_span("outer") as outer_span:
        await client.get("http://leader/mcp/")
    await client.aclose()

    assert "traceparent" in captured["headers"]
    trace_id_hex = format(outer_span.get_span_context().trace_id, "032x")
    assert trace_id_hex in captured["headers"]["traceparent"]


@pytest.mark.anyio
async def test_httpx_factory_no_span_still_works() -> None:
    """Outside a span the factory still produces a working client."""
    captured: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(204)

    factory = tp.tracing_httpx_client_factory()
    client = factory(headers={"x-test": "1"}, timeout=httpx.Timeout(5.0), auth=None)
    client._transport = httpx.MockTransport(handler)  # type: ignore[attr-defined]

    resp = await client.get("http://leader/")
    await client.aclose()

    assert resp.status_code == 204
    assert captured["headers"]["x-test"] == "1"


@pytest.mark.anyio
async def test_httpx_factory_passes_auth_through() -> None:
    """Factory must forward ``auth=`` to AsyncClient (covers the auth branch)."""
    captured: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200)

    factory = tp.tracing_httpx_client_factory()
    auth = httpx.BasicAuth(username="u", password="p")
    client = factory(auth=auth)
    client._transport = httpx.MockTransport(handler)  # type: ignore[attr-defined]
    assert client.auth is auth
    await client.get("http://leader/")
    await client.aclose()
    # BasicAuth produces an Authorization header on the wire.
    assert captured["headers"].get("authorization", "").startswith("Basic ")


@pytest.mark.anyio
async def test_inject_hook_swallows_propagator_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """If propagate.inject blows up we must NOT fail the outbound request."""

    def boom(_carrier: Any) -> None:
        raise RuntimeError("propagator broken")

    monkeypatch.setattr(tp._otel_propagate, "inject", boom)

    request = httpx.Request("GET", "http://leader/")
    # Must complete without raising.
    await tp._inject_traceparent_hook(request)


@pytest.mark.anyio
async def test_inject_hook_noop_when_otel_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tp, "_OTEL_AVAILABLE", False)
    request = httpx.Request("GET", "http://leader/")
    await tp._inject_traceparent_hook(request)  # no-ops


# ---------------------------------------------------------------------------
# TraceContextExtractionMiddleware
# ---------------------------------------------------------------------------


def _make_scope(headers: list[tuple[bytes, bytes]], scope_type: str = "http") -> dict[str, Any]:
    return {
        "type": scope_type,
        "method": "POST",
        "path": "/mcp/",
        "headers": headers,
    }


async def _noop_receive() -> MutableMapping[str, Any]:
    return {"type": "http.request", "body": b""}


def _make_send_collector() -> tuple[
    Callable[[MutableMapping[str, Any]], Awaitable[None]], list[MutableMapping[str, Any]]
]:
    out: list[MutableMapping[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        out.append(message)

    return send, out


@pytest.mark.anyio
async def test_middleware_extracts_traceparent_and_chains_child_span(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """Spans opened inside the wrapped app must have the upstream traceparent's trace_id."""
    exporter, _provider = in_memory_tracer
    exporter.clear()
    trace_id_hex = "0af7651916cd43dd8448eb211c80319c"
    span_id_hex = "b7ad6b7169203331"
    headers = [
        (b"traceparent", f"00-{trace_id_hex}-{span_id_hex}-01".encode("ascii")),
    ]
    captured: dict[str, int] = {}

    async def fake_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        tracer = trace.get_tracer("inside")
        with tracer.start_as_current_span("inner-work") as sp:
            captured["trace_id"] = sp.get_span_context().trace_id
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    await mw(_make_scope(headers), _noop_receive, send)

    assert captured["trace_id"] == int(trace_id_hex, 16)
    # Both the per-request middleware span AND the inner-work span chain
    # under the upstream trace_id.
    finished = exporter.get_finished_spans()
    matching = [s for s in finished if s.context.trace_id == int(trace_id_hex, 16)]
    names = {s.name for s in matching}
    assert "octowright.mcp.request" in names
    assert "inner-work" in names


@pytest.mark.anyio
async def test_middleware_ends_span_on_response_start_not_body(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """The middleware span must close at http.response.start, not body completion.

    For SSE streams the body can stay open for minutes; if the span doesn't
    close at headers we fill the exporter buffer with one span per concurrent
    follower and drop spans silently.
    """
    exporter, _provider = in_memory_tracer
    exporter.clear()
    headers = [(b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")]
    finished_during_body: list[ReadableSpan] = []
    body_done = asyncio.Event()

    async def slow_sse_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        # The span should be ended already by the time the response body
        # is dispatched. Capture the snapshot HERE — before the long body
        # write — to prove it.
        finished_during_body.extend(exporter.get_finished_spans())
        # Simulate a long SSE stream.
        await asyncio.sleep(0.05)
        await send({"type": "http.response.body", "body": b"streamed", "more_body": False})
        body_done.set()

    mw = tp.TraceContextExtractionMiddleware(slow_sse_app)
    send, _ = _make_send_collector()
    await mw(_make_scope(headers), _noop_receive, send)
    await body_done.wait()

    request_spans = [s for s in finished_during_body if s.name == "octowright.mcp.request"]
    assert request_spans, "octowright.mcp.request span must be ended BEFORE the SSE body completes"


@pytest.mark.anyio
async def test_middleware_combines_multivalue_tracestate(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """Multiple tracestate headers are comma-joined per W3C spec, not dict-dedup'd."""
    exporter, _provider = in_memory_tracer
    exporter.clear()
    captured: dict[str, Any] = {}
    headers = [
        (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
        (b"tracestate", b"vendor1=value1"),
        (b"tracestate", b"vendor2=value2"),
    ]

    async def fake_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        # Capture current span's trace state through the propagator's lens
        # by inspecting the active context.
        from opentelemetry.trace import get_current_span

        ctx = get_current_span().get_span_context()
        captured["trace_state"] = dict(ctx.trace_state)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    await mw(_make_scope(headers), _noop_receive, send)

    # Both vendors must round-trip through the combined header.
    assert captured["trace_state"].get("vendor1") == "value1"
    assert captured["trace_state"].get("vendor2") == "value2"


@pytest.mark.anyio
async def test_middleware_ends_span_when_app_raises_before_response(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """If the app raises before http.response.start, the finally still ends the span."""
    exporter, _provider = in_memory_tracer
    exporter.clear()
    headers = [(b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")]

    async def bad_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        _send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        raise RuntimeError("app exploded before producing a response")

    mw = tp.TraceContextExtractionMiddleware(bad_app)
    send, _ = _make_send_collector()
    with pytest.raises(RuntimeError, match="app exploded"):
        await mw(_make_scope(headers), _noop_receive, send)

    # The span MUST have been ended despite the exception.
    request_spans = [s for s in exporter.get_finished_spans() if s.name == "octowright.mcp.request"]
    assert request_spans


@pytest.mark.anyio
async def test_middleware_skips_non_http_scope() -> None:
    """Lifespan / websocket scopes pass through untouched."""
    called: dict[str, bool] = {}

    async def fake_app(*_: Any) -> None:
        called["yes"] = True

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    await mw({"type": "lifespan"}, _noop_receive, send)
    assert called == {"yes": True}


@pytest.mark.anyio
async def test_middleware_skips_when_otel_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tp, "_OTEL_AVAILABLE", False)
    called: dict[str, bool] = {}

    async def fake_app(*_: Any) -> None:
        called["yes"] = True

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    await mw(_make_scope([]), _noop_receive, send)
    assert called == {"yes": True}


@pytest.mark.anyio
async def test_middleware_passes_through_when_extract_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If propagate.extract raises or returns None, fall through with no span."""

    def boom(_carrier: dict[str, str]) -> Any:
        raise RuntimeError("bad carrier")

    monkeypatch.setattr(tp._otel_propagate, "extract", boom)

    called: dict[str, bool] = {}

    async def fake_app(*_: Any) -> None:
        called["yes"] = True

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    await mw(_make_scope([(b"traceparent", b"badness")]), _noop_receive, send)
    assert called == {"yes": True}


@pytest.mark.anyio
async def test_middleware_set_attribute_failure_does_not_break_request(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If set_attribute on the span raises, the middleware still runs the app and ends the span."""
    exporter, _provider = in_memory_tracer
    exporter.clear()

    real_get_tracer = tp._tracing_get_tracer

    class FailingSpan:
        def __init__(self, wrapped: Any) -> None:
            self._wrapped = wrapped

        def set_attribute(self, *_a: Any, **_kw: Any) -> None:
            raise RuntimeError("attr broken")

        def __getattr__(self, name: str) -> Any:
            return getattr(self._wrapped, name)

    class FailingSpanCM:
        def __init__(self, inner_cm: Any) -> None:
            self._inner = inner_cm

        def __enter__(self) -> Any:
            real = self._inner.__enter__()
            return FailingSpan(real)

        def __exit__(self, *args: Any) -> Any:
            return self._inner.__exit__(*args)

    class WrappedTracer:
        def __init__(self, real: Any) -> None:
            self._real = real

        def start_as_current_span(self, name: str, **kw: Any) -> Any:
            return FailingSpanCM(self._real.start_as_current_span(name, **kw))

    def fake_get_tracer(name: str) -> Any:
        return WrappedTracer(real_get_tracer(name))

    monkeypatch.setattr(tp, "_tracing_get_tracer", fake_get_tracer)

    headers = [(b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")]
    completed: dict[str, bool] = {}

    async def fake_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})
        completed["yes"] = True

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    await mw(_make_scope(headers), _noop_receive, send)
    assert completed == {"yes": True}


@pytest.mark.anyio
async def test_middleware_swallows_span_exit_failure(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If span_cm.__exit__ raises, the middleware must still complete cleanly."""
    exporter, _provider = in_memory_tracer
    exporter.clear()

    real_get_tracer = tp._tracing_get_tracer

    class ExitFailingCM:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __enter__(self) -> Any:
            return self._inner.__enter__()

        def __exit__(self, *args: Any) -> Any:
            self._inner.__exit__(*args)
            raise RuntimeError("exit blew up")

    class WrappedTracer:
        def __init__(self, real: Any) -> None:
            self._real = real

        def start_as_current_span(self, name: str, **kw: Any) -> Any:
            return ExitFailingCM(self._real.start_as_current_span(name, **kw))

    def fake_get_tracer(name: str) -> Any:
        return WrappedTracer(real_get_tracer(name))

    monkeypatch.setattr(tp, "_tracing_get_tracer", fake_get_tracer)

    headers = [(b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")]

    async def fake_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, _ = _make_send_collector()
    # The wrapped __exit__ raises but the middleware swallows it.
    await mw(_make_scope(headers), _noop_receive, send)


@pytest.mark.anyio
async def test_middleware_send_wrapper_only_ends_span_on_first_response_start(
    in_memory_tracer: tuple[InMemorySpanExporter, TracerProvider],
) -> None:
    """A second http.response.start (shouldn't happen, but defensive) must not crash."""
    exporter, _provider = in_memory_tracer
    exporter.clear()
    headers = [(b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01")]

    async def fake_app(
        _scope: MutableMapping[str, Any],
        _recv: Callable[[], Awaitable[MutableMapping[str, Any]]],
        send: Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        # Garbage second start — must be ignored cleanly.
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    mw = tp.TraceContextExtractionMiddleware(fake_app)
    send, out = _make_send_collector()
    await mw(_make_scope(headers), _noop_receive, send)

    # All three send calls should have been forwarded.
    assert len(out) == 3
    request_spans = [s for s in exporter.get_finished_spans() if s.name == "octowright.mcp.request"]
    # Exactly one middleware span should have been emitted.
    assert len(request_spans) == 1
