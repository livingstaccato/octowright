# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Web discovery must dial exactly the host it validated, and validate off the loop.

The pinned-DNS map was keyed by ``urlsplit``'s Unicode hostname while httpx
hands the network backend the IDNA-2008 ASCII form, so a non-ASCII host missed
the pin and the backend resolved the name again -- the DNS-rebinding window the
pin exists to close. Worse, ``socket.getaddrinfo`` encodes with IDNA-2003, under
which ``straße.de`` is ``strasse.de``: a DIFFERENT domain from the
``xn--strae-oqa.de`` httpx dials, so the check could validate one site and the
request reach another.
"""

from __future__ import annotations

import threading

import httpcore2
import pytest

from octowright.server import web as _web


class _RecordingBackend:
    def __init__(self) -> None:
        self.connected: list[str] = []

    async def connect_tcp(self, host: str, port: int, **_kw: object) -> object:
        del port
        self.connected.append(host)
        raise RuntimeError("stop after backend host check")

    async def sleep(self, seconds: float) -> None:
        del seconds


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("url", "dialed_name"),
    [
        ("https://straße.de/", "xn--strae-oqa.de"),
        ("https://bücher.example/", "xn--bcher-kva.example"),
    ],
)
async def test_a_unicode_host_is_validated_and_dialed_as_the_same_ascii_name(
    monkeypatch: pytest.MonkeyPatch, url: str, dialed_name: str
) -> None:
    resolved: list[str] = []

    def fake_resolve(host: str) -> list[str]:
        resolved.append(host)
        return ["93.184.216.34"]

    backend = _RecordingBackend()
    monkeypatch.setattr(_web, "_resolve_host_ips", fake_resolve)
    monkeypatch.setattr(_web, "AutoBackend", lambda: backend)

    with pytest.raises(RuntimeError, match="stop after backend host check"):
        await _web._fetch_text(url)

    # The name checked is the name httpx connects to, and the connection goes
    # to the validated address rather than to a second lookup.
    assert resolved == [dialed_name]
    assert backend.connected == ["93.184.216.34"]


@pytest.mark.anyio
async def test_the_backend_refuses_a_host_it_was_not_given_a_pin_for() -> None:
    """Fail closed: a host with no validated address is never resolved here."""
    backend = _web._PinnedDNSBackend({"example.com": ["93.184.216.34"]})

    with pytest.raises(httpcore2.ConnectError, match="no validated address"):
        await backend.connect_tcp("other.example", 443)


@pytest.mark.anyio
async def test_host_validation_resolves_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """``getaddrinfo`` blocks; on the leader's loop it stalls every other call."""
    loop_thread = threading.get_ident()
    resolver_threads: list[int] = []

    def fake_resolve(host: str) -> list[str]:
        del host
        resolver_threads.append(threading.get_ident())
        return ["93.184.216.34"]

    monkeypatch.setattr(_web, "_resolve_host_ips", fake_resolve)
    monkeypatch.setattr(_web, "AutoBackend", lambda: _RecordingBackend())

    with pytest.raises(RuntimeError, match="stop after backend host check"):
        await _web._fetch_text("https://example.com/")

    assert resolver_threads
    assert loop_thread not in resolver_threads
