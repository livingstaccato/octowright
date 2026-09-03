# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lockfile read/write/staleness tests for the singleton module.

Every test funnels the lockfile path through ``tmp_path`` so the suite never
touches the user's real Octowright config directory.
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


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits don't apply to Windows lockfile")
def test_write_lock_chmod_0600_and_parent_0700(tmp_path: Path) -> None:
    """The lockfile contains pid/host/port/mcp_url — sensitive enough that
    other local users shouldn't read or tamper with it. Pin the mode bits."""
    parent = tmp_path / "state"
    lock = parent / "octowright.lock"
    info = singleton.make_leader_info("127.0.0.1", 8765)
    singleton.write_lock(info, path=lock)

    file_mode = lock.stat().st_mode & 0o777
    parent_mode = parent.stat().st_mode & 0o777
    assert file_mode == 0o600, f"lockfile mode {oct(file_mode)} expected 0o600"
    assert parent_mode == 0o700, f"parent dir mode {oct(parent_mode)} expected 0o700"


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

    from tests.conftest import _free_port

    async def health(_request):  # type: ignore[no-untyped-def]
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/api/health", health)])
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    # Wait for uvicorn to report itself bound, rather than guessing how long
    # that takes. This was a flat `sleep(0.5)`: on a loaded runner the socket
    # was not listening yet, so `probe_http_alive` got a connection refused and
    # the test failed asserting `is True` -- a failure about the sleep, not
    # about the probe. `Server.started` is set at the end of uvicorn's own
    # startup, so it answers the actual question; the deadline only bounds a
    # server that never comes up at all.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10.0
    while not server.started and loop.time() < deadline:
        await asyncio.sleep(0.01)
    assert server.started, "uvicorn did not bind within the deadline"
    try:
        info = singleton.LeaderInfo(
            pid=os.getpid(),
            http_host="127.0.0.1",
            http_port=port,
            mcp_url=f"http://127.0.0.1:{port}/mcp/",
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


def test_pid_is_alive_windows_reads_last_error_before_closehandle(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, GetLastError must be queried before any other Win32 call.
    CloseHandle (and the C runtime itself) can clobber the thread-local
    last-error code, so reading it after would misclassify ERROR_ACCESS_DENIED
    (alive but owned by another user) as "dead". The test runs on any
    platform — we inject a fake kernel32 to verify call ordering, since real
    Windows behaviour can only be reproduced on a Windows host."""
    from octowright import singleton as _sg

    call_order: list[str] = []

    class FakeKernel32:
        def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:
            call_order.append("OpenProcess")
            return 0  # NULL handle → forces the GetLastError branch

        def CloseHandle(self, _handle: int) -> int:
            call_order.append("CloseHandle")
            return 1

        def GetLastError(self) -> int:
            call_order.append("GetLastError")
            return 5  # ERROR_ACCESS_DENIED → "alive but not ours"

    class FakeWindll:
        kernel32 = FakeKernel32()

    class FakeCtypes:
        windll = FakeWindll()

    # Replace the lazy __import__("ctypes") so the function picks up our fake
    # without us having to depend on running on Windows.
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "ctypes":
            return FakeCtypes
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert _sg._pid_is_alive_windows(12345) is True
    # The critical invariant: GetLastError must come before CloseHandle is NOT
    # what we want — when OpenProcess returns NULL we never call CloseHandle.
    # The real ordering hazard is on the *success* path; verify it there too.
    assert call_order == ["OpenProcess", "GetLastError"]

    # Success path: OpenProcess returns a non-null handle. CloseHandle is
    # called; GetLastError is not consulted (early return True), so ordering
    # is moot. But the *bug* this guards against is: if we ever change the
    # function to read GetLastError on the success path too, it must come
    # before CloseHandle. Cover that future-proofing with a second probe.
    call_order.clear()

    class FakeKernel32Success:
        def OpenProcess(self, _access: int, _inherit: bool, _pid: int) -> int:
            call_order.append("OpenProcess")
            return 0xDEADBEEF

        def CloseHandle(self, _handle: int) -> int:
            call_order.append("CloseHandle")
            return 1

        def GetLastError(self) -> int:
            call_order.append("GetLastError")
            return 0

    FakeWindll.kernel32 = FakeKernel32Success()  # type: ignore[assignment]
    assert _sg._pid_is_alive_windows(12345) is True
    assert call_order == ["OpenProcess", "CloseHandle"]


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
