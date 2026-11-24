# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Raw Playwright coverage, independent of pool/controller/driver integration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from playwright.async_api import Page, async_playwright

pytestmark = [pytest.mark.live_browser, pytest.mark.asyncio]
ASSET = Path(__file__).resolve().parents[1] / "src/octowright/browser_pool/_assets/interactions.js"
PAGE = """<style>body {margin:0;height:4000px} button {position:absolute;left:20px;top:20px;width:160px;height:100px}</style>
<button id="target">Application</button><script>
window.appEvents=[];
for (const type of ['pointerdown','pointerup','mousedown','mouseup','click','dblclick','contextmenu','wheel'])
 addEventListener(type,e=>appEvents.push([type,e.button,e.pointerType||'',e.defaultPrevented,e.target.id]));
</script>"""


@pytest_asyncio.fixture(params=["chromium", "firefox", "webkit"])
async def observer_page(request: pytest.FixtureRequest) -> AsyncIterator[tuple[Page, asyncio.Queue[dict[str, Any]]]]:
    async with async_playwright() as playwright:
        browser = await getattr(playwright, request.param).launch()
        context = await browser.new_context(viewport={"width": 800, "height": 600}, has_touch=True)
        events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def record(_source: Any, payload: dict[str, Any]) -> None:
            events.put_nowait(payload)

        await context.expose_binding(
            "__octowrightInteractionConfig", lambda _source, _payload: {"show": True, "record": True}
        )
        await context.expose_binding("__octowrightInteractionEvent", record)
        # Capture closed roots outside the production observer; this is not a
        # page API shipped by the asset. Keep ordering within one init script.
        inspect_shadow = """(() => {
          window.__observerShadows = new WeakMap();
          const attach = Element.prototype.attachShadow;
          Element.prototype.attachShadow = function(options) {
            const shadow = attach.call(this,options);
            window.__observerShadows.set(this,shadow); return shadow;
          };
        })();"""
        await context.add_init_script(inspect_shadow + (ASSET.read_text(encoding="utf-8") if ASSET.exists() else ""))
        await context.route("http://**/*", lambda route: route.fulfill(body=PAGE, content_type="text/html"))
        page = await context.new_page()
        await page.goto("http://observer.test/")
        assert await page.evaluate("Boolean(window.__octowrightInteractions)"), "document controller must be installed"
        await page.evaluate("window.__octowrightInteractions.ready")
        yield page, events
        await context.close()
        await browser.close()


