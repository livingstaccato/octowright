# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Public Playwright video identities and native capture through navigation."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from octowright.interactions import InteractionController, read_interactions
from octowright.session.operation.gate.core import SessionOperationGate

pytestmark = [pytest.mark.live_browser, pytest.mark.asyncio]


@pytest_asyncio.fixture(params=["chromium", "firefox", "webkit"])
async def capture(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[tuple[Any, Any, Path]]:
    async with async_playwright() as playwright:
        browser = await getattr(playwright, request.param).launch()
        context = await browser.new_context(
            record_video_dir=tmp_path / "videos", viewport={"width": 640, "height": 480}
        )
        await context.route(
            "http://**/*", lambda route: route.fulfill(body="<button>App</button>", content_type="text/html")
        )
        await context.new_page()
        log = tmp_path / "x.jsonl"
        controller = InteractionController(log)
        await controller.install(
            SimpleNamespace(
                context=context,
                kind=request.param,
                operation=SessionOperationGate("interactiontest", "chromium").operation,
            )
        )
        try:
            yield controller, context, log
        finally:
            await context.close()
            await controller.close()
            await browser.close()


async def click_and_capture(page: Any, log: Path, minimum: int) -> None:
    await page.evaluate("__octowrightInteractions.ready")
    await page.mouse.click(30, 30)
    async with asyncio.timeout(5):
        while sum(r["type"] == "interaction" for r in read_interactions(log)["events"]) < minimum:
            await asyncio.sleep(0.01)


async def test_navigation_and_tabs_keep_estimated_video_origins(capture: Any) -> None:
    controller, context, log = capture
    page = context.pages[0]
    await page.goto("http://capture.test/first")
    assert not log.with_suffix(".interactions.jsonl").exists()
    await controller.configure(record=True)
    await click_and_capture(page, log, 2)
    first = read_interactions(log)["events"]
    await page.set_viewport_size({"width": 320, "height": 240})
    await page.goto("http://capture.test/second")
    await click_and_capture(page, log, 4)
    other = await context.new_page()
    await other.goto("http://capture.test/other")
    await click_and_capture(other, log, 6)
    rows = read_interactions(log)["events"]
    events = [r for r in rows if r["type"] == "interaction"]
    first_page = events[0]["page_id"]
    matching = [r for r in events if r["page_id"] == first_page]
    assert matching[2]["t"] > matching[0]["t"]
    assert matching[2]["width"] == 320
    geometries = [r["video_geometry"] for r in rows if r["type"] == "page" and r["page_id"] == first_page]
    assert all(g == geometries[0] for g in geometries)
    assert geometries[0]["viewport_width"] == 640 and geometries[0]["viewport_height"] == 480
    assert geometries[0]["device_scale_factor"] == 1
    origins = [r["time_origin"] for r in rows if r["type"] == "page" and r["page_id"] == first_page]
    assert len(set(origins)) == 1
    assert all(r["time_origin_kind"] == "estimate" for r in first if r["type"] == "page")
    async with asyncio.timeout(5):
        while True:
            latest = {r["page_id"]: r for r in read_interactions(log)["events"] if r["type"] == "page"}
            if len(latest) == 2 and all(r["video"] for r in latest.values()):
                break
            await asyncio.sleep(0.01)
    assert len({r["video"] for r in latest.values()}) == 2
    assert all("/" not in r["video"] for r in latest.values())
