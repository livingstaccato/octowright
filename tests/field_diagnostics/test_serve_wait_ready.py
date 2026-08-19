# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``serve --wait-ready``: start it and tell me when it's ready.

Field report: every workflow hand-rolled the same thing -- background
``serve --keep-alive``, poll for the lockfile in bash/pwsh, print a guess
when it never appeared. ``wait_for_daemon`` already did this internally.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from octowright.cli._root import cli


@contextlib.asynccontextmanager
async def _lock_granted(*_args: Any, **_kwargs: Any) -> Any:
    yield


class _LockContended:
    """Another instance already holds the election lock."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> None:
        raise TimeoutError

    async def __aexit__(self, *_exc: Any) -> None:
        return None


LEADER = SimpleNamespace(mcp_url="http://127.0.0.1:6286/mcp")


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the leader-election + daemon seams; record what got called."""
    from octowright import daemonize as _daemon
    from octowright import singleton as _sn
    from octowright.cli import _leader_election as _election

    calls: dict[str, Any] = {"spawned": 0, "alive": None, "adopt": None, "waited": LEADER}

    async def _probe(_sn_mod: Any) -> Any:
        return calls["alive"]

    async def _adopt(_sn_mod: Any, _host: Any, _port: Any) -> Any:
        return calls["adopt"]

    def _spawn(**_kwargs: Any) -> int:
        calls["spawned"] += 1
        return 999

    async def _wait(*_args: Any, **_kwargs: Any) -> Any:
        return calls["waited"]

    monkeypatch.setattr(_election, "_probe_alive_leader", _probe)
    monkeypatch.setattr(_election, "_adopt_canonical_leader", _adopt)
    monkeypatch.setattr(_daemon, "spawn_daemon", _spawn)
    monkeypatch.setattr(_daemon, "wait_for_daemon", _wait)
    monkeypatch.setattr(_sn, "async_election_lock", _lock_granted)
    monkeypatch.setattr(_daemon, "daemon_log_tail", lambda *_a, **_k: "boom: port already in use")
    return calls


def _run(*args: str) -> Any:
    return CliRunner().invoke(cli, ["serve", "--wait-ready", *args])


def test_existing_leader_is_reported_without_spawning(wired: dict[str, Any]) -> None:
    wired["alive"] = LEADER

    result = _run()

    assert result.exit_code == 0
    assert result.stdout.strip() == LEADER.mcp_url
    assert wired["spawned"] == 0


def test_spawns_and_reports_when_no_leader_exists(wired: dict[str, Any]) -> None:
    result = _run()

    assert result.exit_code == 0
    assert result.stdout.strip() == LEADER.mcp_url
    assert wired["spawned"] == 1


def test_adopts_a_canonical_port_leader_instead_of_competing(wired: dict[str, Any]) -> None:
    wired["adopt"] = LEADER

    result = _run()

    assert result.exit_code == 0
    assert wired["spawned"] == 0


def test_failure_exits_nonzero_and_quotes_the_daemon_log(wired: dict[str, Any]) -> None:
    """The whole point: a workflow must get an exit code AND a reason."""
    wired["waited"] = None

    result = _run()

    assert result.exit_code == 1
    assert "did not become ready" in result.stderr
    assert "--ready-timeout" in result.stderr
    assert "boom: port already in use" in result.stderr


def test_contended_election_does_not_claim_readiness(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import singleton as _sn

    monkeypatch.setattr(_sn, "async_election_lock", _LockContended)

    result = _run()

    assert result.exit_code == 1
    assert "electing a leader" in result.stderr


def test_ready_timeout_flag_is_exported_for_every_wait(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Exported rather than passed once, so the post-bridge respawn's own
    wait_for_daemon() call honours the same budget."""
    import os

    from octowright import daemonize as _daemon

    seen: dict[str, str | None] = {}

    async def _wait(*_args: Any, **_kwargs: Any) -> Any:
        seen["env"] = os.environ.get(_daemon.DAEMON_READY_TIMEOUT_ENV)
        return LEADER

    monkeypatch.setattr(_daemon, "wait_for_daemon", _wait)
    # setenv, not delenv: monkeypatch.delenv(raising=False) records NOTHING
    # when the variable is absent, so the CLI's own os.environ[...] = "90.0"
    # would survive teardown and give every later test in this worker a 90s
    # readiness budget.
    monkeypatch.setenv(_daemon.DAEMON_READY_TIMEOUT_ENV, "")
    monkeypatch.delenv(_daemon.DAEMON_READY_TIMEOUT_ENV)

    assert _run("--ready-timeout", "90").exit_code == 0
    assert seen["env"] == "90.0"


def test_wait_ready_never_falls_back_to_serving_inline(wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """A script's contract is an exit code. The inline fallback that is right
    for an MCP client (serve in the foreground so the user gets *a* server)
    would block a workflow forever instead of failing it."""
    from octowright.cli import serve as serve_mod

    ran_inline = False

    async def _run_leader(**_kwargs: Any) -> None:
        nonlocal ran_inline
        ran_inline = True

    monkeypatch.setattr(serve_mod, "_run_leader", _run_leader)
    wired["waited"] = None

    result = _run()

    assert result.exit_code == 1
    assert ran_inline is False


def test_ready_timeout_does_not_leak_into_later_tests(wired: dict[str, Any]) -> None:
    """monkeypatch.delenv(raising=False) records no restore when the variable
    is absent, so a naive test left the CLI's own export in place and gave
    every later test in the worker a 90s readiness budget."""
    import os

    from octowright import daemonize as _daemon

    assert os.environ.get(_daemon.DAEMON_READY_TIMEOUT_ENV) in (None, "")


def test_election_lock_wait_exceeds_the_readiness_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The holder keeps the election lock across wait_for_daemon, so a raised
    --ready-timeout must not make every other `serve` give up on the lock
    first and treat ordinary contention as an error."""
    from octowright.cli import _daemon_ready as _ready

    monkeypatch.setenv("OCTOWRIGHT_DAEMON_READY_TIMEOUT", "60")

    assert _ready._election_lock_timeout() > 60.0
