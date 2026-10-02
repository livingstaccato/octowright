# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Where one fill/type step spent its budget, named in its timeout error.

A ``fill`` shares one budget between waiting for its element, reading the
element's role metadata, classifying it for redaction, and the fill itself
(``input_redaction.probe_timeout_ms``). When an earlier phase takes the budget,
the action is left with nothing and Playwright's error describes only the
action: a Windows Firefox run held the lease 21 s against a 15 s budget and
reported ``Page.fill: Timeout 1ms exceeded ... waiting for locator("#pw")``,
which reads as a missing element while the element had attached.

:class:`StepPhases` stamps each phase as it ends and, when the step fails with a
Playwright ``TimeoutError``, re-raises that error -- same class, same leading
message, ``name``/``stack`` carried over as Playwright's own ``rewrite_error``
does -- with one line appended: the budget, the elapsed total, and each phase's
milliseconds, the one that was running marked ``(timed out)``. Nothing but
monotonic stamps is taken on the happy path, and no other error is touched.
"""

from __future__ import annotations

import time
from types import TracebackType

from playwright.async_api import TimeoutError as PlaywrightTimeoutError


class StepPhases:
    """Context manager over one step; ``done(...)`` after each named phase but the last."""

    def __init__(self, budget_ms: float, *phases: str) -> None:
        self.budget_ms = budget_ms
        self._phases = phases
        self._started = time.monotonic()
        self._mark = self._started
        self._spent: list[int] = []
        #: The step's shared deadline (``time.monotonic()``), for ``probe_timeout_ms``.
        self.deadline = self._started + budget_ms / 1000

    def done(self) -> None:
        """The current phase ended; the next one starts now."""
        now = time.monotonic()
        self._spent.append(round((now - self._mark) * 1000))
        self._mark = now

    def summary(self) -> str:
        now = time.monotonic()
        parts = [f"{name} {ms}ms" for name, ms in zip(self._phases, self._spent, strict=False)]
        if len(self._spent) < len(self._phases):
            running = self._phases[len(self._spent)]
            parts.append(f"{running} (timed out) {round((now - self._mark) * 1000)}ms")
        total = round((now - self._started) * 1000)
        return f"step budget {self.budget_ms:g}ms, {total}ms elapsed: " + ", ".join(parts)

    def explain(self, exc: PlaywrightTimeoutError) -> PlaywrightTimeoutError:
        explained = type(exc)(f"{exc.message}\n{self.summary()}")
        explained._name = exc.name
        explained._stack = exc.stack
        return explained

    def __enter__(self) -> StepPhases:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if isinstance(exc, PlaywrightTimeoutError):
            raise self.explain(exc) from exc


__all__ = ["StepPhases"]
