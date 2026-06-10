# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Re-exports of provide.telemetry tracing + metrics helpers.

Octowright used to implement its own ``span()`` / ``set_attrs()`` /
``record_exception()`` and lazy ``counter()`` / ``histogram()`` here. Those now
live in ``provide.telemetry`` — with consent / sampling / backpressure
governance, lazy provider rebinding, and trace↔metric exemplar correlation
built in — so this module is a thin re-export that keeps the
``octowright._tracing`` import path stable for existing call sites::

    from octowright._tracing import span, set_attrs, record_exception
    from octowright._tracing import counter, histogram

Span-name convention is unchanged: ``octowright.<area>.<verb>`` — e.g.
``octowright.browser.launch``, ``octowright.macro.run_sequence``.
"""

from __future__ import annotations

from provide.telemetry import (
    counter,
    get_tracer,
    histogram,
    record_exception,
    set_attrs,
    span,
)

# Back-compat alias: ``_trace_propagation`` imports ``_tracer`` as the
# tracer-resolution seam for its manual MCP-request span. provide.telemetry's
# get_tracer(name) has the same contract as the old local helper.
_tracer = get_tracer

__all__ = [
    "_tracer",
    "counter",
    "get_tracer",
    "histogram",
    "record_exception",
    "set_attrs",
    "span",
]
