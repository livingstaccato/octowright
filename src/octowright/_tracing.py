# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Re-exports of provide.telemetry tracing + metrics helpers.

Octowright does not implement its own ``span()`` / ``set_attrs()`` /
``record_exception()`` or lazy ``counter()`` / ``histogram()``. Those
live in ``provide.telemetry`` — with consent / sampling / backpressure
governance, lazy provider rebinding, and trace↔metric exemplar correlation
built in — so this module is a thin re-export that keeps the
``octowright._tracing`` import path stable for existing call sites::

    from octowright._tracing import span, set_attrs, record_exception
    from octowright._tracing import counter, gauge, histogram

Span-name convention is unchanged: ``octowright.<area>.<verb>`` — e.g.
``octowright.browser.launch``, ``octowright.macro.run_sequence``.
"""

from __future__ import annotations

from provide.telemetry import (
    counter,
    gauge,
    get_tracer,
    histogram,
    record_exception,
    set_attrs,
    span,
)

__all__ = [
    "counter",
    "gauge",
    "get_tracer",
    "histogram",
    "record_exception",
    "set_attrs",
    "span",
]
