# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Windows: a port in TIME_WAIT does not block the leader's exclusive rebind.

The leader binds with ``SO_EXCLUSIVEADDRUSE`` (`_port_probe.set_listen_options`).
Microsoft's documentation reads as though a port whose exclusive listener
closed stays unbindable for exclusive sockets until its connections leave
TIME_WAIT, which would make ``octowright restart`` time out on the port and a
respawned daemon drift to the next one. Measured on the Windows CI runner it
does not happen: after a served connection is closed server side first (so
TIME_WAIT lands on the listener's port) and the listener closes, an exclusive
bind succeeds and `port_is_free` says so.

This pins that measurement, so a Windows change that starts refusing the
rebind fails here instead of in the field, and pins the guarantee the
exclusive bind exists for: a live leader cannot be bound over, not even by a
socket asking for ``SO_REUSEADDR``.
"""

from __future__ import annotations

import socket
import sys
from typing import Any

import pytest

from octowright import _port_probe
from octowright.http import lifespan

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="SO_EXCLUSIVEADDRUSE and its TIME_WAIT behaviour are Windows-only; POSIX binds with SO_REUSEADDR",
)

_HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((_HOST, 0))
        return int(s.getsockname()[1])


def _leave_time_wait() -> int:
    """An exclusive listener serves one connection, closes it from the server
    side first (so TIME_WAIT lands on the listener's port), then closes."""
    port = _free_port()
    listener = lifespan._bind_server_socket(_HOST, port)
    try:
        client = socket.create_connection((_HOST, port), timeout=5)
        conn, _ = listener.accept()
        conn.sendall(b"x")
        assert client.recv(1) == b"x"
        conn.close()  # the server side's FIN goes first
        client.settimeout(5)
        assert client.recv(1) == b""
        client.close()
    finally:
        listener.close()
    return port


def _bind(port: int, *, option: Any = None) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if option is not None:
            sock.setsockopt(socket.SOL_SOCKET, option, 1)
        sock.bind((_HOST, port))
    except OSError:
        return False
    finally:
        sock.close()
    return True


def test_a_port_left_in_time_wait_rebinds_exclusively() -> None:
    port = _leave_time_wait()

    assert _bind(port, option=socket.SO_EXCLUSIVEADDRUSE)  # type: ignore[attr-defined]
    assert _port_probe.port_is_free(_HOST, port)
    lifespan._bind_server_socket(_HOST, port).close()


def test_a_live_exclusive_listener_cannot_be_bound_over() -> None:
    port = _free_port()
    listener = lifespan._bind_server_socket(_HOST, port)
    try:
        assert not _bind(port)
        assert not _bind(port, option=socket.SO_REUSEADDR)
        assert not _port_probe.port_is_free(_HOST, port)
    finally:
        listener.close()
