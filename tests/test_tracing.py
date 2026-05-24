# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Smoke + regression tests for octowright._tracing.

These verify the tracer/metric helpers behave both when telemetry is off
(noop everything) and when an in-memory OTel SDK exporter is wired up.
The key regression test is :func:`test_lazy_counter_binds_to_late_provider`,
which proves the lazy-instrument fix: a counter built at import time still
emits datapoints into a ``MeterProvider`` installed *after* construction.
"""

from __future__ import annotations

from typing import Any

import pytest


def _setup_span_exporter(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return an InMemorySpanExporter and monkeypatch ``_tracing._tracer``.

    OTel only allows a TracerProvider to be set once per process. To get
    per-test isolation we build a fresh SDK TracerProvider, attach our
    exporter, then swap ``octowright._tracing._tracer`` to return a tracer
    bound to *that* provider — bypassing the global. This is the same
    pattern ``tests/test_telemetry_fixes.py`` uses.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("octowright")

    import octowright._tracing as tracing

    monkeypatch.setattr(tracing, "_tracer", lambda _name: tracer)
    return exporter


def _setup_metric_reader(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Return an InMemoryMetricReader and monkeypatch ``_tracing._meter``.

    Mirrors :func:`_setup_span_exporter` for metrics: build a fresh
    SDK MeterProvider + InMemoryMetricReader, then swap
    ``octowright._tracing._meter`` so lazy instruments resolve against
    that provider instead of whichever global another test installed.
    """
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("octowright")

    import octowright._tracing as tracing

    monkeypatch.setattr(tracing, "_meter", lambda: meter)
    return reader


def test_span_noop_when_tracing_disabled() -> None:
    from octowright._tracing import record_exception, set_attrs, span

    with span("octowright.test.noop", a=1, b="x", c=None) as sp:
        # Setting attrs / recording exception on a NoopSpan must not raise.
        set_attrs(sp, more="ok")
        try:
            raise ValueError("kaboom")
        except ValueError as exc:
            record_exception(sp, exc)


def test_counter_and_histogram_noop_when_metrics_disabled() -> None:
    from octowright._tracing import counter, histogram

    c = counter("octowright_test_counter", description="t", unit="1")
    c.add(1, attributes={"k": "v"})
    h = histogram("octowright_test_hist", description="t", unit="s")
    h.record(0.5, attributes={"k": "v"})


def test_span_attribute_filtering() -> None:
    """None-valued attributes should be silently dropped, not crash the SDK."""
    from octowright._tracing import span

    with span("octowright.test.attrs", real=1, null=None, listy=[1, 2, 3]):
        pass


def test_set_attrs_with_unusual_types() -> None:
    """set_attrs should stringify exotic types rather than dropping them."""
    from octowright._tracing import set_attrs, span

    class Weird:
        def __str__(self) -> str:
            return "weird-value"

    with span("octowright.test.weird") as sp:
        # mixed list (not all primitives) hits the stringify branch;
        # explicit None hits the skip-None branch in set_attrs.
        set_attrs(sp, obj=Weird(), mixed=[1, "two", Weird()], skipme=None)


def test_record_exception_on_real_span(monkeypatch: pytest.MonkeyPatch) -> None:
    """record_exception must mark the span as ERROR when the SDK is present."""
    pytest.importorskip("opentelemetry.sdk")
    exporter = _setup_span_exporter(monkeypatch)

    from octowright._tracing import record_exception, span

    with span("octowright.test.exc") as sp:
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            record_exception(sp, exc)

    finished = exporter.get_finished_spans()
    matched = [s for s in finished if s.name == "octowright.test.exc"]
    assert matched, "span did not export"
    assert matched[-1].status.status_code.name == "ERROR"


def test_real_span_recorded_with_in_memory_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wires the in-memory exporter to verify spans actually fire when tracing is on."""
    pytest.importorskip("opentelemetry.sdk")
    exporter = _setup_span_exporter(monkeypatch)

    from octowright._tracing import span

    with span("octowright.test.real", who="me"):
        pass

    finished = exporter.get_finished_spans()
    assert any(s.name == "octowright.test.real" for s in finished)


def test_lazy_counter_binds_to_late_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a counter built before setup_telemetry() must still emit.

    This is the whole point of the lazy-instrument design — module-level
    ``counter(...)`` calls run at import time, often before the OTel
    ``MeterProvider`` is installed. The proxy must defer binding until the
    first ``.add()``, so it picks up the live provider.

    To prove the deferred-binding behaviour we (1) build the instruments
    BEFORE wiring the in-memory provider, then (2) swap ``_tracing._meter``
    to return a meter from a fresh in-memory provider, then (3) record
    datapoints and assert the reader saw them. With the old
    ``lru_cache(_meter)`` trap, the instruments would already have been
    bound to whatever provider was current at construction and the new
    meter would be ignored.
    """
    pytest.importorskip("opentelemetry.sdk")
    from octowright._tracing import counter, histogram

    # Build the instruments BEFORE the provider is installed (the trap).
    c = counter("octowright_test_lazy_counter", description="t", unit="1")
    h = histogram("octowright_test_lazy_hist", description="t", unit="s")

    # Now swap in the fresh in-memory provider via the resolver hook.
    reader = _setup_metric_reader(monkeypatch)

    c.add(1, attributes={"kind": "chromium"})
    c.add(2, attributes={"kind": "chromium"})
    h.record(0.5, attributes={"kind": "chromium"})

    data = reader.get_metrics_data()
    seen_names: set[str] = set()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                seen_names.add(metric.name)

    assert "octowright_test_lazy_counter" in seen_names
    assert "octowright_test_lazy_hist" in seen_names


