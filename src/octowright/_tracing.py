# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Tracing + metrics helpers for Octowright.

Wraps ``provide.telemetry`` so callers can write::

    from octowright._tracing import span, set_attrs, record_exception

    with span("octowright.browser.launch", kind="chromium", profile=profile):
        ...

When tracing is disabled (no ``PROVIDE_TRACE_ENABLED=true`` / no OTel SDK),
``provide.telemetry`` returns a noop tracer and these helpers stay cheap —
no env-var reads per call, just a single attribute set on a NoopSpan.

The metrics surface uses the OTel meter API directly when available; when
not, ``counter()`` / ``histogram()`` return noop recorders that drop calls.
This way call sites stay declarative (``LAUNCHED.add(1, ...)``) and the
gate lives at module-load time, not in the hot path.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from provide.telemetry import get_tracer

# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def _tracer(name: str) -> Any:
    """Resolve a tracer by name.

    Not cached locally — ``provide.telemetry.get_tracer`` (and the underlying
    ``opentelemetry.trace.get_tracer``) already deduplicates by name and binds
    to whichever provider is current at call time. Local caching would freeze
    the binding to whatever provider existed at first import, which silently
    no-ops every span if ``setup_telemetry()`` runs later.
    """
    return get_tracer(name)


@contextmanager
def span(name: str, /, **attrs: Any) -> Iterator[Any]:
    """Start a span named ``name`` with the given attributes.

    Attributes are filtered: ``None`` values are dropped (OTel rejects them);
    everything else is passed through. The yielded value is the underlying
    span — call ``span.set_attribute`` / ``span.record_exception`` directly,
    or use the module-level helpers below which no-op on NoopSpans.

    Span name convention: ``octowright.<area>.<verb>`` — e.g.
    ``octowright.browser.launch``, ``octowright.macro.run_sequence``,
    ``octowright.bridge.forward_rpc``.
    """
    tracer = _tracer("octowright")
    cleaned = {k: v for k, v in attrs.items() if v is not None}
    with tracer.start_as_current_span(name) as sp:
        for key, value in cleaned.items():
            _set_attr(sp, key, value)
        yield sp


def _set_attr(sp: Any, key: str, value: Any) -> None:
    # OTel accepts: str, bool, int, float, sequences of those. Anything else
    # we stringify so the span still records the field rather than dropping
    # silently on the SDK side.
    if isinstance(value, str | bool | int | float):
        try:
            sp.set_attribute(key, value)
        except Exception:
            pass
        return
    if isinstance(value, list | tuple) and all(isinstance(v, str | bool | int | float) for v in value):
        try:
            sp.set_attribute(key, list(value))
        except Exception:
            pass
        return
    try:
        sp.set_attribute(key, str(value))
    except Exception:
        pass


def set_attrs(sp: Any, /, **attrs: Any) -> None:
    """Set multiple attributes on a span. Safe on NoopSpan (just drops)."""
    for key, value in attrs.items():
        if value is None:
            continue
        _set_attr(sp, key, value)


def record_exception(sp: Any, exc: BaseException) -> None:
    """Attach an exception to ``sp`` and mark the span as error.

    Safe on NoopSpan. Does NOT re-raise — caller controls flow.
    """
    record = getattr(sp, "record_exception", None)
    if record is not None:
        try:
            record(exc)
        except Exception:
            pass
    set_status = getattr(sp, "set_status", None)
    if set_status is not None:
        try:
            # Import lazily so importing this module doesn't require OTel.
            from opentelemetry.trace import Status, StatusCode

            set_status(Status(StatusCode.ERROR, str(exc)[:200]))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Metrics (counter / histogram)
# ---------------------------------------------------------------------------


class _NoopInstrument:
    def add(self, _amount: float, _attributes: dict[str, Any] | None = None, **_kwargs: Any) -> None: ...
    def record(self, _value: float, _attributes: dict[str, Any] | None = None, **_kwargs: Any) -> None: ...


