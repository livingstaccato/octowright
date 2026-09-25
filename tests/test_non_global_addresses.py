# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Non-public means "not globally routable", not only "RFC1918".

``ipaddress``'s ``is_private`` is False for the shared address space
100.64.0.0/10 (RFC 6598, CGNAT), yet that range holds Alibaba Cloud's metadata
service (100.100.100.200) and every Tailscale node. Both the always-on web
discovery check and the opt-in ``block-private`` SSRF policy classified
addresses with ``is_private`` and friends, so they let it through.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import pytest

from octowright import ssrf
from octowright.request_errors import InvalidRequestError
from octowright.server import web as _web

NON_GLOBAL = [
    "100.100.100.200",  # Alibaba Cloud metadata
    "100.64.0.1",  # bottom of the shared address space
    "100.127.255.254",  # top of it
    "::ffff:100.100.100.200",  # the same, IPv4-mapped
]

#: Adjacent to the shared space but outside it: must stay reachable.
GLOBAL = ["100.63.255.255", "100.128.0.1", "93.184.216.34"]


def _url(ip: str) -> str:
    return f"http://[{ip}]/" if ":" in ip else f"http://{ip}/"


@pytest.mark.parametrize("ip", NON_GLOBAL)
def test_web_discovery_refuses_a_literal_shared_address(ip: str) -> None:
    with pytest.raises(ValueError, match="non-public"):
        _web._check_discovery_url(_url(ip))


@pytest.mark.parametrize("ip", NON_GLOBAL)
def test_web_discovery_refuses_a_name_resolving_to_a_shared_address(ip: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_web, "_resolve_host_ips", lambda host: [ip])
    with pytest.raises(ValueError, match="non-public"):
        _web._check_discovery_url("https://metadata.example/", resolve_host=True)


@pytest.mark.parametrize("ip", GLOBAL)
def test_web_discovery_still_allows_global_neighbours(ip: str) -> None:
    _web._check_discovery_url(_url(ip))


@pytest.mark.parametrize("ip", NON_GLOBAL)
def test_block_private_refuses_a_literal_shared_address(ip: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    with pytest.raises(InvalidRequestError, match="non-public"):
        ssrf.check_navigation_url(_url(ip))


@pytest.mark.parametrize("ip", NON_GLOBAL)
async def test_block_private_refuses_a_name_resolving_to_a_shared_address(
    ip: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")

    def fake(host: str, *_args: Any, **_kwargs: Any) -> list[Any]:
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]

    monkeypatch.setattr(ssrf, "_getaddrinfo", fake)
    with pytest.raises(InvalidRequestError):
        await ssrf.check_navigation_url_resolved("https://metadata.example/")


@pytest.mark.parametrize("ip", GLOBAL)
def test_block_private_still_allows_global_neighbours(ip: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    ssrf.check_navigation_url(_url(ip))


def test_the_allowlist_still_admits_a_shared_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Tailscale node is a legitimate internal target; the allowlist is how to reach it."""
    monkeypatch.setenv("OCTOWRIGHT_SSRF_POLICY", "block-private")
    monkeypatch.setenv("OCTOWRIGHT_SSRF_ALLOW", "100.64.0.1")
    ssrf.check_navigation_url("http://100.64.0.1/")


def test_multicast_stays_non_public() -> None:
    """``is_global`` alone calls 224.0.0.0/4 global on 3.11; the explicit check must stay."""
    assert ssrf.ip_is_non_public(ipaddress.ip_address("224.0.0.1"))
