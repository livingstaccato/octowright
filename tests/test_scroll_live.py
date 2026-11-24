# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Native wheel targeting and render synchronization against a local page."""

from __future__ import annotations

import asyncio
import os
import subprocess
import textwrap
import time
from pathlib import Path

import playwright
import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from octowright import export as py_export
from octowright import export_ts as ts_export
from octowright.artifacts.script_export_actions import EXPORT_DISPATCH
from octowright.recorder import Recorder
from octowright.session import BrowserSession

# Playwright's fixture and tests must share pytest-asyncio's loop. Mixing an
# AnyIO mark with asyncio_mode=auto makes fixture ownership plugin-order dependent.
pytestmark = pytest.mark.live_browser


@pytest_asyncio.fixture
async def scroll_session(tmp_path):
    async with asyncio.timeout(20), async_playwright() as pw:
        engine_name = os.environ.get("OCTOWRIGHT_TEST_SCROLL_ENGINE", "chromium")
        engine = getattr(pw, engine_name)
        browser = await engine.launch(
            executable_path=os.environ.get(f"OCTOWRIGHT_TEST_{engine_name.upper()}_EXECUTABLE")
        )
        log_path = tmp_path / "scroll.jsonl"
        recorder = Recorder(log_path)
        try:
            context = await browser.new_context(viewport={"width": 900, "height": 600})
            page = await context.new_page()
            await page.set_content("""
                <style>
                body { margin: 0; height: 4000px; }
                #panel { position: fixed; left: 500px; top: 50px;
                         width: 300px; height: 300px; overflow: auto; }
                #inside { height: 3000px; background: lightblue; }
                </style>
                <div id="panel"><div id="inside">Panel</div></div>
            """)
            await page.mouse.move(100, 100)
            yield BrowserSession(
                instance_id="scroll",
                kind=engine_name,
                label=None,
                url="about:blank",
                browser=browser,
                context=context,
                page=page,
                recorder=recorder,
                log_path=log_path,
            )
        finally:
            recorder.close()
            await browser.close()


@pytest.mark.asyncio
async def test_selector_wheel_reaches_visible_panel_instead_of_previous_pointer(scroll_session) -> None:
    session = scroll_session
    await session.scroll(delta_x=0, delta_y=300, selector="#panel")
    assert await session.page.locator("#panel").evaluate("panel => panel.scrollTop") == 300
    assert await session.page.evaluate("window.scrollY") == 0


@pytest.mark.asyncio
async def test_window_metrics_include_the_delivered_wheel(scroll_session) -> None:
    result = await scroll_session.scroll(delta_x=0, delta_y=300)
    assert result["y"] == 300
    assert result["settled"] is True


@pytest.mark.asyncio
async def test_slow_dispatch_keeps_observation_armed_until_wheel_arrives(scroll_session, monkeypatch) -> None:
    wheel = scroll_session.page.mouse.wheel

    async def delayed_wheel(dx, dy):
        await asyncio.sleep(1.1)
        await wheel(dx, dy)

    monkeypatch.setattr(scroll_session.page.mouse, "wheel", delayed_wheel)
    result = await scroll_session.scroll(delta_x=0, delta_y=300)
    assert result["settled"] is True
    assert result["y"] == 300


@pytest.mark.asyncio
async def test_wheel_before_delayed_ack_keeps_full_render_observation_budget(scroll_session, monkeypatch) -> None:
    page = scroll_session.page
    await page.evaluate("""() => {
        const requestFrame = window.requestAnimationFrame.bind(window);
        const callbacks = new Map();
        let nextFrame = 0;
        window.requestAnimationFrame = callback => {
            callbacks.set(++nextFrame, callback);
            return nextFrame;
        };
        window.cancelAnimationFrame = id => callbacks.delete(id);
        window.wheelObserved = new Promise(resolve =>
            window.addEventListener('wheel', () => resolve(true), {once: true}));
        window.releaseScrollFrames = () => {
            window.requestAnimationFrame = requestFrame;
            for (const callback of callbacks.values()) requestFrame(callback);
            callbacks.clear();
        };
    }""")
    wheel = page.mouse.wheel

    async def delayed_ack(dx, dy):
        await wheel(dx, dy)
        assert await page.evaluate("window.wheelObserved") is True
        # Native input has arrived, but its host acknowledgement is withheld.
        # A page-owned one-second timer would consume the observation here.
        await asyncio.sleep(1.1)
        await page.evaluate("window.releaseScrollFrames()")

    monkeypatch.setattr(page.mouse, "wheel", delayed_ack)
    result = await scroll_session.scroll(delta_x=0, delta_y=300)
    assert result["settled"] is True
    assert result["y"] == 300


