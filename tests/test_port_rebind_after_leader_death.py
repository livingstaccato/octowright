# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A leader that dies with followers still connected leaves its port rebindable.

When a leader exits or is killed, the OS closes its accepted connections from
the leader's side first. Until each follower notices and closes its end, the
leader-side half of every connection lingers on the canonical port (FIN_WAIT,
then TIME_WAIT once the follower closes). A replacement leader -- a follower's
``_respawn_if_leader_gone``, or ``octowright restart`` -- binds that port
moments later. If the lingering connections refused the bind, the replacement
would walk to the next port (``http.lifespan._claim_port``) and the canonical
one would be lost until they expired, which on Windows is minutes.

``test_windows_port_time_wait.py`` measured the TIME_WAIT half on Windows: it
does not block the exclusive rebind. This covers the half before it, with the
follower still holding its end open, and the case a respawn actually follows:
the leader PROCESS killed rather than closing its sockets itself. It runs on
every platform, because the leader's bind options differ per platform
(``_port_probe.set_listen_options``) and each has to rebind here.
"""

from __future__ import annotations

import socket
import subprocess  # nosec B404
import sys
import textwrap

from octowright import _port_probe
from octowright.http import lifespan
from tests._serve_subprocess import free_port

_HOST = "127.0.0.1"

# A stand-in leader: binds with the leader's own options, accepts one
# connection, writes to it, and waits to be killed.
_LEADER = textwrap.dedent(
    """
    import socket
    import sys
    import time

    from octowright import _port_probe

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _port_probe.set_listen_options(sock)
    sock.bind(("127.0.0.1", int(sys.argv[1])))
    sock.listen()
    print("LISTENING", flush=True)
    conn, _ = sock.accept()
    conn.sendall(b"x")
    print("SERVED", flush=True)
    time.sleep(120)
    """
)


def _assert_rebindable(port: int) -> None:
    assert _port_probe.port_is_free(_HOST, port), f"port {port} reads busy after its leader went away"
    lifespan._bind_server_socket(_HOST, port).close()


def test_port_rebinds_while_a_follower_still_holds_its_end() -> None:
    port = free_port()
    listener = lifespan._bind_server_socket(_HOST, port)
    client = socket.create_connection((_HOST, port), timeout=5)
    try:
        conn, _ = listener.accept()
        conn.sendall(b"x")
        assert client.recv(1) == b"x"
        conn.close()  # the leader's side closes first ...
        listener.close()
        # ... and the follower has not closed its end yet.
        _assert_rebindable(port)
    finally:
        client.close()


def test_port_rebinds_after_the_leader_process_is_killed_mid_connection() -> None:
    port = free_port()
    leader = subprocess.Popen(  # nosec B603
        [sys.executable, "-c", _LEADER, str(port)],
        stdout=subprocess.PIPE,
        text=True,
    )
    client: socket.socket | None = None
    try:
        assert leader.stdout is not None
        assert leader.stdout.readline().strip() == "LISTENING"
        client = socket.create_connection((_HOST, port), timeout=10)
        assert leader.stdout.readline().strip() == "SERVED"
        client.settimeout(10)
        assert client.recv(1) == b"x"
        leader.kill()
        leader.wait(timeout=10)
        # The follower has not noticed yet: its end is still open.
        _assert_rebindable(port)
    finally:
        if client is not None:
            client.close()
        if leader.poll() is None:
            leader.kill()
            leader.wait(timeout=10)
