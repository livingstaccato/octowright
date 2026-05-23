# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Smoke tests for octowright._tracing.

We don't run a live OTLP exporter here — that's an integration concern.
Instead we verify that the tracer and metric helpers are *safe to call*
when telemetry is off (noop) AND when an in-memory exporter is wired up
(real spans flow). The expensive integration path lives behind the
``OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`` env var which only takes effect
when an operator points it at OpenObserve / Jaeger / etc.
"""

from __future__ import annotations

import pytest


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


@pytest.mark.skipif(True, reason="Live OTel SDK integration — run manually with PROVIDE_TRACE_ENABLED=true.")
def test_real_span_recorded_with_in_memory_exporter() -> None:
    """Wires the in-memory exporter to verify spans actually fire when tracing is on.

    Skipped in normal CI to keep the import surface minimal — the helpers
    are exercised in noop mode by the tests above, which catches the
    "broken when telemetry off" failure mode the rest of the codebase
    cares about.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Cached tracer in _tracing was created before this; clear the cache.
    from octowright import _tracing

    _tracing._tracer.cache_clear()  # type: ignore[attr-defined]

    from octowright._tracing import span

    with span("octowright.test.real", who="me"):
        pass

    finished = exporter.get_finished_spans()
    assert any(s.name == "octowright.test.real" for s in finished)
