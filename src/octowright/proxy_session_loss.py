# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the leader's HTTP status says about the follower's session.

mcp's streamable-HTTP client (2.x) does not raise when the leader refuses a
POST. It turns the refusal into a JSON-RPC error frame for the request's id
(``-32600 Session not found`` / ``Session terminated`` for a 404, ``-32603
Server returned an error response`` for a 403 or a 429 whose body is not
JSON-RPC), drops a refused notification, and keeps the transport open. Two
consequences the bridge could not see from the frames alone:

* **A lost session was never left.** When the leader no longer knows the
  session -- the idle reaper, the session-table cap, the dead-follower reaper
  ended it, or a restarted leader rotated the bridge token -- every call was
  answered with that error, forever, and nothing reconnected.
* **A throttled connect looked like any other.** The flap guard's 429/410 on
  the replayed ``initialize`` arrived as an error answer, so the connect failed
  as a ``ReplayHandshakeError`` and ``Retry-After`` was never read.

Both are visible at the HTTP layer, so the follower's client carries a
response hook that reads the status before mcp reduces it:

* a 429 or 410 on a session-creating request (no ``Mcp-Session-Id``) is kept
  as the connect's refusal, for ``proxy_retry.classify_connect_error``;
* a 401, 403 or 404 on a request that carries the CURRENT session's id
  (a stale session's late answer cannot trigger it) marks the session lost:
  the requests in that POST are flagged *rejected unrun* -- the leader refused
  them before running them, so re-sending is safe whatever their idempotency
  -- and the runtime is told to drop the session and reconnect. Their error
  frames are swallowed (``BridgeSupervisor.forward_remote_message``) and they
  are re-sent after the next handshake (``resume_in_flight``). A 404 whose body
  is a JSON-RPC error naming a request id is an answer to that request (a
  method the leader does not know), not a lost session, and is left alone.

Loops stay bounded by what already bounds a reconnect: the flap backoff for a
session that ends too soon and ``BRIDGE_RESUME_MAX_ATTEMPTS`` per request.
"""

from __future__ import annotations

import json
from typing import Any

import anyio
import httpx2
from provide.telemetry import get_logger

from octowright._trace_propagation import build_tracing_http_client
from octowright.proxy_supervisor import _BRIDGE_RECONNECT

log = get_logger(__name__)

# Statuses that mean the leader will not serve this session any more: 404 (it
# does not know the session), 401/403 (the credential the session was opened
# with is no longer accepted -- a restarted leader's rotated bridge token).
SESSION_LOSS_STATUSES = frozenset({401, 403, 404})
# The flap guard's answers to a session-creating request.
REFUSAL_STATUSES = frozenset({410, 429})
_SESSION_HEADER = "mcp-session-id"


def bridge_http_client(headers: dict[str, str], supervisor_obj: Any) -> Any:
    """The follower's HTTP client for one leader connection.

    MCP 2.0 takes a ready-made client instead of a factory, and its transport no
    longer exposes ``get_session_id`` -- so the session id is captured from the
    response header here and pushed onto the supervisor, where bridge state (and
    the leader's pid-liveness reaper) reads it. Every per-connect observation is
    reset here, since one client serves exactly one connect.
    """
    supervisor_obj.remote_session_id = None
    supervisor_obj.connect_refusal = None
    supervisor_obj.session_lost = None

    def _note_session_id(value: str) -> None:
        supervisor_obj.remote_session_id = value

    async def _watch(response: httpx2.Response) -> None:
        await watch_response(supervisor_obj, response)

    return build_tracing_http_client(headers=headers, on_session_id=_note_session_id, on_response=_watch)


def arm(supervisor_obj: Any, write_slot: Any, reset_slot: Any) -> None:
    """Give the supervisor the runtime's way to drop a lost session: stop
    handing out its writer (calls wait for the next connect) and cancel the
    session's task group so the reconnect loop moves on."""

    def _drop() -> None:
        write_slot.write = None
        write_slot.ready = anyio.Event()
        if (scope := reset_slot.cancel_scope) is not None:
            scope.cancel()

    supervisor_obj.on_session_lost = _drop


async def watch_response(supervisor_obj: Any, response: httpx2.Response) -> None:
    """The response hook. Never raises: it runs inside the transport's POST."""
    try:
        await _watch(supervisor_obj, response)
    except Exception as exc:
        log.debug("octowright.bridge.response_watch_failed", error=repr(exc))


async def _watch(supervisor_obj: Any, response: httpx2.Response) -> None:
    status = response.status_code
    request = response.request
    session_id = request.headers.get(_SESSION_HEADER)
    if status in REFUSAL_STATUSES and session_id is None:
        supervisor_obj.connect_refusal = (status, response.headers.get("retry-after"))
        return
    if status not in SESSION_LOSS_STATUSES or request.method == "DELETE":
        # The transport's DELETE on the way out of a session the leader already
        # forgot is answered 404 too; it says nothing about the next session.
        return
    if session_id is None or session_id != supervisor_obj.remote_session_id:
        return
    if status == 404 and await _answers_a_request(response):
        return
    mark_session_lost(supervisor_obj, status, _request_ids(request))


async def _answers_a_request(response: httpx2.Response) -> bool:
    """Whether a 404's body is a JSON-RPC error for a specific request id (an
    answer, e.g. an unknown method) rather than the session-level refusal the
    SDK sends with ``id: null``. Reading it here is safe: httpx keeps the body,
    and the transport's own read gets the same bytes."""
    if not response.headers.get("content-type", "").lower().startswith("application/json"):
        return False
    try:
        body = json.loads(await response.aread())
    except ValueError:
        return False
    return isinstance(body, dict) and "error" in body and body.get("id") is not None


def _request_ids(request: httpx2.Request) -> list[str | int]:
    """The JSON-RPC request ids in a POST body (none for a notification or a GET)."""
    try:
        payload = json.loads(request.content or b"null")
    except (httpx2.RequestNotRead, ValueError):
        return []
    items = payload if isinstance(payload, list) else [payload]
    return [
        item["id"]
        for item in items
        if isinstance(item, dict) and "method" in item and isinstance(item.get("id"), str | int)
    ]


def mark_session_lost(supervisor_obj: Any, status: int, request_ids: list[str | int]) -> None:
    """Record that the leader dropped the current session, keep the refused
    requests for a resume, and have the runtime reconnect."""
    for request_id in request_ids:
        item = supervisor_obj._in_flight.get(request_id)
        if item is not None and not item.responded:
            # Refused before it ran: re-sending it is safe on any leader, so a
            # resume onto a replacement must not report it as possibly run.
            item.rejected_unrun = True
            item.delivered = False
    if supervisor_obj.session_lost is not None:
        return  # already handled for this session; later refusals only add ids
    reason = f"leader session lost (HTTP {status}): the leader no longer serves this session; reconnecting"
    supervisor_obj.session_lost = reason
    supervisor_obj.last_error = reason
    _BRIDGE_RECONNECT.add(1, attributes={"reason": "session_lost"})
    log.warning(
        "octowright.bridge.session_lost",
        status=status,
        remote_session_id=supervisor_obj.remote_session_id,
        rejected_requests=len(request_ids),
        in_flight=supervisor_obj.in_flight_count,
    )
    if (drop := getattr(supervisor_obj, "on_session_lost", None)) is not None:
        drop()
