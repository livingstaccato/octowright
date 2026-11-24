# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Same native input through the pool, observer, private sidecar and native video."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool import BrowserPool
from octowright.interactions import read_interactions

pytestmark = [pytest.mark.live_browser, pytest.mark.asyncio]
PAGE = """<style>body{margin:0}button{position:absolute;left:30px;top:30px;width:180px;height:100px}</style>
<button id="target">Application</button><script>
window.appEvents=[];
for(const type of ['pointerdown','pointerup','mousedown','mouseup','click','dblclick','contextmenu','wheel'])
 addEventListener(type,e=>appEvents.push([type,e.button,e.pointerType||'',e.defaultPrevented,e.target.id]));
</script>"""
SHADOW_INSPECTOR = """(() => {
 const attach=Element.prototype.attachShadow; window.testShadows=new WeakMap();
 Element.prototype.attachShadow=function(options){const root=attach.call(this,options);testShadows.set(this,root);return root};
})()"""


async def input_sequence(session: Any) -> list[Any]:
    page = session.page
    await page.evaluate("appEvents=[]")
    await session.click("#target")
    await page.mouse.click(80, 70, button="right")
    await page.mouse.click(80, 70, button="middle")
    await page.mouse.dblclick(80, 70)
    await page.mouse.move(80, 70)
    # Cross the documented host 20 Hz move-coalescing interval before moving.
    await page.mouse.down()
    started = await page.evaluate("performance.now()")
    await page.wait_for_function("started=>performance.now()-started>=60", arg=started)
    await page.mouse.move(150, 90, steps=4)
    await page.evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
    await page.mouse.up()
    await session.scroll(delta_x=0, delta_y=80, selector="#target")
    await page.wait_for_function("appEvents.some(e=>e[0]==='wheel')")
    return await page.evaluate("appEvents")


async def captured(log: Path, minimum: int) -> list[dict[str, Any]]:
    async with asyncio.timeout(5):
        while True:
            rows = read_interactions(log)["events"]
            if sum(r["type"] == "interaction" for r in rows) >= minimum:
                return rows
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("engine", ["chromium", "firefox", "webkit"])
async def test_pool_native_input_modes_frames_navigation_and_video(tmp_path: Path, engine: str) -> None:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        launched = await pool.launch(
            kind=engine,
            url="about:blank",
            headed=False,
            ephemeral=True,
            record_video=True,
            viewport_w=640,
            viewport_h=480,
        )
        session = pool.get(launched["instance_id"])
        page = session.page
        await session.context.add_init_script(SHADOW_INSPECTOR)
        await session.context.route("http://**/*", lambda route: route.fulfill(body=PAGE, content_type="text/html"))
        await page.goto("http://integration.test/first")
        await page.evaluate("__octowrightInteractions.ready")
        baseline = await input_sequence(session)
        baseline_focus = await page.evaluate("document.activeElement.id")
        assert not read_interactions(session.log_path)["available"]
        for show, record in [(True, False), (False, True), (True, True)]:
            await session.set_interaction_visuals(show=show, record=record)
            before = sum(r["type"] == "interaction" for r in read_interactions(session.log_path)["events"])
            assert await input_sequence(session) == baseline
            assert await page.evaluate("document.activeElement.id") == baseline_focus
            assert read_interactions(session.log_path)["available"] is record
            if record:
                rows = await captured(session.log_path, before + 14)
                observations = [r for r in rows if r["type"] == "interaction"][before:]
                assert {r["button"] for r in observations if r["phase"] == "down"} == {0, 1, 2}
                assert {r["phase"] for r in observations} >= {"down", "up", "move", "double", "wheel"}
                assert all(r["shown"] is show for r in observations)
        # A held ring is visible and disabling removes the closed-root layer.
        await page.mouse.move(80, 70)
        await page.mouse.down()
        await page.wait_for_function(
            "testShadows.get(document.getElementById('__octowright_interactions__'))?.textContent.includes('Hold')"
        )
        await session.set_interaction_visuals(show=False, record=False)
        assert await page.locator("#__octowright_interactions__").count() == 0
        await page.mouse.up()
        prior = read_interactions(session.log_path)["events"]
        await session.click("#target")
        await page.evaluate("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
        assert read_interactions(session.log_path)["events"] == prior
        # Future documents inherit host state, and child coordinates map to the top viewport.
        await session.set_interaction_visuals(show=False, record=True)
        await page.evaluate(
            """() => {const f=document.createElement('iframe');f.src='http://frame.test/';f.style.cssText='position:absolute;left:250px;top:170px;width:300px;height:200px;border:0';document.body.append(f)}"""
        )
        child = page.frame_locator("iframe").locator("#target")
        await child.wait_for()
        frame = next(f for f in page.frames if "frame.test" in f.url)
        await frame.evaluate("__octowrightInteractions.ready")
        before = sum(r["type"] == "interaction" for r in read_interactions(session.log_path)["events"])
        box = await child.bounding_box()
        assert box
        await page.mouse.click(box["x"] + 20, box["y"] + 20)
        rows = await captured(session.log_path, before + 2)
        down = [r for r in rows if r["type"] == "interaction"][before]
        assert down["x"] == pytest.approx(box["x"] + 20, abs=1)
        assert down["y"] == pytest.approx(box["y"] + 20, abs=1)
        first_id, first_t = down["page_id"], down["t"]
        await page.goto("http://integration.test/next")
        await page.evaluate("__octowrightInteractions.ready")
        await session.click("#target")
        rows = await captured(session.log_path, before + 4)
        assert [r for r in rows if r["type"] == "interaction"][-1]["t"] > first_t
        other = await session.context.new_page()
        await other.goto("http://integration.test/new-tab")
        await other.evaluate("__octowrightInteractions.ready")
        await other.mouse.click(80, 70)
        await captured(session.log_path, before + 6)
        controller, log = session.interaction_controller, session.log_path
        video_paths = [Path(await p.video.path()) for p in session.context.pages]
        await pool.close(session.instance_id, force=True)
        rows = read_interactions(log)["events"]
        pages = {r["page_id"]: r for r in rows if r["type"] == "page"}
        assert len(pages) == 2 and first_id in pages
        assert {r["video"] for r in pages.values()} == {p.name for p in video_paths}
        assert all(p.is_file() and p.stat().st_size > 1000 for p in video_paths)
        assert all(r["time_origin_kind"] == "estimate" for r in pages.values())
        assert controller._closed and not controller._tasks and controller._sink._stream.closed
        actions = [json.loads(line).get("action") for line in log.read_text(encoding="utf-8").splitlines()]
        assert "click" in actions and "scroll" in actions and "interaction" not in actions
    finally:
        await pool.shutdown()
