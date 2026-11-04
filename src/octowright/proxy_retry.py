# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Follower-side classification of a failed leader connect.

A connect failure is not one kind of thing. The leader's new-session rate
limit (``http/mcp_flap_guard.py``) answers one with ``429 + Retry-After`` —
the one failure where the FOLLOWER is the party being told to slow down, and
where the leader already said exactly how long to wait. Pre-fix the runtime
treated it like a dropped connection: the flat ``reconnect_delay(attempt)``
backoff, the header ignored, and ``repr(exc)`` (an opaque ExceptionGroup
wrapping httpx's HTTPStatusError) as ``last_error``. A stale follower wedged
against the guard then looked like a flaky network for days — observed live:
a 0.13.x follower that POSTs queued frames without a session id, so every
reconnect trip-wires the limit, looping for ~50 minutes at the 5s backoff
ceiling while the bridge summary showed nothing actionable.

Split out of ``proxy_runtime`` so the runtime loop reads as control flow and
the classification (ExceptionGroup walking, Retry-After parsing) is directly
unit-testable without a transport.
"""

from __future__ import annotations

import email.utils
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx2

from octowright.version import VERSION

KIND_THROTTLED: Final[str] = "throttled"
KIND_REFUSED: Final[str] = "refused"
KIND_HTTP_ERROR: Final[str] = "http_error"
KIND_TIMEOUT: Final[str] = "timeout"
KIND_ERROR: Final[str] = "error"

# A throttle repeating this many connects in a row is not a race with the
# limiter: the follower structurally trips it (too old to replay its handshake
# correctly) or is in a genuine connect loop. Past this streak every surface
# (bridge error text, snapshot, log) says "persistent mismatch" instead of
# "retrying".
THROTTLE_LOUD_STREAK: Final[int] = 5


@dataclass(frozen=True)
class ConnectFailure:
    """How one leader connect ended, reduced to what the reconnect loop needs."""

    kind: str
    status: int | None = None
    # The leader's requested delay, in seconds (429 only). ``None`` = sent
    # none or an unparsable one (treated as absent — never trust garbage into
    # a sleep duration).
    retry_after: float | None = None


def _walk(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` and every nested exception: ExceptionGroup leaves, then
    ``__cause__``/``__context__`` links, each level walked once."""
    stack: list[BaseException] = [exc]
    seen: set[int] = set()
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        yield item
        if isinstance(item, BaseExceptionGroup):
            stack.extend(item.exceptions)
        for linked in (item.__cause__, item.__context__):
            if linked is not None:
                stack.append(linked)


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header: delta-seconds (the only form the flap
    guard emits) or an HTTP-date. None on anything else."""
    if value is None:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        parsed: datetime | None = None
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        seconds = (parsed - datetime.now(UTC)).total_seconds()
    return max(0.0, seconds) if seconds >= 0 else None


def _from_status(status: int, retry_after: str | None) -> ConnectFailure:
    if status == 429:
        return ConnectFailure(KIND_THROTTLED, status=status, retry_after=_retry_after_seconds(retry_after))
    if status == 410:
        # The leader's sustained-throttle refusal: pacing is over, this
        # follower is structurally wrong for this leader. The 410 body
        # still carries Retry-After, and honoring it keeps the follower's
        # (pointless, but bounded) retry loop at the leader's pace.
        return ConnectFailure(KIND_REFUSED, status=status, retry_after=_retry_after_seconds(retry_after))
    return ConnectFailure(KIND_HTTP_ERROR, status=status)


def classify_connect_error(exc: BaseException, observed: tuple[int, str | None] | None = None) -> ConnectFailure:
    """The failure kind of one raised leader connect.

    ``observed`` is the ``(status, Retry-After)`` of a session-creating
    request the leader refused during this connect, as the bridge's HTTP
    response hook saw it (``proxy_session_loss``). It is the path the real
    transport takes: mcp's streamable-HTTP client does not raise for a POST
    answered 429 or 410 but hands the replayed ``initialize`` a JSON-RPC
    error frame, so the connect fails as a ``ReplayHandshakeError`` that
    carries no status at all. An ``httpx`` ``HTTPStatusError`` leaf (nested
    in the transport's ``ExceptionGroup``) is still honoured, for a
    transport that does raise one. Anything without a recoverable status
    keeps its existing handling (flat attempt backoff).
    """
    for leaf in _walk(exc):
        if isinstance(leaf, httpx2.HTTPStatusError):
            return _from_status(leaf.response.status_code, leaf.response.headers.get("retry-after"))
    if observed is not None:
        return _from_status(*observed)
    if isinstance(exc, TimeoutError):
        return ConnectFailure(KIND_TIMEOUT)
    return ConnectFailure(KIND_ERROR)


def describe_throttle(
    failure: ConnectFailure,
    *,
    streak: int,
    retry_in: float,
    leader_version: str | None,
) -> str:
    """The throttle state as one operator-readable sentence.

    Goes to three surfaces: ``last_error`` in the bridge snapshot (what
    ``octowright_status`` shows), the bridge error text the MCP client sees
    on calls failed by the reset, and the follower log. One sentence so the
    three never drift. "Leader" names versions on both ends because the
    proven cause of a persistent throttle loop is a follower too old for the
    leader's handshake, and the remediation differs per side.
    """
    retry_note = " (the leader's Retry-After)" if failure.retry_after is not None else ""
    text = (
        f"leader throttled this follower via its new-session rate limit (HTTP 429),"
        f" retry in {retry_in:.1f}s{retry_note};"
        f" follower v{VERSION} vs leader v{leader_version or 'unknown'}"
    )
    if streak >= THROTTLE_LOUD_STREAK:
        text += (
            f"; throttled {streak} connects in a row — a persistent follower/leader mismatch,"
            " not a flaky connection; reconnect this MCP client so a current follower spawns"
            " (octowright doctor names this client's step)"
        )
    return text


def describe_refused(*, retry_in: float, leader_version: str | None) -> str:
    """The leader's sustained-throttle refusal as one operator sentence.

    Unlike a 429 this never quiets down on its own: the leader has decided
    this follower is structurally wrong for it and pacing is over, so the
    sentence leads with the remediation, not the retry."""
    return (
        f"leader refused to serve this follower (HTTP 410: sustained new-session throttling) —"
        f" this follower can no longer work with this leader: reconnect this MCP client so a"
        f" current follower spawns; follower v{VERSION} vs leader v{leader_version or 'unknown'};"
        f" pace-retried in {retry_in:.1f}s"
    )