@pytest.mark.parametrize("mode", ["zero", "boundary", "prevented", "missing_event", "no_frames", "stalled_callbacks"])
@pytest.mark.asyncio
async def test_scroll_wait_is_bounded_when_position_cannot_change(scroll_session, mode) -> None:
    page = scroll_session.page
    if mode == "prevented":
        await page.evaluate("window.addEventListener('wheel', e => e.preventDefault(), {passive: false})")
    elif mode == "missing_event":
        # A page listener registered before ours can suppress all observation.
        await page.evaluate(
            "window.addEventListener('wheel', e => {e.stopImmediatePropagation(); e.preventDefault()}, {capture: true, passive: false})"
        )
    elif mode == "no_frames":
        await page.evaluate("window.requestAnimationFrame = () => 0")
    elif mode == "stalled_callbacks":
        # This local fixture deliberately never invokes scheduled callbacks.
        await page.evaluate("window.setTimeout = window.requestAnimationFrame = () => 0")
    started = time.monotonic()
    async with asyncio.timeout(3):
        delta = 0 if mode == "zero" else (300 if mode in {"prevented", "missing_event"} else -300)
        result = await scroll_session.scroll(delta_x=0, delta_y=delta)
    assert result["y"] == 0
    assert time.monotonic() - started < 2
    assert result["settled"] is (mode not in {"missing_event", "no_frames", "stalled_callbacks"})


@pytest.mark.asyncio
async def test_cancelling_scroll_removes_render_wait_and_releases_gate(scroll_session) -> None:
    page = scroll_session.page
    await page.evaluate("""() => {
        window.wheelObserved = new Promise(resolve => window.addEventListener('wheel', resolve, {once: true}));
        window.requestAnimationFrame = () => 0;
        window.scrollListeners = new Set();
        window.cancelledFrames = [];
        const add = window.addEventListener.bind(window);
        const remove = window.removeEventListener.bind(window);
        window.addEventListener = (type, listener, options) => {
            if (type === 'wheel') window.scrollListeners.add(listener);
            return add(type, listener, options);
        };
        window.removeEventListener = (type, listener, options) => {
            window.scrollListeners.delete(listener);
            return remove(type, listener, options);
        };
        window.cancelAnimationFrame = id => window.cancelledFrames.push(id);
    }""")
    task = asyncio.create_task(scroll_session.scroll(delta_x=0, delta_y=300))
    await page.evaluate("window.wheelObserved.then(() => true)")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await page.evaluate("window.scrollListeners.size") == 0
    assert await page.evaluate("window.cancelledFrames.length") >= 1
    async with asyncio.timeout(1):
        assert (await scroll_session.scroll(delta_x=0, delta_y=0))["ok"]


@pytest.mark.parametrize("exporter", ["python", "cli"])
@pytest.mark.asyncio
async def test_exported_scroll_targets_panel(scroll_session, exporter) -> None:
    action = {"selector": "#panel", "delta_x": 0, "delta_y": 300}
    if exporter == "python":
        fragment = py_export._PY_HANDLERS["scroll"](action)
        namespace = {"page": scroll_session.page}
    else:
        fragment = EXPORT_DISPATCH["scroll"]
        namespace = {
            "state": {},
            "action": action,
            "_target": lambda state: scroll_session.page,
            "_page": lambda state: scroll_session.page,
        }
    body = "executed = 0\n" + textwrap.dedent(fragment)
    exec("async def replay():\n" + textwrap.indent(body, "    "), namespace)
    await namespace["replay"]()
    await scroll_session.page.wait_for_function("document.querySelector('#panel').scrollTop === 300", timeout=2000)
    assert await scroll_session.page.evaluate("window.scrollY") == 0


def test_typescript_export_scroll_targets_panel_in_real_browser() -> None:
    # The wheel fragment is valid JavaScript as well as TypeScript. Execute it
    # using Playwright's bundled Node API, without an npm install or TS shim.
    driver = Path(playwright.__file__).parent / "driver"
    fragment = ts_export._TS_HANDLERS["scroll"]({"selector": "#panel", "delta_x": 0, "delta_y": 300})
    program = """
        const playwright = require(process.argv[1]);
        (async () => {
            const engineName = process.env.OCTOWRIGHT_TEST_SCROLL_ENGINE || 'chromium';
            const executablePath = process.env[`OCTOWRIGHT_TEST_${engineName.toUpperCase()}_EXECUTABLE`];
            const browser = await playwright[engineName].launch({ executablePath });
            try {
                const page = await browser.newPage({ viewport: { width: 900, height: 600 } });
                await page.setContent('<body style="margin:0;height:4000px"><div id="panel" style="position:fixed;left:500px;top:50px;width:300px;height:300px;overflow:auto"><div style="height:3000px">Panel</div></div></body>');
                await page.mouse.move(100, 100);
                FRAGMENT
                await page.waitForFunction(() => document.querySelector('#panel').scrollTop === 300, null, { timeout: 2000 });
                if (await page.evaluate(() => window.scrollY) !== 0) throw new Error('Wrong scroll target');
            } finally { await browser.close(); }
        })().catch(error => { console.error(error); process.exitCode = 1; });
    """.replace("FRAGMENT", fragment)
    completed = subprocess.run(
        [str(driver / "node"), "-e", program, str(driver / "package")],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