async def take(events: asyncio.Queue[dict[str, Any]], phase: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            event = await events.get()
            if event["phase"] == phase:
                return event


async def markers(page: Any) -> list[dict[str, Any]]:
    return await page.evaluate(
        """() => [...(window.__observerShadows.get(document.getElementById('__octowright_interactions__'))?.querySelectorAll('[data-phase]')||[])].map(n=>({label:n.textContent,phase:n.dataset.phase,x:n.style.left,y:n.style.top}))"""
    )


async def test_native_buttons_drag_wheel_and_application_sequence(observer_page: Any) -> None:
    page, events = observer_page

    async def actions() -> list[Any]:
        await page.evaluate("appEvents=[]")
        await page.mouse.click(80, 70, button="right")
        await page.mouse.click(80, 70, button="middle")
        await page.mouse.dblclick(80, 70)
        await page.mouse.move(80, 70)
        await page.mouse.down()
        await page.mouse.move(150, 90, steps=3)
        await page.mouse.up()
        await page.mouse.wheel(0, 80)
        await page.wait_for_function("appEvents.some(e=>e[0]==='wheel')")
        return await page.evaluate("appEvents")

    await page.evaluate("__octowrightInteractions.configure({show:false,record:false})")
    baseline = await actions()
    baseline_focus = await page.evaluate("document.activeElement.id")
    await page.evaluate("scrollTo(0,0);__octowrightInteractions.configure({show:true,record:true})")
    actual = await actions()
    assert actual == baseline
    assert await page.evaluate("document.activeElement.id") == baseline_focus
    recorded = []
    while not events.empty():
        recorded.append(events.get_nowait())
    assert {row["button"] for row in recorded if row["phase"] == "down"} == {0, 1, 2}
    assert sum(row["phase"] == "double" for row in recorded) == 1
    assert any(row["phase"] == "move" for row in recorded)
    assert any(row["phase"] == "wheel" and row["dy"] > 0 for row in recorded)
    assert all(row["shown"] for row in recorded)


async def test_native_hold_touch_and_runtime_cleanup(observer_page: Any) -> None:
    page, events = observer_page
    await page.clock.install(time=1000)
    await page.clock.pause_at(2000)
    await page.mouse.move(80, 70)
    await page.mouse.down()
    await take(events, "down")
    await page.clock.fast_forward(499)
    assert (await markers(page))[0]["label"] == "Left"
    await page.clock.fast_forward(1)
    assert (await markers(page))[0]["label"] == "Hold"
    await page.mouse.up()
    await take(events, "up")
    await page.clock.fast_forward(450)
    assert await markers(page) == []
    await page.touchscreen.tap(80, 70)
    touch = await take(events, "down")
    assert touch["pointer_type"] == "touch"
    await take(events, "up")
    await page.clock.fast_forward(100)
    await page.touchscreen.tap(80, 70)
    await take(events, "down")
    await take(events, "up")
    assert (await take(events, "double"))["pointer_type"] == "touch"
    await page.evaluate("__octowrightInteractions.configure({show:false,record:true})")
    await page.mouse.click(80, 70)
    assert (await take(events, "down"))["shown"] is False
    await take(events, "up")
    assert await markers(page) == []
    await page.evaluate("__octowrightInteractions.configure({show:true,record:false})")
    await page.mouse.down()
    assert len(await markers(page)) == 1
    await page.evaluate("__octowrightInteractions.destroy()")
    await page.mouse.up()
    await page.clock.fast_forward(1000)
    assert events.empty()
    assert await markers(page) == []


async def test_nested_cross_origin_frame_coordinates_and_relay_validation(observer_page: Any) -> None:
    page, events = observer_page
    await page.evaluate("""() => {
      const f=document.createElement('iframe'); f.id='outer'; f.src='http://outer.test/';
      f.style.cssText='position:absolute;left:100px;top:180px;width:400px;height:250px;border:6px solid black;transform:scale(.8);transform-origin:0 0'; document.body.append(f);
    }""")
    outer = page.frame_locator("#outer")
    await outer.locator("#target").wait_for()
    outer_frame = page.frames[1]
    await outer_frame.evaluate("""() => {
      const f=document.createElement('iframe'); f.id='inner'; f.src='http://inner.test/';
      f.style.cssText='position:absolute;left:35px;top:130px;width:230px;height:90px;border:3px solid black'; document.body.append(f);
    }""")
    inner = outer.frame_locator("#inner")
    await inner.locator("#target").wait_for()
    inner_frame = next(frame for frame in page.frames if "inner.test" in frame.url)
    await inner_frame.evaluate("__octowrightInteractions.ready")
    target = inner.locator("#target")
    box = await target.bounding_box()
    assert box
    await page.mouse.move(box["x"] + 30 * 0.8, box["y"] + 25 * 0.8)
    await page.mouse.down()
    try:
        event = await take(events, "down")
        assert event["x"] == pytest.approx(box["x"] + 30 * 0.8, abs=1)
        assert event["y"] == pytest.approx(box["y"] + 25 * 0.8, abs=1)
        assert (event["width"], event["height"]) == (800, 600)
        # Inspect the contact while held; a released marker legitimately fades.
        local = await markers(inner_frame)
        assert len(local) == 1
        assert float(local[0]["x"].removesuffix("px")) == pytest.approx(50, abs=1)
    finally:
        await page.mouse.up()
    await take(events, "up")
    # A page-generated mapping request/response has no pending native observation to release.
    await inner_frame.evaluate("""() => {
      parent.postMessage({type:'octowright:interaction:map',id:'fake',x:9,y:10,width:230,height:90,depth:0},'*');
      dispatchEvent(new MessageEvent('message',{data:{type:'octowright:interaction:mapped',id:'fake',x:5,y:6,width:800,height:600}}));
    }""")
    await page.evaluate("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
    assert events.empty()
    await inner_frame.goto("http://inner.test/next")
    await inner_frame.evaluate("__octowrightInteractions.ready")
    await inner.locator("#target").click(position={"x": 30, "y": 25})
    assert (await take(events, "down"))["width"] == 800


async def test_native_drag_leaving_frame_terminates_contact(observer_page: Any) -> None:
    page, events = observer_page
    await page.evaluate("""() => {
      const f=document.createElement('iframe'); f.id='exit-frame'; f.src='http://exit.test/';
      f.style.cssText='position:absolute;left:200px;top:200px;width:300px;height:200px'; document.body.append(f);
    }""")
    child = page.frame_locator("#exit-frame")
    await child.locator("#target").wait_for()
    frame = next(frame for frame in page.frames if "exit.test" in frame.url)
    await frame.evaluate("__octowrightInteractions.ready")
    await page.mouse.move(280, 270)
    await page.mouse.down()
    await take(events, "down")
    await page.mouse.move(80, 70, steps=3)
    await page.mouse.up()
    async with asyncio.timeout(5):
        while (await events.get())["phase"] not in {"cancel", "up"}:
            pass
    # Engines may retain native implicit mouse capture across a frame edge.
    # Whichever terminal event they deliver must remove the held contact.
    await frame.wait_for_function(
        "!window.__observerShadows.get(document.getElementById('__octowright_interactions__'))?.querySelector('[data-phase]')"
    )
    assert await markers(frame) == []


@pytest.mark.parametrize("observer_page", ["chromium"], indirect=True)
async def test_native_chromium_touch_hold(observer_page: Any) -> None:
    """Public Touchscreen has tap only; CDP supplies a genuine held contact."""
    page, events = observer_page
    await page.clock.install(time=1000)
    await page.clock.pause_at(2000)
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": 80, "y": 70, "id": 1}]})
    assert (await take(events, "down"))["pointer_type"] == "touch"
    await page.clock.fast_forward(499)
    assert (await markers(page))[0]["label"].startswith("Touch ")
    await page.clock.fast_forward(1)
    assert (await markers(page))[0]["label"] == "Hold"
    await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    await take(events, "up")
    await page.clock.fast_forward(450)
    assert await markers(page) == []
    await cdp.send(
        "Input.dispatchTouchEvent",
        {"type": "touchStart", "touchPoints": [{"x": 80, "y": 70, "id": 1}, {"x": 95, "y": 70, "id": 2}]},
    )
    first, second = await take(events, "down"), await take(events, "down")
    assert first["pointer_id"] != second["pointer_id"]
    assert len(await markers(page)) == 2
    await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    await take(events, "up")
    await take(events, "up")
    while not events.empty():
        assert events.get_nowait()["phase"] != "double"


async def test_feedback_preserves_application_text_locators(observer_page: Any) -> None:
    page, events = observer_page
    await page.locator("#target").evaluate("node=>node.textContent='Hold'")
    await page.clock.install(time=1000)
    await page.clock.pause_at(2000)
    await page.mouse.move(80, 70)
    await page.mouse.down()
    await take(events, "down")
    await page.clock.fast_forward(500)
    assert (await markers(page))[0]["label"] == "Hold"
    assert await page.get_by_text("Hold", exact=True).count() == 1
    assert await page.get_by_text("Hold", exact=True).get_attribute("id") == "target"
    await page.mouse.up()


async def test_native_input_focus_transition_preserves_click(observer_page: Any) -> None:
    page, events = observer_page
    await page.evaluate("""() => {
      for (const [id,top] of [['inputA',30],['inputB',90]]) {
        const node=document.createElement('input'); node.id=id;
        node.style.cssText=`position:absolute;left:220px;top:${top}px`; document.body.append(node);
      }
    }""")

    async def actions() -> list[Any]:
        await page.evaluate("appEvents=[]")
        await page.locator("#inputA").click()
        await page.locator("#inputB").click()
        return await page.evaluate("appEvents")

    await page.evaluate("__octowrightInteractions.configure({show:false,record:false})")
    baseline = await actions()
    await page.evaluate("__octowrightInteractions.configure({show:true,record:true})")
    assert await actions() == baseline
    assert await page.evaluate("document.activeElement.id") == "inputB"
    recorded = [await asyncio.wait_for(events.get(), 5) for _ in range(4)]
    assert [event["phase"] for event in recorded] == ["down", "up", "down", "up"]


@pytest.mark.parametrize("delay", [0, 200])
async def test_native_delayed_double_click_is_recorded_once(observer_page: Any, delay: int) -> None:
    page, events = observer_page

    async def actions() -> list[Any]:
        await page.evaluate("appEvents=[]")
        await page.mouse.dblclick(80, 70, delay=delay)
        return await page.evaluate("appEvents")

    await page.evaluate("__octowrightInteractions.configure({show:false,record:false})")
    baseline = await actions()
    await page.evaluate("__octowrightInteractions.configure({show:true,record:true})")
    # Snapshot after the observer handles native dblclick, before its pulse fades.
    # Keep the real input delay so this still exercises native slow-double handling.
    await page.evaluate("""() => addEventListener('dblclick', () => {
      const shadow=window.__observerShadows.get(document.getElementById('__octowright_interactions__'));
      window.__doubleMarkerPhases=[...shadow.querySelectorAll('[data-phase]')].map(node=>node.dataset.phase);
    }, {once:true})""")
    actual = await actions()
    assert actual == baseline
    assert sum(event[0] == "dblclick" for event in actual) == 1
    recorded = []
    while not events.empty():
        recorded.append(events.get_nowait())
    assert [event["phase"] for event in recorded] == ["down", "up", "down", "up", "double"]
    assert sum(phase == "double" for phase in await page.evaluate("__doubleMarkerPhases")) == 1
