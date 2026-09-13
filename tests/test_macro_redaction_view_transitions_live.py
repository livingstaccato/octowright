# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""View transitions on a real page: a running one is reported, and ending them reaches every root.

A view transition draws a raster of its scope's old state, which no redaction reaches. Chromium runs
one on the document or on any element, including an element in an open or closed shadow root.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from octowright.macros.page_devtools import PageController, end_view_transitions
from octowright.macros.safe_screenshot import redacted_screenshot
from tests.test_macro_redaction_controller_live import _SETTLED, SECRET, _page, _watched

pytestmark = pytest.mark.live_browser

_LONG = (
    "<style>::view-transition-group(*),::view-transition-old(*),::view-transition-new(*),"
    "*::view-transition-group(*),*::view-transition-old(*),*::view-transition-new(*){animation-duration:60s}</style>"
)
_SCOPED = "<div id=s><p id=t>old</p></div>"


def _shadow(mode: str) -> str:
    return (
        f"<div id=h></div><script>window.__root = document.getElementById('h').attachShadow({{mode: '{mode}'}});"
        f" window.__root.innerHTML = {json.dumps(_LONG + _SCOPED)};</script>"
    )


# Each scope: its markup, which keeps the root holding #t on window.__root, and the transition's scope.
_SCOPES = {
    "document": (f"{_LONG}<p id=t>old</p><script>window.__root = document;</script>", "document"),
    "element": (f"{_LONG}{_SCOPED}<script>window.__root = document;</script>", "window.__root.getElementById('s')"),
    "element in an open shadow root": (_shadow("open"), "window.__root.getElementById('s')"),
    "element in a closed shadow root": (_shadow("closed"), "window.__root.getElementById('s')"),
}
_START = (
    "() => {{ window.__vt = {scope}.startViewTransition("
    "() => {{ window.__root.getElementById('t').textContent = 'new'; }}); }}"
)
_STATE = (
    "() => [Boolean(document.activeViewTransition), Boolean(({scope}).activeViewTransition),"
    " window.__root.getElementById('t').textContent]"
)


@pytest.mark.parametrize("scope", sorted(_SCOPES))
async def test_a_running_view_transition_is_reported_until_it_ends(scope: str) -> None:
    html, target = _SCOPES[scope]
    async with _page(html) as page:
        await page.evaluate(_START.format(scope=target))
        await page.wait_for_timeout(100)
        async with _watched(page) as watched:
            assert (await watched.controller.verify())["transitioning"] == 1
            await page.evaluate("async () => { window.__vt.skipTransition(); await window.__vt.finished; }")
            assert (await watched.controller.verify())["transitioning"] == 0


@pytest.mark.parametrize("scope", sorted(_SCOPES))
async def test_ending_view_transitions_skips_one_in_any_root_while_animations_are_paused(scope: str) -> None:
    html, target = _SCOPES[scope]
    async with _page(html) as page:
        await page.evaluate(_START.format(scope=target))
        await page.wait_for_timeout(100)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Animation.enable")
        await cdp.send("Animation.setPlaybackRate", {"playbackRate": 0})
        assert await end_view_transitions(cdp) is True
        assert await page.evaluate(_STATE.format(scope=target)) == [False, False, "new"]
        await cdp.detach()


def _view_transition_pseudo_elements(node: dict[str, Any]) -> list[str]:
    """The view-transition pseudo-elements in a pierced ``DOM.getDocument`` tree."""
    found: list[str] = []
    pending = [node]
    while pending:
        current = pending.pop()
        for pseudo in current.get("pseudoElements", []):
            if str(pseudo.get("pseudoType", "")).startswith("view-transition"):
                found.append(str(pseudo["pseudoType"]))
            pending.append(pseudo)
        pending.extend(current.get("children", []))
        pending.extend(current.get("shadowRoots", []))
    return found


async def test_ending_view_transitions_returns_once_their_pseudo_elements_are_gone() -> None:
    # Their removal is a page change DevTools reports; it must land before counting begins, not during the capture.
    html, target = _SCOPES["element"]
    async with _page(html) as page:
        await page.evaluate(_START.format(scope=target))
        await page.wait_for_timeout(100)
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Animation.enable")
        await cdp.send("Animation.setPlaybackRate", {"playbackRate": 0})
        assert await end_view_transitions(cdp) is True
        removed: list[Any] = []
        cdp.on("DOM.pseudoElementRemoved", lambda params: removed.append(params))
        await page.evaluate(_SETTLED)
        tree = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        assert (removed, _view_transition_pseudo_elements(tree["root"])) == ([], [])
        await cdp.detach()


