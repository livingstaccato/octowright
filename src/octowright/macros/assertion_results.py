# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What each ``expect_network_clean`` / ``expect_no_text`` step of a macro run saw.

Both session methods return what they judged -- ``in_flight`` and
``in_flight_untracked`` for the network check, ``matched`` and the frame counts
for the text check -- and macro dispatch threw that away, so a check that
passed on requests still pending, or on a selector that matched nothing, read
exactly like a clean pass. ``_dispatch_standard`` hands each result here, and
``run_macro`` returns them as ``assertions``.

A context variable rather than a return value: dispatch returns
``(executed, skipped)`` through conditionals and nested ``macro_call`` alike,
and each of those runs in the calling task, so the run that started collecting
sees every step, however deep. A run nested inside another collects its own.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from octowright.assertion_warnings import assertion_warning
from octowright.macros._redact import _REDACTED_MACRO_VALUE
from octowright.macros.privacy import scrub_sensitive_values
from octowright.mcp_types import MacroAssertionFields

#: The checks whose result says more than pass/fail.
OBSERVED_ASSERTIONS = frozenset({"expect_network_clean", "expect_no_text"})


class AssertionResults:
    """One run's observations; ``step`` is the top-level step now dispatching."""

    def __init__(self) -> None:
        self.step = 0
        self.observations: list[dict[str, Any]] = []

    def add(self, kind: str, kwargs: dict[str, Any], result: dict[str, Any]) -> None:
        observation: dict[str, Any] = {"step": self.step, "action": kind, **result}
        if kind == "expect_no_text":
            # Where it looked, never what it looked for: the text is a secret.
            observation["selector"] = kwargs.get("selector", "body")
        warning = assertion_warning(kind, observation)
        if warning is not None:
            observation["warning"] = warning
        self.observations.append(observation)

    def fields(
        self, sensitive_values: tuple[str, ...], *, word_bounded: frozenset[str] = frozenset()
    ) -> MacroAssertionFields:
        """``{"assertions": [...]}`` for a result or failure payload, or nothing when no check ran.

        Scrubbed of the run's sensitive values like the rest of that payload: a
        selector is macro text, and a substituted argument can land in it.
        """
        if not self.observations:
            return {}
        copies = [dict(o) for o in self.observations]
        scrubbed = scrub_sensitive_values(
            copies, sensitive_values, marker=_REDACTED_MACRO_VALUE, word_bounded=word_bounded
        )
        return {"assertions": scrubbed}


_CURRENT: ContextVar[AssertionResults | None] = ContextVar("octowright_macro_assertions", default=None)


def begin_collecting() -> tuple[AssertionResults, Token[AssertionResults | None]]:
    """Start collecting for one run; pass the token to ``end_collecting`` from its ``finally``."""
    results = AssertionResults()
    return results, _CURRENT.set(results)


def end_collecting(token: Token[AssertionResults | None]) -> None:
    _CURRENT.reset(token)


def observe(kind: str, kwargs: dict[str, Any], result: Any) -> None:
    """Keep *result* if a run is collecting and *kind* is an observed check."""
    results = _CURRENT.get()
    if results is not None and kind in OBSERVED_ASSERTIONS and isinstance(result, dict):
        results.add(kind, kwargs, result)
