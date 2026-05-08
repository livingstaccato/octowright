# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``_run_leader`` keeps serving HTTP-MCP after the stdio client disconnects.

Stub out the three long-running tasks (mcp.run_stdio_async, http.serve_app,
idle_watchdog) with asyncio.Event-driven coroutines so we can fire them in
controlled order and assert which ones the leader cancels vs. waits on.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from octowright import idle_watchdog as _watchdog_mod
from octowright import singleton as _singleton
from octowright.cli import serve as _serve


class _Stubs:
    """Holds the events that drive each stubbed long-running task."""

    def __init__(self) -> None:
        self.stdio_done = asyncio.Event()
        self.http_done = asyncio.Event()
        self.watchdog_done = asyncio.Event()
        self.http_started = asyncio.Event()
        self.watchdog_started = asyncio.Event()


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> _Stubs:
    s = _Stubs()

    async def fake_stdio() -> None:
        await s.stdio_done.wait()

    async def fake_http(**kwargs: Any) -> None:
        # Honour the on_bound callback so the lockfile gets written like in
        # production. The test path uses a tmp lockfile so this is hermetic.
        on_bound = kwargs.get("on_bound")
        if on_bound is not None:
            on_bound("127.0.0.1", 18999)
        s.http_started.set()
        await s.http_done.wait()

    async def fake_watchdog(*_args: Any, **_kwargs: Any) -> None:
        s.watchdog_started.set()
        await s.watchdog_done.wait()

    # FastMCP instance — replace its method on the live object.
    from octowright.server import _state as _server_state

    monkeypatch.setattr(_server_state.mcp, "run_stdio_async", fake_stdio)

    # Replace the http.serve_app symbol that _run_leader imports.
    from octowright import http as _http_pkg

    monkeypatch.setattr(_http_pkg, "serve_app", fake_http)

    # Replace idle_watchdog at the module level (imported with `from x import y`).
    monkeypatch.setattr(_watchdog_mod, "idle_watchdog", fake_watchdog)

    return s


@pytest.fixture
def isolated_lockfile(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """Redirect the lockfile path so tests don't touch ~/.config/."""
    lock = tmp_path / "octowright.lock"

    real_write = _singleton.write_lock
    real_read = _singleton.read_lock
    real_remove = _singleton.remove_lock

    monkeypatch.setattr(_singleton, "write_lock", lambda info, path=lock: real_write(info, path=path))
    monkeypatch.setattr(_singleton, "read_lock", lambda path=lock: real_read(path=path))
    monkeypatch.setattr(_singleton, "remove_lock", lambda path=lock: real_remove(path=path))
    return lock


def _kwargs() -> dict[str, Any]:
    return {
        "http_host": None,
        "http_port": None,
        "no_http": False,
        "keep_alive": False,
        "idle_grace": None,
        "no_singleton": False,
    }


@pytest.mark.asyncio
async def test_leader_stays_alive_after_stdio_eof(stubs: _Stubs, isolated_lockfile: Any) -> None:
    """Stdio EOF on a discoverable leader → keep waiting on the watchdog."""
    leader_task = asyncio.create_task(_serve._run_leader(**_kwargs()))

    # Wait for sidecars to come up.
    await asyncio.wait_for(stubs.http_started.wait(), timeout=2.0)
    await asyncio.wait_for(stubs.watchdog_started.wait(), timeout=2.0)
    assert isolated_lockfile.exists(), "leader should write lockfile on http bind"

    # Stdio client disconnects (e.g. Claude Code closes).
    stubs.stdio_done.set()
    await asyncio.sleep(0.1)

    # Leader must NOT have exited — it should still be waiting on the watchdog.
    assert not leader_task.done(), "leader should stay alive after stdio EOF"

    # Now fire the watchdog — that's the real shutdown signal.
    stubs.watchdog_done.set()
    await asyncio.wait_for(leader_task, timeout=2.0)
    assert not isolated_lockfile.exists(), "leader should remove lockfile on exit"


@pytest.mark.asyncio
async def test_leader_exits_immediately_when_no_http_and_stdio_eofs(stubs: _Stubs, isolated_lockfile: Any) -> None:
    """With --no-http there's no HTTP-MCP for followers; stdio EOF must end us."""
    kwargs = _kwargs()
    kwargs["no_http"] = True
    leader_task = asyncio.create_task(_serve._run_leader(**kwargs))

    await asyncio.wait_for(stubs.watchdog_started.wait(), timeout=2.0)
    # http_started should NEVER fire because --no-http.
    assert not stubs.http_started.is_set()

    stubs.stdio_done.set()
    # No "stay alive" path — leader should exit on stdio EOF.
    await asyncio.wait_for(leader_task, timeout=2.0)


@pytest.mark.asyncio
async def test_leader_exits_immediately_when_no_singleton_and_stdio_eofs(stubs: _Stubs, isolated_lockfile: Any) -> None:
    """--no-singleton means no lockfile, so followers can't find us anyway."""
    kwargs = _kwargs()
    kwargs["no_singleton"] = True
    leader_task = asyncio.create_task(_serve._run_leader(**kwargs))

    await asyncio.wait_for(stubs.http_started.wait(), timeout=2.0)
    # No lockfile written when --no-singleton.
    assert not isolated_lockfile.exists()

    stubs.stdio_done.set()
    await asyncio.wait_for(leader_task, timeout=2.0)


@pytest.mark.asyncio
async def test_leader_exits_when_watchdog_fires_first(stubs: _Stubs, isolated_lockfile: Any) -> None:
    """Watchdog fires (idle pool grace expired) → exit normally even before stdio EOF."""
    leader_task = asyncio.create_task(_serve._run_leader(**_kwargs()))

    await asyncio.wait_for(stubs.watchdog_started.wait(), timeout=2.0)
    stubs.watchdog_done.set()
    await asyncio.wait_for(leader_task, timeout=2.0)


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="Windows os.kill(SIGTERM) terminates the process.")
async def test_sigterm_translates_to_graceful_stdio_close(stubs: _Stubs, isolated_lockfile: Any) -> None:
    """SIGTERM on a discoverable leader must NOT exit; treat as stdio EOF.

    Claude Code (and similar MCP clients) send SIGTERM to their child stdio
    processes on close. Without the signal handler, the asyncio loop dies
    instantly and takes all the browsers down with it.
    """
    import os
    import signal

    leader_task = asyncio.create_task(_serve._run_leader(**_kwargs()))
    await asyncio.wait_for(stubs.http_started.wait(), timeout=2.0)
    await asyncio.wait_for(stubs.watchdog_started.wait(), timeout=2.0)

    # Fire SIGTERM at this very process — the leader's signal handler should
    # convert it into a graceful mcp_task.cancel() and the keep-alive path
    # below should kick in.
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(0.2)

    # If signal handling is wired correctly, the leader is still waiting on
    # the watchdog (mcp_task is cancelled, but the keep-alive logic took over).
    assert not leader_task.done(), "leader should survive SIGTERM when discoverable"

    stubs.watchdog_done.set()
    await asyncio.wait_for(leader_task, timeout=2.0)