def test_lazy_instruments_noop_when_otel_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """If OTel isn't importable, counter()/histogram() must return the static noop."""
    import octowright._tracing as tracing

    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", False)

    c = tracing.counter("octowright_test_noop_counter")
    h = tracing.histogram("octowright_test_noop_hist")

    assert c is tracing._NOOP
    assert h is tracing._NOOP

    # Calls must be safe.
    c.add(1, attributes={"k": "v"})
    h.record(0.5, attributes={"k": "v"})
    # _meter() must also bail out.
    assert tracing._meter() is None


def test_lazy_proxy_swallows_resolver_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the meter raises during create_*, the proxy must fall back to NOOP."""
    import octowright._tracing as tracing

    class BrokenMeter:
        def create_counter(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("nope")

        def create_histogram(self, *_args: object, **_kwargs: object) -> object:
            raise RuntimeError("nope")

    monkeypatch.setattr(tracing, "_meter", lambda: BrokenMeter())

    c = tracing.counter("octowright_test_broken_counter")
    h = tracing.histogram("octowright_test_broken_hist")
    # First call resolves -> hits exception -> caches _NOOP -> safe forever.
    c.add(1, attributes={"k": "v"})
    c.add(2, attributes={"k": "v"})
    h.record(0.5, attributes={"k": "v"})


def test_meter_returns_none_when_metrics_module_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """If opentelemetry.metrics.get_meter raises, _meter() returns None."""
    import octowright._tracing as tracing

    # Force the available-path; then make get_meter blow up.
    monkeypatch.setattr(tracing, "_OTEL_AVAILABLE", True)
    from opentelemetry import metrics as otel_metrics

    def boom(_name: str) -> object:
        raise RuntimeError("provider misconfigured")

    monkeypatch.setattr(otel_metrics, "get_meter", boom)
    assert tracing._meter() is None


def test_set_attr_swallows_sdk_failure_on_primitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """If sp.set_attribute raises on a primitive, _set_attr must not propagate."""
    from octowright._tracing import _set_attr

    class BrokenSpan:
        def set_attribute(self, _k: str, _v: object) -> None:
            raise RuntimeError("nope")

    sp = BrokenSpan()
    _set_attr(sp, "k", "value")  # primitive path
    _set_attr(sp, "k", [1, 2, 3])  # list path
    _set_attr(sp, "k", object())  # stringify path


def test_record_exception_handles_broken_span() -> None:
    """record_exception must swallow failures in the span's own methods."""
    from octowright._tracing import record_exception

    class BrokenSpan:
        def record_exception(self, _exc: BaseException) -> None:
            raise RuntimeError("inner")

        def set_status(self, _status: object) -> None:
            raise RuntimeError("status")

    record_exception(BrokenSpan(), ValueError("boom"))


def test_record_exception_with_bare_object() -> None:
    """record_exception on an object lacking the methods must no-op."""
    from octowright._tracing import record_exception

    record_exception(object(), ValueError("boom"))


def test_lazy_resolve_caches_noop_when_meter_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """_LazyInstrument._resolve must cache _NOOP when _meter() returns None."""
    import octowright._tracing as tracing

    monkeypatch.setattr(tracing, "_meter", lambda: None)

    proxy = tracing._LazyCounter("create_counter", "octowright_test_no_meter")
    proxy.add(1)
    assert proxy._instrument is tracing._NOOP


def test_lazy_counter_swallows_add_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """If the resolved instrument's .add raises, the proxy must swallow."""
    import octowright._tracing as tracing

    class BrokenInstrument:
        def add(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("add boom")

        def record(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError("rec boom")

    class StubMeter:
        def create_counter(self, *_args: object, **_kwargs: object) -> BrokenInstrument:
            return BrokenInstrument()

        def create_histogram(self, *_args: object, **_kwargs: object) -> BrokenInstrument:
            return BrokenInstrument()

    monkeypatch.setattr(tracing, "_meter", lambda: StubMeter())

    c = tracing.counter("octowright_test_add_boom")
    c.add(1)
    h = tracing.histogram("octowright_test_rec_boom")
    h.record(0.5)


def test_lazy_instrument_caches_resolved_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    """The proxy must resolve the underlying instrument once and reuse it."""
    pytest.importorskip("opentelemetry.sdk")
    _setup_metric_reader(monkeypatch)

    from octowright._tracing import _LazyCounter

    proxy = _LazyCounter("create_counter", "octowright_test_cached", description="t", unit="1")
    proxy.add(1)
    first = proxy._instrument
    proxy.add(1)
    assert proxy._instrument is first
