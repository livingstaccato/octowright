# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``run_sequence`` decides around its steps: inputs, a failed step, the span.

A failing step never raises out of a sequence (#248): with ``stop_on_failure``
the walk stops after it and the result says where (``stopped_at``), so the
caller keeps the steps that passed. What still raises is what means the
sequence could not run at all -- malformed ``names``/``args_list``, an
``args_list`` longer than ``names``, or a name no macro could have
(`resolve_sequence_args`), the operation gate (`GATE_ERRORS`), and
cancellation, which is a ``BaseException`` and never reaches an
``except Exception``.
"""

from __future__ import annotations

import contextlib
from typing import Any

from provide.telemetry import get_logger

from octowright._tracing import set_attrs
from octowright.macros._redact import _REDACTED_MACRO_VALUE
from octowright.macros.nesting import MacroLoader
from octowright.macros.privacy import MacroArgPrivacy
from octowright.macros.storage import macro_path
from octowright.mcp_types import MacroSequenceStep
from octowright.session.operation.gate import (
    OperationGateInvariantError,
    SessionBusyTimeoutError,
    SessionClosedError,
    SessionClosingError,
    SessionOperationAbortedError,
)

#: Raised by a step's own ``session.operation`` lease: the session, not the
#: step, is what failed, so no later step could run either. A gate error raised
#: INSIDE a macro's action is already a macro failure (``RuntimeError(payload)``)
#: by the time it reaches the sequence, and is a step like any other.
GATE_ERRORS: tuple[type[Exception], ...] = (
    SessionBusyTimeoutError,
    SessionClosingError,
    SessionClosedError,
    OperationGateInvariantError,
    SessionOperationAbortedError,
)

log = get_logger(__name__)


def resolve_sequence_args(names: Any, args_list: Any) -> list[dict[str, Any]]:
    """Each step's arguments, or ``ValueError`` before anything runs.

    A short *args_list* pads with ``{}`` and a ``None`` entry means ``{}``, as
    before. A malformed one used to surface as a failure of whichever step it
    reached, after the steps before it had already acted on the browser. A
    longer one is refused (its extra entries used to be dropped without a
    word, usually a caller's off-by-one), and so is a name ``macro_path``
    would refuse: it, too, used to fail only when its step ran.
    """
    if not _list_of(names, str):
        raise ValueError("names must be a list of macro names")
    supplied = [] if args_list is None else args_list
    if not _list_of(supplied, (dict, type(None))):
        raise ValueError("args_list must be a list of argument objects (or null), one per name")
    if len(supplied) > len(names):
        raise ValueError(
            f"args_list has {len(supplied)} entries but names has {len(names)}; "
            "each args_list entry supplies the macro at the same index, so the extra ones would never run"
        )
    _check_names(names)
    padded = supplied + [None] * (len(names) - len(supplied))
    return [padded[index] or {} for index in range(len(names))]


def _check_names(names: list[str]) -> None:
    """Refuse a name no step could load (`storage.macro_path`) before the first step acts.

    Only the name's shape: a macro that is merely not saved is still a failed
    step, as it was, so the steps before it keep their results.
    """
    for index, name in enumerate(names):
        try:
            macro_path(name)
        except ValueError as exc:
            raise ValueError(f"names[{index}] cannot name a macro: {exc}") from None


def _list_of(value: Any, kinds: type | tuple[type, ...]) -> bool:
    return isinstance(value, list) and all(isinstance(item, kinds) for item in value)


def macro_failure_details(exc: BaseException) -> dict[str, Any] | None:
    """The structured payload a macro failure raises, when *exc* is one.

    ``run_macro`` raises ``RuntimeError(payload)``; the payload is already
    scrubbed of the run's sensitive values, and its text is exactly the step's
    ``error``, so carrying it structured exposes nothing the error did not.
    """
    payload = exc.args[0] if isinstance(exc, RuntimeError) and exc.args else None
    if isinstance(payload, dict) and "macro" in payload and "failed_at_step" in payload:
        return dict(payload)
    return None


def step_args_used(macros: MacroLoader, name: str, step_args: dict[str, Any]) -> dict[str, Any]:
    """A failed step's ``args_used``, redacted as the macro its run loaded classifies them.

    A macro that never loaded (missing, unreadable, an unsafe name -- what
    *macros* raises) substituted nothing, so redacting by name alone loses
    nothing. A macro that DID load but cannot be classified may have
    substituted anything, so every value is redacted: failing open there would
    show an argument its own view would have hidden.
    """
    try:
        macro = macros(name)
    except Exception as exc:
        log.debug("octowright.macro.assertion_args_unavailable", macro=name, error=repr(exc))
        return MacroArgPrivacy().redact(step_args, marker=_REDACTED_MACRO_VALUE)
    try:
        return MacroArgPrivacy.for_macro(macro.get("actions", [])).redact(step_args, marker=_REDACTED_MACRO_VALUE)
    except Exception as exc:
        log.debug("octowright.macro.assertion_args_unclassifiable", macro=name, error=repr(exc))
        return dict.fromkeys(step_args, _REDACTED_MACRO_VALUE)


def failed_step(name: str, exc: Exception, args_used: dict[str, Any]) -> MacroSequenceStep:
    details = macro_failure_details(exc)
    error = str(exc) if details is None else failure_line(details)
    step: MacroSequenceStep = {"macro": name, "ok": False, "error": error, "args_used": args_used}
    if details is not None:
        step["failure"] = details
    return step


def failure_line(payload: dict[str, Any]) -> str:
    """One line for a macro failure; the whole payload is the step's ``failure``.

    ``str(exc)`` of a macro failure is the payload's repr, so carrying it as
    ``error`` as well sent every bundle twice. The line keeps the scrubbed
    original message's first line, which is what names the fault.
    """
    action = payload.get("failed_action")
    kind = action.get("action", "?") if isinstance(action, dict) else "?"
    original = str(payload.get("original") or "").strip().splitlines()
    cause = f": {original[0]}" if original else ""
    return f"macro {payload['macro']} failed at step {payload['failed_at_step']} ({kind}){cause}"


def mark_sequence_span(sp: Any, *, ok: bool, stopped_at: int | None, failed_steps: int) -> None:
    """Record the outcome on the sequence span, ERROR when a step failed.

    The sequence no longer raises, so the span no longer sees an exception to
    record. The status description is fixed text: an exception message is
    exported off-box and a macro failure's can quote page content.
    """
    set_attrs(sp, ok=ok, stopped_at=stopped_at, failed_steps=failed_steps)
    if ok:
        return
    set_status = getattr(sp, "set_status", None)
    if set_status is not None:
        with contextlib.suppress(Exception):
            from opentelemetry.trace import Status, StatusCode

            set_status(Status(StatusCode.ERROR, "macro sequence step failed"))
