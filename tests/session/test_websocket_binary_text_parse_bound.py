# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A ``b'...'``-shaped text frame is parsed back to bytes at most once, and only when small.

The frame handler ran ``ast.literal_eval`` on the full, uncapped payload up to
three times per frame -- twice for previews and once for the sidecar size --
on the event loop, before ``WEBSOCKET_FRAME_MAX_BYTES`` applied anywhere. The
text is page-controlled, so a 50-100 MB frame shaped like a bytes literal
stalled the daemon.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from octowright.session import core_io_mixin
from octowright.session._constants import WEBSOCKET_FRAME_MAX_BYTES
from tests._websocket_fakes import FakeSocket, io_mixin_session, sidecar_rows


@pytest.fixture
def evals(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    sizes: list[int] = []
    real = ast.literal_eval

    def counting(node_or_string: Any) -> Any:
        sizes.append(len(node_or_string))
        return real(node_or_string)

    monkeypatch.setattr(core_io_mixin.ast, "literal_eval", counting)
    return sizes


def test_a_small_bytes_literal_frame_is_parsed_exactly_once(tmp_path: Path, evals: list[int]) -> None:
    session = io_mixin_session(tmp_path)
    socket = FakeSocket()
    session._handle_websocket(socket)

    socket.emit("framereceived", "b'\\x00\\x01abc'")

    assert len(evals) == 1
    row = next(r for r in sidecar_rows(session) if r["action"] == "websocket_framereceived")
    assert row["payload_size"] == 5
    assert "payload_b64" in row


def test_an_oversized_bytes_literal_frame_is_never_parsed(tmp_path: Path, evals: list[int]) -> None:
    session = io_mixin_session(tmp_path)
    socket = FakeSocket()
    session._handle_websocket(socket)
    huge = "b'" + "A" * (4 * WEBSOCKET_FRAME_MAX_BYTES + 16) + "'"

    socket.emit("framereceived", huge)

    assert evals == []
    row = next(r for r in sidecar_rows(session) if r["action"] == "websocket_framereceived")
    # Kept as bounded text, not decoded: its prefix and true length survive.
    assert row["payload_truncated"] is True
    assert len(row["payload_text"]) == WEBSOCKET_FRAME_MAX_BYTES
    assert row["payload_size"] == len(huge)


def test_the_ceiling_path_does_not_parse_an_oversized_frame_either(
    tmp_path: Path, evals: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With OCTOWRIGHT_WEBSOCKET_MAX_BYTES set the frame is not cut, so the
    sidecar's own parse had to be bounded too."""
    monkeypatch.setenv("OCTOWRIGHT_WEBSOCKET_MAX_BYTES", str(64 * 1024 * 1024))
    session = io_mixin_session(tmp_path)
    socket = FakeSocket()
    session._handle_websocket(socket)

    socket.emit("framereceived", "b'" + "A" * (4 * WEBSOCKET_FRAME_MAX_BYTES + 16) + "'")

    assert evals == []
