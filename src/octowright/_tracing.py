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

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from provide.telemetry import get_tracer

# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _tracer(name: str) -> Any:
    """Cached tracer per module name. Noop when tracing is disabled."""
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
    def add(self, _amount: float, _attributes: dict[str, Any] | None = None) -> None: ...
    def record(self, _value: float, _attributes: dict[str, Any] | None = None) -> None: ...


_NOOP = _NoopInstrument()


@functools.lru_cache(maxsize=1)
def _meter() -> Any | None:
    try:
        from opentelemetry import metrics
    except ImportError:
        return None
    try:
        return metrics.get_meter("octowright")
    except Exception:
        return None


def counter(name: str, *, description: str = "", unit: str = "1") -> Any:
    """Get-or-create a counter instrument. Returns a noop when metrics off."""
    m = _meter()
    if m is None:
        return _NOOP
    try:
        return m.create_counter(name, description=description, unit=unit)
    except Exception:
        return _NOOP


def histogram(name: str, *, description: str = "", unit: str = "s") -> Any:
    """Get-or-create a histogram. Returns a noop when metrics off."""
    m = _meter()
    if m is None:
        return _NOOP
    try:
        return m.create_histogram(name, description=description, unit=unit)
    except Exception:
        return _NOOP
