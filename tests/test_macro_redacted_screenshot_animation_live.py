# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redacted screenshot holds the page's animations still for the capture and lets them carry on after it."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from octowright.macros import safe_screenshot
from octowright.macros.safe_screenshot import redacted_screenshot
from tests.test_macro_redacted_screenshot_leaks_live import SECRET, STYLE, VIEWPORT, _browser, _PageSession

pytestmark = pytest.mark.live_browser


_ANIMATION_STATE = (
    "(() => { const a = document.getAnimations()[0];"
    " return a && {time: a.currentTime, opacity: getComputedStyle(document.getElementById('a')).opacity}; })()"
)


async def test_page_animations_hold_still_for_the_capture_and_resume_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The captured frame is the one the scans saw, and the page's animations carry on from it.

    The screenshot starts while the animation is still pending, the moment a freshly loaded
    page is screenshotted. Chrome resolves the start time of an animation that was pending
    when its timeline stopped against the stopped clock, which put it hours in the future:
    the frame jumped to the unanimated style during the capture, and the animation did not
    play again afterwards. Each attempt is a fresh page, because that race is not won every time.
    """
    reads: list[list[dict[str, float]]] = []
    capture = safe_screenshot._capture
    read = {"expression": _ANIMATION_STATE, "returnByValue": True}

    async def capture_while_watching_the_clock(cdp: object, image: str) -> bytes:
        reads.append([])
        for _ in range(2):
            reply = await cdp.send("Runtime.evaluate", read)  # type: ignore[attr-defined]
            value = reply["result"]["value"]
            reads[-1].append({"time": float(value["time"]), "opacity": float(value["opacity"])})
            await asyncio.sleep(0.2)
        return await capture(cdp, image)

    monkeypatch.setattr(safe_screenshot, "_capture", capture_while_watching_the_clock)
    html = (
        f"{STYLE}<style>@keyframes k{{from{{opacity:.2}}to{{opacity:.8}}}}</style>"
        f"<div id=a style='animation:k 10s linear infinite'>.</div><p>{SECRET}</p>"
    )
    async with _browser() as browser:
        for attempt in range(4):
            page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
            await page.set_content(html)
            assert await redacted_screenshot(
                _PageSession(page), {"path": str(tmp_path / f"a{attempt}.png")}, (SECRET,), root=tmp_path
            ) == (1, 0)
            first, second = reads[-1]
            # Still, and still showing the animation: its first keyframe, not the unanimated opacity of 1.
            assert first == second
            assert first["opacity"] < 0.3
            after = await page.evaluate(_ANIMATION_STATE)
            await page.wait_for_timeout(200)
            later = await page.evaluate(_ANIMATION_STATE)
            # It resumes from where it stood, rather than from a start time hours away.
            assert first["time"] <= after["time"] < later["time"] < first["time"] + 2000
            assert float(after["opacity"]) < float(later["opacity"])
            await page.close()
