# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A daemon-mode leader no election started takes the election lock itself.

The race this closes (2026-11-03): ``systemctl --user restart`` started the
daemon, a new client's follower ran the election during the daemon's slow
imports, found no leader and spawned a second daemon, which port-walked to
6287 beside it. See ``cli/_daemon_startup``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright import daemonize as _daemon
from octowright import singleton as _sn
from octowright.cli import _daemon_startup
from octowright.cli import _leader_election as _election


@pytest.fixture
def lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "octowright.lock"
    # HeldElectionLock.acquire binds LOCK_PATH as its default at definition
    # time, so steer the classmethod rather than the module constant.
    real = _sn.HeldElectionLock.acquire.__func__  # type: ignore[attr-defined]

    async def _acquire(cls: Any, path_arg: Path = path, *, timeout: float = 10.0) -> Any:
        return await real(cls, path_arg, timeout=timeout)

    monkeypatch.setattr(_sn.HeldElectionLock, "acquire", classmethod(_acquire))
    monkeypatch.setattr(_election, "_election_lock_timeout", lambda: 0.3)
    monkeypatch.delenv(_daemon_startup.SPAWNED_UNDER_LOCK_ENV, raising=False)
    return path


def _no_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(_sn_mod: Any) -> None:
        return None

    async def _free(_host: Any, _port: Any) -> bool:
        return False

    monkeypatch.setattr(_election, "_probe_alive_leader", _none)
    monkeypatch.setattr(_election, "_canonical_port_serves_octowright", _free)


async def test_an_unelected_daemon_holds_the_lock_until_released(
    lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_leader(monkeypatch)
    hold = await _daemon_startup.claim_daemon_start(http_host=None, http_port=None)
    assert hold is not None and hold.held
    # A peer's election now waits: the lock is taken until the lockfile is out.
    with pytest.raises(TimeoutError):
        async with _sn.async_election_lock(lock_path, timeout=0.1):
            pass
    hold.release()
    hold.release()  # idempotent: the lockfile hook and the finally both call it
    async with _sn.async_election_lock(lock_path, timeout=0.5):
        pass


async def test_a_daemon_spawned_under_the_lock_does_not_wait_for_it(
    lock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its spawner holds the lock; waiting for it would stall until the
    spawner gave up -- the stall restart once hit."""
    monkeypatch.setenv(_daemon_startup.SPAWNED_UNDER_LOCK_ENV, "1")

    async def _must_not_probe(*_a: Any) -> None:
        raise AssertionError("the spawner already checked under the lock")

    monkeypatch.setattr(_election, "_probe_alive_leader", _must_not_probe)
    async with _sn.async_election_lock(lock_path, timeout=0.5):
        hold = await _daemon_startup.claim_daemon_start(http_host=None, http_port=None)
    assert hold is not None and not hold.held
    # Consumed, so nothing this daemon spawns inherits it.
    assert _daemon_startup.SPAWNED_UNDER_LOCK_ENV not in os.environ


@pytest.mark.parametrize("which", ["lockfile", "canonical_port"])
async def test_a_live_leader_means_no_second_daemon(
    lock_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    _no_leader(monkeypatch)
    if which == "lockfile":

        async def _found(_sn_mod: Any) -> Any:
            return SimpleNamespace(mcp_url="http://127.0.0.1:6286/mcp/")

        monkeypatch.setattr(_election, "_probe_alive_leader", _found)
    else:

        async def _serves(_host: Any, _port: Any) -> bool:
            return True

        monkeypatch.setattr(_election, "_canonical_port_serves_octowright", _serves)
    assert await _daemon_startup.claim_daemon_start(http_host=None, http_port=None) is None
    # The lock is released on the way out, so the live leader's peers are not held up.
    async with _sn.async_election_lock(lock_path, timeout=0.5):
        pass


async def test_a_busy_lock_falls_back_to_the_probes(lock_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A peer holding the lock past the budget does not wedge the daemon; the
    probes still decide, and they find the leader that peer started."""
    seen: list[str] = []

    async def _found(_sn_mod: Any) -> Any:
        seen.append("probed")
        return SimpleNamespace(mcp_url="http://127.0.0.1:6286/mcp/")

    monkeypatch.setattr(_election, "_probe_alive_leader", _found)
    async with _sn.async_election_lock(lock_path, timeout=0.5):
        assert await _daemon_startup.claim_daemon_start(http_host=None, http_port=None) is None
    assert seen == ["probed"]


def test_spawn_daemon_marks_its_child_only_when_it_holds_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    envs: list[dict[str, str]] = []

    def _popen(args: list[str], **kwargs: Any) -> Any:
        envs.append(kwargs["env"])
        return SimpleNamespace(pid=7)

    monkeypatch.setattr(subprocess, "Popen", _popen)
    monkeypatch.setattr(_daemon, "_detach_candidates", lambda: [{}])
    monkeypatch.setenv(_daemon_startup.SPAWNED_UNDER_LOCK_ENV, "1")  # a stale one in our own env
    _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None)
    _daemon.spawn_daemon(http_host=None, http_port=None, idle_grace=None, under_election_lock=False)
    assert envs[0].get(_daemon_startup.SPAWNED_UNDER_LOCK_ENV) == "1"
    assert _daemon_startup.SPAWNED_UNDER_LOCK_ENV not in envs[1]


_SERVE_KW: dict[str, Any] = {
    "http_host": None,
    "http_port": None,
    "no_http": False,
    "keep_alive": False,
    "idle_grace": None,
    "no_singleton": False,
    "daemon_mode": True,
}


async def test_serve_starts_no_leader_when_the_claim_is_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.cli import serve as _serve

    async def _declined(**_kw: Any) -> None:
        return None

    async def _must_not_run(**_kw: Any) -> None:
        raise AssertionError("a live leader is serving; this daemon must not start")

    monkeypatch.setattr(_daemon_startup, "claim_daemon_start", _declined)
    monkeypatch.setattr(_serve, "_run_leader", _must_not_run)
    await _serve._serve_async(**_SERVE_KW)


async def test_serve_releases_the_lock_from_the_lockfile_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.cli import serve as _serve

    released: list[str] = []
    hold = SimpleNamespace(held=True, release=lambda: released.append("release"))

    async def _claimed(**_kw: Any) -> Any:
        return hold

    async def _run_leader(**kwargs: Any) -> None:
        kwargs["on_lockfile_written"]()  # what _on_http_bound does after write_lock
        assert released == ["release"]

    monkeypatch.setattr(_daemon_startup, "claim_daemon_start", _claimed)
    monkeypatch.setattr(_serve, "_run_leader", _run_leader)
    await _serve._serve_async(**_SERVE_KW)
    assert released == ["release", "release"]  # and once more on the way out (idempotent in the real lock)
