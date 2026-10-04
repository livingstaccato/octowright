# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Socket options and the free-port probe shared by the leader and ``restart``.

One definition, because the two have to agree: ``restart`` waits for the
canonical port to read free before spawning, and that answer is only useful
if it matches what the daemon's own bind will do.

The options are chosen so that a port another leader is LISTENING on can
never be bound again:

* **POSIX: ``SO_REUSEADDR``, never ``SO_REUSEPORT``.** ``SO_REUSEADDR`` only
  lets a restarted daemon rebind a port whose old socket sits in TIME_WAIT; a
  live listener still refuses the bind. ``SO_REUSEPORT`` (what the leader used
  to set) lets any same-user socket that also sets it bind and listen
  alongside a live one, with the kernel splitting connections between them --
  two leaders on one port.
* **Windows: ``SO_EXCLUSIVEADDRUSE``, never ``SO_REUSEADDR``.** There
  ``SO_REUSEADDR`` permits binding over an actively listening socket, so the
  probe called a served port free and a second leader bound it.
"""

from __future__ import annotations

import os
import socket


def set_listen_options(sock: socket.socket) -> None:
    """Apply the bind options for a socket that will (or might) listen."""
    if os.name == "nt":
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            sock.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        return
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)


def port_is_free(host: str, port: int) -> bool:
    """True when every address ``host`` resolves to can bind ``port`` now.

    Only a hint -- the port can be taken a moment later -- so the leader
    treats the bind itself as the claim (``http.lifespan._claim_port``).
    """
    try:
        addrinfos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not addrinfos:
        return False
    seen: set[tuple[int, int, int, object]] = set()
    checked = False
    for family, socktype, proto, _canonname, sockaddr in addrinfos:
        key = (family, socktype, proto, sockaddr)
        if key in seen:
            continue
        seen.add(key)
        try:
            sock = socket.socket(family, socktype, proto)
        except OSError:
            continue
        try:
            set_listen_options(sock)
            sock.bind(sockaddr)
            checked = True
        except OSError:
            return False
        finally:
            sock.close()
    return checked
