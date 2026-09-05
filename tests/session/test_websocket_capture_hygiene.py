# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Capture-side hygiene the payload fix made load-bearing.

Recording an empty payload for every frame hid three things. The preview now
has content, so it also has SIZE, and it is written to the main session JSONL
as well as the sidecar -- a file with no ceiling on by default that unrelated
tools read. The registry's socket key now decides which frames a caller gets
back, so reusing one is a correctness bug rather than a cosmetic id. And a
read now flushes the write buffer, which the batching arithmetic never
accounted for.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from octowright.session.core_io_mixin import WEBSOCKET_RECORD_PREVIEW_CHARS
from tests._websocket_fakes import FakeSocket, io_mixin_session, sidecar_rows


class _UnwirableSocket(FakeSocket):
    def on(self, event: str, handler: Any) -> None:
        raise RuntimeError("binding refused the listener")


def _recorded(session: Any, action: str) -> list[dict[str, Any]]:
    return [call.kwargs for call in session.recorder.record.call_args_list if call.args and call.args[0] == action]


@pytest.fixture
def wired(tmp_path: Path) -> tuple[Any, Any]:
    """A session with one socket already wired -- the setup every test needs."""
    session = io_mixin_session(tmp_path)
    socket = FakeSocket()
    session._handle_websocket(socket)
    return session, socket


@pytest.fixture
def after_big_frame(wired: tuple[Any, Any]) -> Any:
    session, socket = wired
    socket.emit("framesent", "x" * 5_000)
    return session


class TestMainRecordingIsNotInflated:
    """The sidecar is the full-fidelity sink; the main JSONL is not.

    Before the payload fix this field was ``""`` for every frame, so nothing
    measured what putting a real preview in it costs. ``OCTOWRIGHT_RECORDING_MAX_BYTES``
    is OFF by default, and ``browser_tail_recording``, the dashboard event
    stream and ``capture_create(kind="recording")`` all read that file --
    callers who never asked about websockets pay for every frame.
    """

    def test_the_recorded_preview_is_short(self, after_big_frame: Any) -> None:
        preview = _recorded(after_big_frame, "websocket_framesent")[0]["payload_preview"]
        assert len(preview) <= WEBSOCKET_RECORD_PREVIEW_CHARS + 1  # + the ellipsis

    def test_the_sidecar_preview_is_not_shortened_with_it(self, after_big_frame: Any) -> None:
        """The read path serves previews from here, so it keeps the long one."""
        row = next(r for r in sidecar_rows(after_big_frame) if r["action"] == "websocket_framesent")
        assert len(row["payload_preview"]) > WEBSOCKET_RECORD_PREVIEW_CHARS

    def test_the_size_is_still_recorded_in_full(self, after_big_frame: Any) -> None:
        """Capping the preview must not cost the one cheap field worth having."""
        assert _recorded(after_big_frame, "websocket_framesent")[0]["payload_size"] == 5_000


class TestSocketIdentityIsNeverReused:
    """``id(websocket)`` is an address, and CPython hands addresses out again.

    A page that opens a socket per retry frees the object; a later socket
    allocated at the same address got the same key, so the closed entry was
    overwritten and one socket_id filter returned two sockets' frames as one
    interleaved stream.
    """

    def test_ids_do_not_come_from_the_object_address(self, wired: tuple[Any, Any]) -> None:
        session, socket = wired
        assert str(id(socket)) not in session._websockets

    def test_each_socket_gets_its_own_id(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        session._handle_websocket(FakeSocket())
        session._handle_websocket(FakeSocket())
        assert len(session._websockets) == 2

    def test_an_id_is_not_reissued_after_the_socket_is_freed(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        first = FakeSocket()
        session._handle_websocket(first)
        seen = set(session._websockets)
        del first
        session._handle_websocket(FakeSocket())
        assert not seen & (set(session._websockets) - seen)
        assert len(session._websockets) == 2

    def test_a_binding_supplied_id_is_metadata_not_the_key(self, tmp_path: Path) -> None:
        """Believing a binding's id would reopen the collision it replaced.

        Nothing checks that a supplied id is unique within the session, and
        ``_register_websocket`` overwrites on a repeat key -- so a binding
        handing out a duplicate (or the string ``ws-1``) would merge two
        sockets' frames under one id again. The session owns the key; the
        supplied value is kept beside it for correlation.
        """
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        socket.id = "sock-1"  # type: ignore[attr-defined]
        session._handle_websocket(socket)
        entry = next(iter(session._websockets.values()))
        assert entry["binding_id"] == "sock-1"
        assert entry["id"] != "sock-1"

    def test_two_sockets_sharing_a_supplied_id_stay_separate(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        for _ in range(2):
            socket = FakeSocket()
            socket.id = "same"  # type: ignore[attr-defined]
            session._handle_websocket(socket)
        assert len(session._websockets) == 2


class TestGhostSocketsAreNotRegistered:
    """A socket registered before its listeners attach can never be closed.

    Nothing sets ``closed_at`` for it, so it counts as open forever -- and
    since eviction prefers CLOSED entries, those ghosts are evicted last and
    push out genuinely live sockets, the exact outcome the ordering exists to
    prevent.
    """

    def test_a_socket_whose_listeners_raise_is_not_registered(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        session._handle_websocket(_UnwirableSocket())
        assert session._websockets == {}

    def test_a_socket_with_no_listener_api_is_not_registered(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        session._handle_websocket(object())
        assert session._websockets == {}

    def test_a_wired_socket_is_registered_and_recorded(self, wired: tuple[Any, Any]) -> None:
        session, _socket = wired
        assert len(session._websockets) == 1
        assert _recorded(session, "websocket_opened")


class TestFlushArithmetic:
    def test_an_external_flush_restarts_the_batching_clock(self, wired: tuple[Any, Any]) -> None:
        """It reset the frame counter and left the timestamp alone.

        The write path flushes on frames-since OR seconds-since, so after a
        read the next frame written saw a stale ``last_flush``, took the time
        branch and flushed again immediately -- undoing a batch's worth of the
        syscall batching the surrounding comment argues for.
        """
        session, socket = wired
        socket.emit("framesent", "a")
        session._websocket_last_flush_ts = time.monotonic() - 3600
        session._flush_websocket_cache()
        socket.emit("framesent", "b")
        assert session._websocket_frames_since_flush == 1
