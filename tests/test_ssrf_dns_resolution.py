# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``block-private`` must resolve a hostname, not only classify its spelling.

The literal-only check waved through any name it did not recognise, so
``private.attacker.test`` pointed at ``169.254.169.254`` reached the metadata
service under the protective policy. The resolver is patched throughout: these
pin the classification of its answers, not the machine's DNS.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from octowright import ssrf
from octowright.request_errors import InvalidRequestError

POLICY = "OCTOWRIGHT_SSRF_POLICY"
ALLOW = "OCTOWRIGHT_SSRF_ALLOW"


def _answers(*addresses: str) -> Any:
    """A getaddrinfo stand-in returning ``addresses`` and recording each lookup."""
    calls: list[str] = []

    def fake(host: str, *_args: Any, **_kwargs: Any) -> list[Any]:
        calls.append(host)
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in addresses]

    fake.calls = calls  # type: ignore[attr-defined]
    return fake


@pytest.fixture
def policy_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(POLICY, "block-private")
    monkeypatch.delenv(ALLOW, raising=False)


@pytest.mark.usefixtures("policy_on")
@pytest.mark.parametrize("answer", ["127.0.0.1", "169.254.169.254", "10.1.2.3", "::1", "::ffff:192.168.0.1"])
async def test_public_looking_name_resolving_private_is_blocked(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(ssrf, "_getaddrinfo", _answers(answer))
    with pytest.raises(InvalidRequestError, match="resolves to non-public"):
        await ssrf.check_navigation_url_resolved("http://private.attacker.test/latest/meta-data/")


@pytest.mark.usefixtures("policy_on")
async def test_any_non_public_answer_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mixed answer set is refused: the browser may pick either address."""
    monkeypatch.setattr(ssrf, "_getaddrinfo", _answers("93.184.216.34", "127.0.0.1"))
    with pytest.raises(InvalidRequestError, match="non-public"):
        await ssrf.check_navigation_url_resolved("https://mixed.attacker.test/")


@pytest.mark.usefixtures("policy_on")
async def test_public_answer_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _answers("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")
    monkeypatch.setattr(ssrf, "_getaddrinfo", fake)
    await ssrf.check_navigation_url_resolved("https://example.com/path")
    assert fake.calls == ["example.com"]


@pytest.mark.usefixtures("policy_on")
async def test_resolution_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(ssrf, "_getaddrinfo", boom)
    with pytest.raises(InvalidRequestError, match="could not be resolved"):
        await ssrf.check_navigation_url_resolved("https://nx.attacker.test/")


async def test_policy_off_never_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(POLICY, raising=False)
    fake = _answers("127.0.0.1")
    monkeypatch.setattr(ssrf, "_getaddrinfo", fake)
    await ssrf.check_navigation_url_resolved("http://private.attacker.test/")
    assert fake.calls == []


@pytest.mark.usefixtures("policy_on")
async def test_allowlisted_host_is_not_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """The allowlist names internal targets; resolving them would refuse them."""
    monkeypatch.setenv(ALLOW, "intranet.corp")
    fake = _answers("10.0.0.7")
    monkeypatch.setattr(ssrf, "_getaddrinfo", fake)
    await ssrf.check_navigation_url_resolved("http://intranet.corp/")
    assert fake.calls == []


@pytest.mark.usefixtures("policy_on")
@pytest.mark.parametrize("url", ["http://93.184.216.34/", "data:text/html,x", "/orders"])
async def test_literal_or_unroutable_targets_are_not_resolved(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    fake = _answers("127.0.0.1")
    monkeypatch.setattr(ssrf, "_getaddrinfo", fake)
    await ssrf.check_navigation_url_resolved(url)
    assert fake.calls == []


@pytest.mark.usefixtures("policy_on")
async def test_literal_block_still_applies_without_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _answers("93.184.216.34")
    monkeypatch.setattr(ssrf, "_getaddrinfo", fake)
    with pytest.raises(InvalidRequestError, match="SSRF"):
        await ssrf.check_navigation_url_resolved("http://169.254.169.254/")
    assert fake.calls == []


@pytest.mark.usefixtures("policy_on")
async def test_navigate_preflight_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool entry point applies the resolving check, before Playwright."""
    from octowright.session.core_page_mixin import reject_unsafe_url_resolved

    monkeypatch.setattr(ssrf, "_getaddrinfo", _answers("169.254.169.254"))
    with pytest.raises(InvalidRequestError, match="non-public"):
        await reject_unsafe_url_resolved("http://private.attacker.test/")


def _count_calls(monkeypatch: pytest.MonkeyPatch, module: Any, name: str) -> list[object]:
    calls: list[object] = []
    real = getattr(module, name)

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


@pytest.mark.usefixtures("policy_on")
@pytest.mark.parametrize("url", ["https://example.com/path", "http://93.184.216.34/"])
async def test_the_resolving_check_normalizes_the_host_once(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """The literal and DNS layers share one parse, so they cannot classify a host differently."""
    monkeypatch.setattr(ssrf, "_getaddrinfo", _answers("93.184.216.34"))
    calls = _count_calls(monkeypatch, ssrf, "normalize_host_for_policy")
    await ssrf.check_navigation_url_resolved(url)
    assert len(calls) == 1


@pytest.mark.usefixtures("policy_on")
async def test_navigate_preflight_checks_the_host_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool entry point runs the scheme guard and ONE host check, not the sync one twice."""
    from octowright.session import core_page_mixin

    monkeypatch.setattr(ssrf, "_getaddrinfo", _answers("93.184.216.34"))
    normalized = _count_calls(monkeypatch, ssrf, "normalize_host_for_policy")
    canonicalized = _count_calls(monkeypatch, core_page_mixin, "_canonicalize_for_guard")
    await core_page_mixin.reject_unsafe_url_resolved("https://example.com/")
    assert len(normalized) == 1
    assert len(canonicalized) == 1
