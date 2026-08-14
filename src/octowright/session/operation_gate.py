# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Literal, TypedDict

from octowright._tracing import counter, gauge, histogram

DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS = 300.0
_OPERATION_TIMEOUT_ENV = "OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS"
_OPERATION_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


class OperationGateState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    BROKEN = "broken"


class SessionBusyTimeoutError(RuntimeError):
    """The operation's FIFO ticket expired before it owned the session."""


class SessionClosingError(RuntimeError):
    """The operation arrived after the session close cutoff."""


class SessionClosedError(RuntimeError):
    """The underlying browser session is already closed."""


class OperationGateInvariantError(RuntimeError):
    """The gate's ownership/state invariants were violated."""


class OperationGateSnapshot(TypedDict):
    state: Literal["open", "closing", "closed", "broken"]
    active_operation: str | None
    active_for_ms: int | None
    queue_depth: int
    oldest_wait_ms: int | None
    queue_timeout_seconds: float


def _positive_finite_seconds(value: object, *, source: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{source} must be positive finite seconds, got {value!r}") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{source} must be positive finite seconds, got {value!r}")
    return parsed


def resolve_operation_queue_timeout_seconds(
    explicit: float | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> float:
    if explicit is not None:
        return _positive_finite_seconds(explicit, source="operation_queue_timeout_seconds")
    source = os.environ if environ is None else environ
    raw = source.get(_OPERATION_TIMEOUT_ENV, str(DEFAULT_OPERATION_QUEUE_TIMEOUT_SECONDS))
    return _positive_finite_seconds(raw, source=_OPERATION_TIMEOUT_ENV)


def validate_operation_name(name: str) -> str:
    if not _OPERATION_NAME_RE.fullmatch(name):
        raise ValueError(f"operation name must be a fixed identifier, got {name!r}")
    return name


_QUEUE_WAIT = histogram("octowright_operation_queue_wait_seconds", unit="s")
_ACTIVE_DURATION = histogram("octowright_operation_active_duration_seconds", unit="s")
_QUEUE_TIMEOUT = counter("octowright_operation_queue_timeout_total")
_REJECTED = counter("octowright_operation_rejected_total")
_QUEUE_DEPTH = gauge("octowright_operation_queue_depth", unit="1")
