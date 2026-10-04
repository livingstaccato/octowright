# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Two leaders can never listen on the same port.

The leader pre-bound its HTTP socket with SO_REUSEPORT, which on Linux lets
any same-user socket that sets it too bind AND listen on a port another one
is already serving -- the kernel then load-balances connections between the
two. Two daemons that both passed the free-port probe (restart proceeding
unlocked beside a follower respawn) therefore came up on ONE port, splitting
followers between two pools under one lockfile token. On Windows SO_REUSEADDR
has the same effect, and also made the probe (and restart's
``_wait_for_port_free``) report a port free while a daemon listened on it.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from octowright import _port_probe
from octowright.cli import restart as restart_mod
from octowright.http import lifespan


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_a_second_claim_on_a_listening_port_is_refused() -> None:
    port = _free_port()
    first = lifespan._bind_server_socket("127.0.0.1", port)
    try:
        with pytest.raises(OSError):
            second = lifespan._bind_server_socket("127.0.0.1", port)
            second.close()
    finally:
        first.close()


def test_claim_port_walks_past_a_port_another_leader_holds() -> None:
    port = _free_port()
    first = lifespan._bind_server_socket("127.0.0.1", port)
    try:
        claimed = lifespan._claim_port("127.0.0.1", port, retries=20)
        assert claimed is not None
        sock, bound = claimed
        sock.close()
        assert bound != port
    finally:
        first.close()


def test_claim_port_returns_none_when_every_candidate_is_held() -> None:
    port = _free_port()
    first = lifespan._bind_server_socket("127.0.0.1", port)
    try:
        assert lifespan._claim_port("127.0.0.1", port, retries=0) is None
    finally:
        first.close()


class _RecordingSocket:
    def __init__(self, *_a: Any) -> None:
        self.opts: list[tuple[int, int, int]] = []
        _RecordingSocket.last = self

    last: _RecordingSocket

    def setsockopt(self, level: int, optname: int, value: int) -> None:
        self.opts.append((level, optname, value))

    def bind(self, _addr: object) -> None:
        return None

    def close(self) -> None:
        return None


_EXCLUSIVE = -5  # SO_EXCLUSIVEADDRUSE is ~SO_REUSEADDR on Windows; any sentinel works here


def test_windows_listeners_use_exclusive_address_use(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_port_probe.os, "name", "nt")
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", _EXCLUSIVE, raising=False)
    sock = _RecordingSocket()

    _port_probe.set_listen_options(sock)  # type: ignore[arg-type]

    assert (socket.SOL_SOCKET, _EXCLUSIVE, 1) in sock.opts
    assert all(opt != socket.SO_REUSEADDR for _lvl, opt, _v in sock.opts), (
        "REUSEADDR binds over a live listener on Windows"
    )


def test_posix_listeners_keep_reuseaddr_and_drop_reuseport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_port_probe.os, "name", "posix")
    sock = _RecordingSocket()

    _port_probe.set_listen_options(sock)  # type: ignore[arg-type]

    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in sock.opts
    reuseport = getattr(socket, "SO_REUSEPORT", None)
    assert all(opt != reuseport for _lvl, opt, _v in sock.opts)


def test_restart_probes_with_the_daemons_options(monkeypatch: pytest.MonkeyPatch) -> None:
    """restart's port-free wait must agree with what the daemon can bind; on
    Windows its REUSEADDR probe read a live daemon's port as free."""
    monkeypatch.setattr(_port_probe.os, "name", "nt")
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", _EXCLUSIVE, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", ("127.0.0.1", 6286))])
    monkeypatch.setattr(socket, "socket", _RecordingSocket)

    assert restart_mod._port_is_free("127.0.0.1", 6286) is True
    assert (socket.SOL_SOCKET, _EXCLUSIVE, 1) in _RecordingSocket.last.opts
