# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Websocket frames are recorded WITH their payloads.

playwright-python emits the payload itself -- a ``str``, or ``bytes`` for a
binary opcode (``_network.WebSocket._on_frame_sent`` calls
``emit(FrameSent, data)``). Only Node's API wraps it in an object carrying
``.payload``. Reading that attribute therefore resolved to ``None`` for every
frame, so the sidecar, its byte ceiling and its batched flush were all
faithfully persisting rows with no content in them, and no test noticed
because every one of them asserted on the row's shape rather than its payload.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tests._websocket_fakes import FakeSocket, io_mixin_session, sidecar_rows


class TestPayloadsReachTheSidecar:
    def test_text_frame_payload_is_persisted(self, tmp_path: Path) -> None:
        """The regression: this was an empty row for the life of the feature."""
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        session._handle_websocket(socket)
        socket.emit("framesent", "hello-from-page")
        rows = [row for row in sidecar_rows(session) if row["action"] == "websocket_framesent"]
        assert rows[0]["payload_text"] == "hello-from-page"
        assert rows[0]["payload_preview"] == "hello-from-page"
        assert rows[0]["payload_size"] == len("hello-from-page")

    def test_binary_frame_is_persisted_as_base64(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        session._handle_websocket(socket)
        socket.emit("framereceived", b"\x00\x01\x02binary")
        rows = [row for row in sidecar_rows(session) if row["action"] == "websocket_framereceived"]
        assert "payload_b64" in rows[0]
        assert "hidden" in rows[0]["payload_preview"]

    def test_a_frame_object_shape_still_works(self, tmp_path: Path) -> None:
        """The attribute read is kept as a fallback, so a binding that grows a
        frame object does not silently go empty the way this one did."""
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        session._handle_websocket(socket)
        frame = MagicMock()
        frame.payload = "from-an-object"
        frame.is_binary = False
        socket.emit("framesent", frame)
        rows = [row for row in sidecar_rows(session) if row["action"] == "websocket_framesent"]
        assert rows[0]["payload_text"] == "from-an-object"


class TestSocketRegistry:
    def test_open_socket_is_registered_with_counts(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        session._handle_websocket(socket)
        socket.emit("framesent", "a")
        socket.emit("framereceived", "bb")
        summary = session.get_websocket_summary()
        assert summary["open_count"] == 1
        entry = summary["open"][0]
        assert (entry["framesent"], entry["framereceived"]) == (1, 1)
        assert entry["bytes"] == 3
        assert entry["url"] == "ws://app.test/stream"

    def test_close_moves_the_socket_to_closed(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        session._handle_websocket(socket)
        socket.emit("close")
        summary = session.get_websocket_summary()
        assert (summary["open_count"], summary["closed_count"]) == (0, 1)
        assert summary["closed"][0]["closed_at"] is not None

    def test_socket_error_is_recorded_on_the_entry(self, tmp_path: Path) -> None:
        session = io_mixin_session(tmp_path)
        socket = FakeSocket()
        session._handle_websocket(socket)
        socket.emit("socketerror", "handshake failed")
        assert session.get_websocket_summary()["open"][0]["error"] == "handshake failed"

    def test_registry_is_bounded_and_evicts_closed_sockets_first(self, tmp_path: Path) -> None:
        """The question this answers is 'what is connected right now', so
        evicting a live socket to retain a finished one answers it wrong."""
        from octowright.session.core_io_mixin import WEBSOCKET_REGISTRY_MAX

        session = io_mixin_session(tmp_path)
        live = FakeSocket("ws://app.test/live")
        live.id = "live"
        session._handle_websocket(live)
        for index in range(WEBSOCKET_REGISTRY_MAX + 10):
            churn = FakeSocket(f"ws://app.test/{index}")
            churn.id = f"churn-{index}"
            session._handle_websocket(churn)
            churn.emit("close")
        summary = session.get_websocket_summary()
        assert len(session._websockets) <= WEBSOCKET_REGISTRY_MAX
        assert summary["dropped"] > 0
        # The one socket never closed must still be there.
        assert [entry["url"] for entry in summary["open"]] == ["ws://app.test/live"]
