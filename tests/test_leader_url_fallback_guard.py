# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A function that logs "rejected" must not return the rejected value.

``resolve_leader_url`` exists to stop a poisoned lockfile pointing the follower
bridge at an attacker's host: the 0600 lockfile is same-user-writable, so
without the check a hostile local process could redirect every follower->leader
JSON-RPC frame -- tool names and arguments, which is where persona credentials
are substituted -- off the box, and control every response the MCP client sees.

The check itself is correct. Its *return* was not: on rejection it logged and
then returned ``fallback_url`` unexamined. In production that fallback is the
SAME poisoned string -- ``cli/serve._bridge_to_leader`` passes
``leader_info.mcp_url`` straight into ``_run_follower``, which hands it to
``run_proxy`` -- because ``_probe_alive_leader`` accepts the lock on pid
liveness plus an HTTP 200 the attacker serves, with no host check anywhere.

So the guard fired, warned, and returned the attacker's URL. The token half of
the defence held (``resolve_leader_token`` returns ``""`` for a rejected lock),
which is why this is a redirect, not a token leak.

The fix refuses rather than passing an unvalidated string through, and the
fallback is validated on the same terms as the lock.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from octowright import proxy_runtime
from octowright.singleton import LeaderInfo

EVIL_URL = "http://evil.test/mcp/"
LOOPBACK_URL = "http://127.0.0.1:6286/mcp/"


def _lock(mcp_url: str, *, token: str = "secret") -> LeaderInfo:
    return LeaderInfo(pid=1, http_host="evil.test", http_port=80, mcp_url=mcp_url, token=token, started_at=0)


def _with_lock(info: LeaderInfo | None):
    return (
        patch.object(proxy_runtime.singleton, "read_lock", return_value=info),
        patch.object(proxy_runtime.singleton, "is_stale", return_value=False),
    )


def test_a_rejected_lock_url_is_not_returned_as_the_fallback() -> None:
    """The live bug: production passes the lock's own url in as `fallback_url`,
    so returning it unexamined handed back exactly what was just rejected."""
    read, stale = _with_lock(_lock(EVIL_URL))
    with read, stale, pytest.raises(ValueError, match="non-loopback"):
        proxy_runtime.resolve_leader_url(EVIL_URL)


def test_a_non_loopback_fallback_is_refused_even_with_no_lock() -> None:
    """The fallback is attacker-influenced too (it is derived from the lock on
    the spawn path), so it is validated on the same terms rather than trusted."""
    read, stale = _with_lock(None)
    with read, stale, pytest.raises(ValueError, match="non-loopback"):
        proxy_runtime.resolve_leader_url(EVIL_URL)


def test_a_safe_lock_url_is_still_preferred() -> None:
    read, stale = _with_lock(_lock("http://127.0.0.1:6287/mcp/"))
    with read, stale:
        assert proxy_runtime.resolve_leader_url(LOOPBACK_URL) == "http://127.0.0.1:6287/mcp/"


def test_a_loopback_fallback_is_used_when_the_lock_is_unusable() -> None:
    """No lock (or a stale one) is the ordinary case, not an attack."""
    read, stale = _with_lock(None)
    with read, stale:
        assert proxy_runtime.resolve_leader_url(LOOPBACK_URL) == LOOPBACK_URL


def test_a_rejected_lock_falls_back_to_a_safe_url_rather_than_failing() -> None:
    """A poisoned lock must not take down a follower that has a legitimate
    loopback target -- refuse the bad url, keep the good one."""
    read, stale = _with_lock(_lock(EVIL_URL))
    with read, stale:
        assert proxy_runtime.resolve_leader_url(LOOPBACK_URL) == LOOPBACK_URL


def test_the_token_is_still_withheld_from_a_rejected_lock() -> None:
    """Pre-existing behaviour, pinned: the redirect must never also hand over
    the capability token."""
    read, stale = _with_lock(_lock(EVIL_URL))
    with read, stale:
        assert proxy_runtime.resolve_leader_token() == ""


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1@evil.test/mcp/",
        "http://evil.test#@127.0.0.1/mcp/",
        "http://2130706433/mcp/",
        "http://0177.0.0.1/mcp/",
        "http://127.0.0.1.evil.test/mcp/",
        "http://localhost.evil.test/mcp/",
    ],
)
def test_loopback_lookalikes_are_refused(url: str) -> None:
    """Userinfo, fragment, integer/octal encodings and suffixed hostnames all
    resolve to a non-loopback authority for the client that connects."""
    read, stale = _with_lock(None)
    with read, stale, pytest.raises(ValueError, match="non-loopback"):
        proxy_runtime.resolve_leader_url(url)


# ---------------------------------------------------------------------------
# The election probe: reject before anything DIALS the recorded url
# ---------------------------------------------------------------------------


class _FakeSingleton:
    """Stands in for the `singleton` module `_probe_alive_leader` is handed."""

    def __init__(self, info: LeaderInfo | None) -> None:
        self._info = info
        self.probed: list[str] = []

    def read_lock(self) -> LeaderInfo | None:
        return self._info

    def is_stale(self, _info: LeaderInfo) -> bool:
        return False

    async def probe_http_alive(self, info: LeaderInfo) -> bool:
        self.probed.append(info.mcp_url)
        return True  # the attacker answers 200


@pytest.mark.anyio
async def test_a_poisoned_lock_is_rejected_before_it_is_probed() -> None:
    """The health GET is itself a dial of an attacker-chosen host, and a 200
    from it is what made the poisoned lock look live."""
    from octowright.cli import _leader_election

    sn = _FakeSingleton(_lock(EVIL_URL))

    assert await _leader_election._probe_alive_leader(sn) is None
    assert sn.probed == [], "the recorded url must not be dialled before it is validated"


@pytest.mark.anyio
async def test_a_loopback_lock_is_still_adopted() -> None:
    from octowright.cli import _leader_election

    info = _lock(LOOPBACK_URL)
    sn = _FakeSingleton(info)

    assert await _leader_election._probe_alive_leader(sn) is info
    assert sn.probed == [LOOPBACK_URL]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