async def test_ending_view_transitions_waits_for_the_page_update_to_finish() -> None:
    # The update settles well after the skip; the redaction must not start before it has (mutant r01).
    html, target = _SCOPES["element"]
    async with _page(html) as page:
        await page.evaluate(
            f"() => {{ window.__vt = {target}.startViewTransition(() => new Promise((resolve) => setTimeout(() => {{"
            " document.getElementById('t').textContent = 'late'; resolve(); }, 500))); }"
        )
        cdp = await page.context.new_cdp_session(page)
        assert await end_view_transitions(cdp) is True
        assert await page.evaluate("() => document.getElementById('t').textContent") == "late"
        await cdp.detach()


async def test_ending_view_transitions_ends_every_running_one_and_waits_for_each() -> None:
    # Only the first skipped or only the first awaited would leave the second running or its update unfinished.
    html = f"{_LONG}<div id=s><p id=t>old</p></div><div id=u><p id=v>old</p></div>"
    async with _page(html) as page:
        await page.evaluate(
            "() => { document.getElementById('s').startViewTransition(() => { document.getElementById('t').textContent = 'new'; });"
            " document.getElementById('u').startViewTransition(() => new Promise((resolve) => setTimeout(() => {"
            " document.getElementById('v').textContent = 'late'; resolve(); }, 500))); }"
        )
        await page.wait_for_timeout(100)
        cdp = await page.context.new_cdp_session(page)
        assert await asyncio.wait_for(end_view_transitions(cdp), 10) is True
        state = "() => [[...document.querySelectorAll('*')].filter((e) => e.activeViewTransition).length, document.getElementById('v').textContent]"
        assert await page.evaluate(state) == [0, "late"]
        await cdp.detach()


async def test_ending_view_transitions_settles_when_the_page_replaced_request_animation_frame() -> None:
    html, target = _SCOPES["element"]
    async with _page(html) as page:
        await page.evaluate(_START.format(scope=target))
        await page.wait_for_timeout(100)
        await page.evaluate("() => { window.requestAnimationFrame = () => 0; }")
        cdp = await page.context.new_cdp_session(page)
        assert await asyncio.wait_for(end_view_transitions(cdp), 10) is True
        tree = await cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        assert _view_transition_pseudo_elements(tree["root"]) == []
        await cdp.detach()


class _Session:
    def __init__(self, page: Any) -> None:
        self.page = page

    @contextlib.asynccontextmanager
    async def operation(self, _name: str) -> AsyncIterator[None]:
        yield


_LATE_ROOT = (
    "(mode) => {{ const r = document.getElementById('h2').attachShadow({{mode}});"
    " r.innerHTML = {inner};"
    " r.getElementById('s2').startViewTransition(() => {{ r.getElementById('t2').textContent = 'benign'; }}); }}"
)


@pytest.mark.parametrize("mode", ["open", "closed"])
@pytest.mark.parametrize("stage", ["redact", "watch"])
async def test_a_transition_in_a_root_attached_after_roots_are_collected_refuses_the_screenshot(
    stage: str, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The root is attached between collecting the roots and counting, so only a read of what Chrome draws finds it.
    script = _LATE_ROOT.format(inner=json.dumps(_LONG + f"<div id=s2><p id=t2>{SECRET}</p></div>"))
    target = tmp_path / "late.png"
    async with _page("<div id=h2></div><p>visible</p>") as page:
        original = getattr(PageController, stage)

        async def attach_first(self: PageController, *args: Any) -> Any:
            await page.evaluate(script, mode)
            await page.wait_for_timeout(100)
            return await original(self, *args)

        monkeypatch.setattr(PageController, stage, attach_first)
        with pytest.raises(RuntimeError):
            await redacted_screenshot(_Session(page), {"path": str(target)}, (SECRET,), root=tmp_path)
        assert not target.exists()


async def test_ending_view_transitions_on_a_page_without_one_reports_none() -> None:
    async with _page(_SCOPES["element"][0]) as page:
        cdp = await page.context.new_cdp_session(page)
        assert await end_view_transitions(cdp) is False
        await cdp.detach()
