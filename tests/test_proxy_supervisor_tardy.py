# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the bridge must NOT forward once a request is already answered.

Every path that finishes a request early -- deadline expiry, a connection
reset, a stream close -- both marks the ``InFlightRequest`` responded AND pops
it from ``_in_flight``, then sends the local client a synthetic
``bridge_error``. From that moment the bridge has spent the one response the
JSON-RPC id is allowed. Anything the leader says about that id afterwards is
the bridge's problem to swallow, not the client's to reconcile.

Two frames could leak out:

* the leader's **tardy response** -- ``forward_remote_message`` looked the id
  up, found nothing (it was popped, not merely flagged), skipped the whole
  ``in_flight is not None`` block, and fell through to the unconditional
  ``local_write.send`` at the bottom. The client sees two responses for one id;
* a tardy ``notifications/progress`` carrying the bridge's **synthetic**
  ``owpt-`` progressToken. ``_discard_progress_token`` removes the token from
  ``_synthetic_progress_tokens`` when the request finishes, so the membership
  test that normally swallows it fails open and the client receives a progress
  notification for a token it never issued.

The swallow must stay narrow. A response is not the only frame carrying an id:
the leader can send the client a genuine *request* (sampling, elicitation, a
roots query), and those ids are the leader's, not entries in ``_in_flight``.
Dropping every unknown id would silently break them, so the drop is gated on
``is_response`` -- the helper this module has imported all along and never used.
"""

from __future__ import annotations

import anyio
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCError, JSONRPCRequest, JSONRPCResponse

from octowright import proxy_supervisor as supervisor
from tests._proxy_supervisor_helpers import _notification, _progress, _request, _response, _tools_call


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _supervisor(send_stream: object) -> supervisor.BridgeSupervisor:
    return supervisor.BridgeSupervisor(
        local_read=None,
        local_write=send_stream,
        request_timeout_seconds=20.0,
    )


async def _drain(recv: object) -> list[SessionMessage]:
    out: list[SessionMessage] = []
    while True:
        try:
            with anyio.fail_after(0.05):
                out.append(await recv.receive())  # type: ignore[attr-defined]
        except (TimeoutError, anyio.EndOfStream):
            return out


@pytest.mark.anyio
async def test_tardy_response_after_timeout_is_dropped() -> None:
    """The client already got a bridge_error for this id; a second frame for the
    same id is a JSON-RPC protocol violation."""
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)
    sup.track_local_message(_request("tools/call", "late-1"))

    await sup._expire_overdue(sup._in_flight["late-1"].deadline + 1.0, None)
    first = await _drain(recv)
    assert len(first) == 1 and isinstance(first[0].message, JSONRPCError)

    await sup.forward_remote_message(_response("late-1"))

    assert await _drain(recv) == [], "the leader's tardy response must not reach the client"


@pytest.mark.anyio
async def test_tardy_response_after_connection_failure_is_dropped() -> None:
    """`fail_all_in_flight` clears the table the same way expiry pops it."""
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)
    sup.track_local_message(_request("tools/call", "late-2"))

    await sup.fail_all_in_flight("leader stream closed")
    await _drain(recv)

    await sup.forward_remote_message(_response("late-2"))

    assert await _drain(recv) == []


@pytest.mark.anyio
async def test_a_leader_request_with_an_unknown_id_is_still_forwarded() -> None:
    """The drop is gated on `is_response`, not on "id not in _in_flight".

    Server→client requests (sampling/createMessage, elicitation, roots/list)
    carry ids the bridge has never tracked. Dropping unknown ids wholesale would
    make them vanish and hang the leader waiting for a reply that never comes.
    """
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)

    await sup.forward_remote_message(_request("sampling/createMessage", "leader-owned-1"))

    forwarded = await _drain(recv)
    assert len(forwarded) == 1
    assert isinstance(forwarded[0].message, JSONRPCRequest)


@pytest.mark.anyio
async def test_an_ordinary_response_for_a_live_request_still_reaches_the_client() -> None:
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)
    sup.track_local_message(_request("tools/call", "live-1"))

    await sup.forward_remote_message(_response("live-1"))

    forwarded = await _drain(recv)
    assert len(forwarded) == 1
    assert isinstance(forwarded[0].message, JSONRPCResponse)


@pytest.mark.anyio
async def test_a_notification_without_an_id_is_untouched() -> None:
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)

    await sup.forward_remote_message(_notification("notifications/tools/list_changed"))

    assert len(await _drain(recv)) == 1


# --- synthetic progress tokens ------------------------------------------------


@pytest.mark.anyio
async def test_tardy_synthetic_progress_is_never_forwarded() -> None:
    """The token is the bridge's own invention; the client cannot make sense of
    it, and after the request is finished the membership set no longer holds
    it. Match on the reserved prefix so the swallow does not depend on
    bookkeeping that is deliberately torn down."""
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)
    sup.track_local_message(_tools_call("browser_launch", "prog-1"))
    token = sup._in_flight["prog-1"].progress_token
    assert isinstance(token, str) and token.startswith(supervisor.SYNTHETIC_PROGRESS_PREFIX)

    await sup._expire_overdue(sup._in_flight["prog-1"].deadline + 1.0, None)
    await _drain(recv)

    await sup.forward_remote_message(_progress(token))

    assert await _drain(recv) == [], "a bridge-internal progressToken must not leak to the client"


@pytest.mark.anyio
async def test_live_synthetic_progress_is_still_swallowed() -> None:
    """The pre-existing behaviour, unchanged: swallowed while in flight too."""
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)
    sup.track_local_message(_tools_call("browser_launch", "prog-2"))
    token = sup._in_flight["prog-2"].progress_token

    await sup.forward_remote_message(_progress(token))

    assert await _drain(recv) == []


@pytest.mark.anyio
async def test_a_client_supplied_progress_token_is_still_forwarded() -> None:
    """A client that opted into progress must keep receiving it -- the prefix is
    reserved for the bridge, so a client token cannot be confused for one."""
    send, recv = anyio.create_memory_object_stream[SessionMessage](10)
    sup = _supervisor(send)

    await sup.forward_remote_message(_progress("client-chosen-token"))

    assert len(await _drain(recv)) == 1
