# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One type for "the caller's own input was rejected", raised by the guards.

Some sinks must distinguish a *request* that was refused from *machinery* that
failed. ``BrowserPool.launch`` is the motivating one: everything it wraps
answers "is this engine working on this machine", and a request that never
reached an engine cannot answer it. Filed as an engine fault, a caller's
``file://`` URL left ``octowright_status()["pool"]["engine_health"]`` reporting
``chromium: {"outcome": "error", "error": "ValueError"}`` — and since only the
exception class name is kept (deliberately; a launch failure message can carry
a filesystem path or a profile name), that is byte-identical to a genuinely
broken engine. It was read as one, retried on firefox for the identical signal,
and cost about an hour, inverting the block's entire purpose (issue #214).

**Classified by TYPE, not by position, because position does not work here.**
The obvious repair is to hoist the checks above the recording window, and it
closes exactly the checks that happen to be hoistable. It was tried first and
left two open: ``har_path`` containment needs the session's log path and the
pool's recordings root, and ``base_url`` validation needs the persona lock held
— both are structurally *inside* the launch pipeline, and both are MCP-surface
fields an LLM sets. A guard that raises this type is classified correctly
wherever it happens to run, and a new guard added deeper in the pipeline
inherits that instead of quietly reintroducing the bug.

Subclassing ``ValueError`` is what makes the conversion free: every existing
``except ValueError`` and ``pytest.raises(ValueError)`` keeps working, so the
guards below can be reclassified without auditing their callers.

Lives at the package root, alongside ``console_levels`` and
``dashboard_events``, so ``_paths`` — which has no octowright imports at all —
can raise it without reaching into ``browser_pool``.
"""

from __future__ import annotations

__all__ = ["InvalidRequestError"]


class InvalidRequestError(ValueError):
    """A rejection of the caller's own input, not a failure of any machinery.

    Raised by the input guards (``_paths.reject_unsafe_path``,
    ``session.core_page_mixin._reject_unsafe_url``,
    ``browser_pool.options.LaunchOptions.validate``). Sinks that describe the
    health of a *component* must not record it: the request was refused before
    that component was asked to do anything, so it says nothing about whether
    the component works.
    """