_NOOP = _NoopInstrument()


try:  # pragma: no cover - import guard
    from opentelemetry import metrics as _otel_metrics  # noqa: F401

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised by monkeypatch test
    _OTEL_AVAILABLE = False


def _meter() -> Any | None:
    """Resolve the current OTel meter.

    Not cached: OTel's ``metrics.get_meter`` is itself idempotent and binds
    to whichever ``MeterProvider`` is currently registered. Caching the
    return value here would freeze the binding to the provider that existed
    when this module (or its callers) was first imported, which silently
    no-ops every instrument if ``setup_telemetry()`` runs later.
    """
    if not _OTEL_AVAILABLE:
        return None
    try:
        from opentelemetry import metrics

        return metrics.get_meter("octowright")
    except Exception:
        return None


class _LazyInstrument:
    """Defer instrument creation until first ``add``/``record``.

    Module-level call sites build instruments at import time. If that import
    happens before ``setup_telemetry()`` swaps in the real ``MeterProvider``,
    the instrument would bind to whatever provider was current at import
    (typically the OTel default no-op), and every subsequent ``add``/``record``
    would silently drop. Deferring resolution to the first datapoint call
    means we always pick up the live provider.
    """

    __slots__ = ("_factory", "_instrument", "_kwargs", "_name")

    def __init__(self, factory_name: str, name: str, **kwargs: Any) -> None:
        # factory_name is the meter method to invoke: "create_counter" /
        # "create_histogram". Stored as a string so we don't capture the
        # bound method off a meter we haven't resolved yet.
        self._factory = factory_name
        self._name = name
        self._kwargs = kwargs
        self._instrument: Any | None = None

    def _resolve(self) -> Any:
        if self._instrument is not None:
            return self._instrument
        m = _meter()
        if m is None:
            self._instrument = _NOOP
            return self._instrument
        try:
            factory = getattr(m, self._factory)
            self._instrument = factory(self._name, **self._kwargs)
        except Exception:
            self._instrument = _NOOP
        return self._instrument


class _LazyCounter(_LazyInstrument):
    # Mirror the parent's __slots__ contract — without an empty __slots__
    # here Python falls back to a per-instance __dict__ on the subclass and
    # cancels the parent's memory saving.
    __slots__ = ()

    def add(self, amount: float, attributes: dict[str, Any] | None = None, **kwargs: Any) -> None:
        try:
            self._resolve().add(amount, attributes=attributes, **kwargs)
        except Exception:
            pass


class _LazyHistogram(_LazyInstrument):
    # Mirror the parent's __slots__ contract — without an empty __slots__
    # here Python falls back to a per-instance __dict__ on the subclass and
    # cancels the parent's memory saving.
    __slots__ = ()

    def record(self, value: float, attributes: dict[str, Any] | None = None, **kwargs: Any) -> None:
        try:
            self._resolve().record(value, attributes=attributes, **kwargs)
        except Exception:
            pass


def counter(name: str, *, description: str = "", unit: str = "1") -> Any:
    """Return a lazy counter proxy.

    The real OTel counter is created on first ``.add()``, so module-level
    ``counter(...)`` calls (which run at import time) safely defer binding
    until after ``setup_telemetry()`` has registered the live provider.
    Returns a noop-equivalent when OTel isn't importable.
    """
    if not _OTEL_AVAILABLE:
        return _NOOP
    return _LazyCounter("create_counter", name, description=description, unit=unit)


def histogram(name: str, *, description: str = "", unit: str = "s") -> Any:
    """Return a lazy histogram proxy.

    Same lazy-resolution semantics as :func:`counter`. Returns a noop-equivalent
    when OTel isn't importable.
    """
    if not _OTEL_AVAILABLE:
        return _NOOP
    return _LazyHistogram("create_histogram", name, description=description, unit=unit)
