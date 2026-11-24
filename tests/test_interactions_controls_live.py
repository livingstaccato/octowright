# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Actual pool launches install before navigation and preserve runtime settings."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from octowright.browser_pool import BrowserPool
from octowright.interactions import read_interactions

pytestmark = [pytest.mark.live_browser, pytest.mark.asyncio]


@pytest.mark.parametrize("show,record", [(False, False), (True, False), (False, True)])
async def test_launch_installs_independent_flags_and_runtime_controls(
    tmp_path: Path, show: bool, record: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.server.browser import interactions

    pool = BrowserPool(recordings_dir=tmp_path)
    monkeypatch.setattr(interactions, "pool", pool)
    try:
        result = await pool.launch(
            kind="chromium",
            url="about:blank",
            headed=False,
            ephemeral=True,
            show_interactions=show,
            record_interactions=record,
            record_video=record,
        )
        session = pool.get(result["instance_id"])
        assert result["show_interactions"] is show
        assert result["record_interactions"] is record
        assert pool.list_sessions()[0]["show_interactions"] is show
        launch = json.loads(session.log_path.read_text().splitlines()[0])
        assert (launch["show_interactions"], launch["record_interactions"]) == (show, record)
        assert read_interactions(session.log_path)["available"] is record
        await session.page.evaluate("__octowrightInteractions.ready")
        await session.context.route("http://**/*", lambda route: route.fulfill(body="<button>App</button>"))
        await session.page.goto("http://controls.test/first")
        await session.page.evaluate("__octowrightInteractions.ready")
        await interactions.browser_set_interaction_visuals(session.instance_id, show=False, record=True)
        page = await session.context.new_page()
        await page.goto("http://controls.test/new")
        await page.evaluate("__octowrightInteractions.ready")
        await page.mouse.click(30, 30)
        async with asyncio.timeout(5):
            while not any(r["type"] == "interaction" for r in read_interactions(session.log_path)["events"]):
                await asyncio.sleep(0.01)
        rows = read_interactions(session.log_path)["events"]
        assert all(r["shown"] is False for r in rows if r["type"] == "interaction")
        assert session.launch_options.record_interactions is True
        controller = session.interaction_controller
        await pool.close(session.instance_id, force=True)
        assert controller._closed and not controller._tasks
        assert controller._sink._stream.closed
        if record:
            assert any(row["type"] == "page" and row["video"] for row in read_interactions(session.log_path)["events"])
    finally:
        await pool.shutdown()


async def test_external_context_close_drains_controller(tmp_path: Path) -> None:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        launched = await pool.launch(
            kind="chromium", url="about:blank", headed=False, ephemeral=True, record_interactions=True
        )
        session = pool.get(launched["instance_id"])
        controller = session.interaction_controller
        await session.context.close()
        async with asyncio.timeout(5):
            while not controller._closed or not controller._sink._stream.closed:
                await asyncio.sleep(0.01)
        assert not controller._tasks
        assert not pool.has_session(session.instance_id)
    finally:
        await pool.shutdown()


async def test_cancelled_close_caller_does_not_abandon_controller(tmp_path: Path) -> None:
    pool = BrowserPool(recordings_dir=tmp_path)
    release = asyncio.Event()
    try:
        launched = await pool.launch(
            kind="chromium", url="about:blank", headed=False, ephemeral=True, record_interactions=True
        )
        session = pool.get(launched["instance_id"])
        controller = session.interaction_controller
        native_close = session.context.close
        entered = asyncio.Event()

        async def close() -> None:
            entered.set()
            await release.wait()
            await native_close()

        session.context.close = close
        task = asyncio.create_task(pool.close(session.instance_id, force=True))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        async with asyncio.timeout(5):
            while not controller._closed or not controller._sink._stream.closed:
                await asyncio.sleep(0.01)
        assert not controller._tasks
    finally:
        release.set()
        await pool.shutdown()
