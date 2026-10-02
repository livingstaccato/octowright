# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Why a classified screenshot was refused: the kind of surface, the tier, the argument -- never the value.

A refusal used to reach the operator as "macro m failed at step 1 (screenshot)"
and nothing else, so a page rendering a signed-in username back took reading
source to diagnose. Every refusal in `safe_screenshot` is now a
`ScreenshotRefused` whose ``fields`` are value-free by construction:

- ``reasons``: the kinds of rendered surface `rendered_surface` reported
  ("rendered text", "form value", "visible attribute text", ...), or for a
  refusal before or beside that scan, why it was refused ("page changed",
  "no privacy handler", ...);
- ``stage``: ``before`` or ``after`` the capture, when the refusal has one;
- ``tiers`` and ``args``: the tier (credential/identity/contextual) and the
  argument path of the values responsible, read from the session ledger's
  provenance. For a rendered-surface refusal that is the values the page was
  found drawing; for any other refusal it is every value held, which is why the
  screenshot was classified at all. A value with no recorded provenance (a
  direct call) contributes nothing: nothing is guessed.

The same fields go into the error message, the failure payload's
``screenshot_refused`` and one ``octowright.macro.screenshot_refused`` warning.
An argument path is a name, not a value (``scrub_exempt_args`` reports paths
too), but one that happens to spell a held value is replaced by the redaction
marker rather than shown.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from provide.telemetry import get_logger

from octowright.macros.privacy_ledger import held_provenance
from octowright.macros.redaction_text import normalize
from octowright.macros.rendered_surface import OPAQUE_REASONS, rendered_leaks
from octowright.macros.scrub_engine import REDACTED, sensitive_value_variants

log = get_logger(__name__)

FIELD = "screenshot_refused"
REFUSED = "screenshot refused"


class ScreenshotRefused(RuntimeError):
    """A classified screenshot was refused; ``fields`` say why, never with the value."""

    def __init__(self, message: str, fields: dict[str, Any]) -> None:
        super().__init__(message)
        self.fields = fields


def _detail(fields: Mapping[str, Any], *, with_reasons: bool) -> str:
    parts = [", ".join(fields.get("reasons") or ())] if with_reasons else []
    if fields.get("tiers"):
        parts.append(f"tier {', '.join(fields['tiers'])}")
    if fields.get("args"):
        parts.append(f"argument {', '.join(fields['args'])}")
    return "; ".join(part for part in parts if part)


def summary(fields: Mapping[str, Any]) -> str:
    """``screenshot refused (<reasons>; tier ...; argument ...)``: the value-free failure-line suffix."""
    detail = _detail(fields, with_reasons=True)
    return f"{REFUSED} ({detail})" if detail else REFUSED


def refusal_fields(exc: BaseException | None) -> dict[str, dict[str, Any]]:
    """``{"screenshot_refused": fields}`` for a failure payload when *exc* is, or was caused by, a refusal."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ScreenshotRefused):
            return {FIELD: dict(exc.fields)}
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return {}


class Refusals:
    """The refusals of one screenshot attempt, attributed from the values it holds."""

    def __init__(self, session: Any, held: Iterable[str]) -> None:
        self._session = session
        self._held = tuple(value for value in held if isinstance(value, str) and value)

    def rendered(self, snapshot: Mapping[str, Any], reasons: list[str], *, stage: str) -> ScreenshotRefused:
        """The rendered surface still holds a value: attributed to the values the page draws, one at a time."""
        drawn = [
            value
            for value in self._held
            if set(rendered_leaks(snapshot, sensitive_value_variants((value,)))) - OPAQUE_REASONS
        ]
        head = f"classified values are still rendered {stage} the screenshot"
        return self._refuse(head, reasons, stage=stage, values=drawn, with_reasons=True)

    def refuse(self, head: str, reason: str, *, stage: str | None = None) -> ScreenshotRefused:
        """Any other refusal, attributed to every held value: that is why the screenshot was classified."""
        return self._refuse(head, [reason], stage=stage, values=self._held, with_reasons=False)

    def _refuse(
        self, head: str, reasons: list[str], *, stage: str | None, values: Iterable[str], with_reasons: bool
    ) -> ScreenshotRefused:
        tiers, args = held_provenance(self._session, values)
        fields: dict[str, Any] = {"reasons": list(reasons)}
        if stage is not None:
            fields["stage"] = stage
        fields["tiers"] = tiers
        fields["args"] = [self._shown(path) for path in args]
        log.warning("octowright.macro.screenshot_refused", **fields)
        detail = _detail(fields, with_reasons=with_reasons)
        message = f"{head} ({detail}); {REFUSED}" if detail else f"{head}; {REFUSED}"
        return ScreenshotRefused(message, fields)

    def _shown(self, path: str) -> str:
        """*path*, or the redaction marker when it spells a held value."""
        spelled = normalize(path)
        held = (normalize(value) for value in self._held)
        return REDACTED if any(value and value in spelled for value in held) else path
