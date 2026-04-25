# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lockfile read/write/staleness tests for the singleton module.

Every test funnels the lockfile path through ``tmp_path`` so the suite never
touches ``~/.config/undef/octowright.lock``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from octowright import singleton


def test_round_trip(tmp_path: Path) -> None:
    """Write then read recovers the same record."""
    lock = tmp_path / "octowright.lock"
    info = singleton.make_leader_info("127.0.0.1", 8765)
    singleton.write_lock(info, path=lock)

    loaded = singleton.read_lock(path=lock)
    assert loaded is not None
    assert loaded.pid == os.getpid()
    assert loaded.http_port == 8765
    assert loaded.mcp_url == "http://127.0.0.1:8765/mcp/"


def test_read_lock_missing_returns_none(tmp_path: Path) -> None:
    assert singleton.read_lock(path=tmp_path / "nope.lock") is None


def test_read_lock_corrupt_returns_none(tmp_path: Path) -> None:
    """A garbled lockfile should not crash the boot path — treat as absent."""
    lock = tmp_path / "octowright.lock"
    lock.write_text("{not json")
    assert singleton.read_lock(path=lock) is None


def test_remove_lock_idempotent(tmp_path: Path) -> None:
    lock = tmp_path / "octowright.lock"
    singleton.remove_lock(path=lock)  # missing — must not raise
    lock.write_text("{}")
    singleton.remove_lock(path=lock)
    assert not lock.exists()
    singleton.remove_lock(path=lock)  # already gone — must not raise


def test_pid_is_alive_for_self() -> None:
    assert singleton.pid_is_alive(os.getpid()) is True


def test_pid_is_alive_for_dead_pid() -> None:
    """PID 0 is never a real process; PID 2_000_000_000 is well beyond max_pid."""
    assert singleton.pid_is_alive(0) is False
    assert singleton.pid_is_alive(2_000_000_000) is False


def test_is_stale_when_pid_dead() -> None:
    info = singleton.LeaderInfo(
        pid=2_000_000_000,
        http_host="127.0.0.1",
        http_port=8765,
        mcp_url="http://127.0.0.1:8765/mcp",
        started_at=0.0,
    )
    assert singleton.is_stale(info) is True


def test_is_stale_when_pid_alive() -> None:
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=8765,
        mcp_url="http://127.0.0.1:8765/mcp",
        started_at=0.0,
    )
    assert singleton.is_stale(info) is False


@pytest.mark.asyncio
async def test_probe_http_alive_against_running_starlette() -> None:
    """A running uvicorn answering /api/health is reported as alive."""
    import asyncio

    import uvicorn
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(_request):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/health", health)])
    config = uvicorn.Config(app, host="127.0.0.1", port=18768, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)
    try:
        info = singleton.LeaderInfo(
            pid=os.getpid(),
            http_host="127.0.0.1",
            http_port=18768,
            mcp_url="http://127.0.0.1:18768/mcp/",
            started_at=0.0,
        )
        assert await singleton.probe_http_alive(info, timeout=2.0) is True
    finally:
        server.should_exit = True
        await server_task


@pytest.mark.asyncio
async def test_probe_http_alive_against_dead_port() -> None:
    """A port with nothing listening is reported as dead within the timeout."""
    info = singleton.LeaderInfo(
        pid=os.getpid(),
        http_host="127.0.0.1",
        http_port=1,  # privileged + nothing listening = guaranteed connect refused
        mcp_url="http://127.0.0.1:1/mcp/",
        started_at=0.0,
    )
    assert await singleton.probe_http_alive(info, timeout=1.0) is False


def test_write_lock_atomically_replaces_existing(tmp_path: Path) -> None:
    """write_lock should overwrite an old leader entry without leaving the temp file."""
    lock = tmp_path / "octowright.lock"
    old = singleton.LeaderInfo(pid=1, http_host="127.0.0.1", http_port=1111, mcp_url="x", started_at=0.0)
    singleton.write_lock(old, path=lock)

    new = singleton.make_leader_info("127.0.0.1", 2222)
    singleton.write_lock(new, path=lock)

    loaded = singleton.read_lock(path=lock)
    assert loaded is not None
    assert loaded.http_port == 2222
    # No leftover .tmp files in the directory.
    leftovers = [p for p in lock.parent.iterdir() if p.suffix == ".tmp" or ".tmp" in p.name]
    assert leftovers == []
