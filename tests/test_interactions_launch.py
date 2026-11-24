# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Controller ownership begins before setup awaits and ends in durable teardown."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool, launch_publish
from tests.test_pool_launch_cleanup import FakeBrowser, FakeBrowserType, FakeContext


@pytest.mark.parametrize("failure", ["install", "install_cancel", "later", "cancel"])
async def test_preparation_failure_durably_closes_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    controllers: list[Any] = []
    entered = asyncio.Event()
    context = FakeContext()
    browser = FakeBrowser(context)
    pool = BrowserPool(recordings_dir=tmp_path)
    pool._pw = SimpleNamespace(chromium=FakeBrowserType(browser))

    class Controller:
        def __init__(self, log_path: Path, **flags: bool) -> None:
            self.path = log_path.with_suffix(".interactions.jsonl")
            self.handle = self.path.open("w")
            self.closed = False
            self.worker: asyncio.Task[None] | None = None
            controllers.append(self)

        async def install(self, session: Any) -> None:
            assert session.interaction_controller is self
            self.worker = asyncio.create_task(asyncio.Event().wait())
            if failure == "install_cancel":
                entered.set()
                await asyncio.Event().wait()
            if failure == "install":
                raise RuntimeError("partial install failure")

        async def close(self) -> None:
            assert context.closed, "native context finalizes before sidecar closes"
            self.closed = True
            self.handle.close()
            if self.worker is not None:
                self.worker.cancel()
                await asyncio.gather(self.worker, return_exceptions=True)

    monkeypatch.setattr("octowright.interactions.InteractionController", Controller)

    async def later_failure(*_: Any, **__: Any) -> None:
        entered.set()
        if failure == "cancel":
            await asyncio.Event().wait()
        raise RuntimeError("later setup failure")

    monkeypatch.setattr(launch_publish, "wire_init_scripts", later_failure)
    task = asyncio.create_task(pool.launch(kind="chromium", headed=False, record_interactions=True))
    if failure in {"cancel", "install_cancel"}:
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(RuntimeError, match="failure"):
            await task
    assert len(controllers) == 1
    controller = controllers[0]
    assert controller.closed and controller.handle.closed
    assert controller.worker.done()
    assert context.closed and browser.closed
    assert pool.list_sessions() == []


async def test_normal_teardown_closes_controller_after_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright.session import core_teardown_helpers
    from tests.test_interactions_controls import session_with_controller

    session = session_with_controller(tmp_path)
    await session.interaction_controller.install(session)
    await session.set_interaction_visuals(record=True)
    controller = session.interaction_controller
    closed = asyncio.Event()

    async def close() -> None:
        closed.set()

    session.context.close = close
    monkeypatch.setattr(core_teardown_helpers, "describe_context_videos", lambda _: [])
    await session._teardown_after_close_cutoff()
    assert closed.is_set()
    assert controller._closed
    assert not controller._tasks
    assert controller._sink._stream.closed


@pytest.mark.parametrize("failure", ["cancel", "error"])
async def test_rollback_before_publication_closes_prepared_controller(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    from octowright.browser_pool import launch_pipeline

    registry_waiting = asyncio.Event()
    fail_acquire = asyncio.Event()

    class RegistryLock(asyncio.Lock):
        async def acquire(self) -> bool:
            if self.locked():
                registry_waiting.set()
                if failure == "error":
                    await fail_acquire.wait()
                    raise RuntimeError("publication lock failed")
            return await super().acquire()

    context = FakeContext()
    browser = FakeBrowser(context)
    pool = BrowserPool(recordings_dir=tmp_path)
    pool._pw = SimpleNamespace(chromium=FakeBrowserType(browser))
    pool._sessions_lock = RegistryLock()
    prepare = launch_pipeline._prepare_session_before_publication
    sessions: list[Any] = []

    async def capture_prepared(*args: Any, **kwargs: Any) -> Any:
        session = await prepare(*args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(launch_pipeline, "_prepare_session_before_publication", capture_prepared)
    try:
        async with pool._sessions_lock:
            task = asyncio.create_task(pool.launch(kind="chromium", headed=False, record_interactions=True))
            async with asyncio.timeout(2):
                await registry_waiting.wait()
            session = sessions[0]
            controller = session.interaction_controller
            assert not controller._sink._stream.closed
            assert not task.done()
            assert not pool.has_session(session.instance_id)
            if failure == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                fail_acquire.set()
                with pytest.raises(RuntimeError, match="publication lock failed"):
                    await task
        assert context.closed and browser.closed
        assert not pool.has_session(session.instance_id)
        assert controller._closed
        assert controller._sink._stream.closed
        assert not controller._tasks
    finally:
        if sessions:
            await sessions[0].interaction_controller.close()
