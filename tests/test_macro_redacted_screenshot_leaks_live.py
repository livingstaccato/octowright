# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redacted screenshot either refuses or shows a page the value was never on.

Each case renders the same markup twice: once as the redacted screenshot sees it, once
with the classified value replaced before load. A redacted screenshot that is taken
must be byte-identical to the second render; anything else may have leaked the value
into the PNG. Refusing (no file written) is always an acceptable outcome.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from octowright.macros.safe_screenshot import redacted_screenshot

pytestmark = pytest.mark.live_browser

SECRET = "Probe-Secret-Canary-7f3a"  # pragma: allowlist secret
AMP = "p&ss<w>rd-canary-9"  # pragma: allowlist secret
STYLE = "<style>body{font:40px monospace;margin:0} *{caret-color:transparent}</style>"
VIEWPORT = {"width": 900, "height": 200}

_NO_ENGINE = (
    "executable doesn't exist",
    "missing x server",
    "no protocol specified",
    "playwright install",
)


class _PageSession:
    def __init__(self, page: object) -> None:
        self.page = page

    @contextlib.asynccontextmanager
    async def operation(self, _name: str) -> AsyncIterator[None]:
        yield

    async def screenshot(self, path: Path) -> Path:
        await self.page.screenshot(path=str(path))  # type: ignore[attr-defined]
        return path


@contextlib.asynccontextmanager
async def _browser() -> AsyncIterator[object]:
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                yield browser
            finally:
                await browser.close()
    except Exception as exc:
        if any(snippet in str(exc).lower() for snippet in _NO_ENGINE):
            pytest.skip(f"live browser engine unavailable: {exc}")
        raise


async def _render(browser: object, html: str, target: Path) -> bytes:
    page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
    try:
        await page.set_content(html)
        await page.wait_for_timeout(300)
        await page.screenshot(path=str(target))
    finally:
        await page.close()
    return target.read_bytes()


async def _redacted(browser: object, html: str, secret: str, tmp_path: Path, name: str) -> bytes | None:
    """The redacted PNG's bytes, or None when the screenshot was refused."""
    page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
    target = tmp_path / f"{name}.redacted.png"
    try:
        await page.set_content(html)
        await page.wait_for_timeout(300)
        try:
            await redacted_screenshot(_PageSession(page), {"path": str(target)}, (secret,), root=tmp_path)
        except RuntimeError:
            assert not target.exists(), "a refused redacted screenshot must leave no file"
            return None
        return target.read_bytes()
    finally:
        await page.close()


_CASES = {
    "split_across_nodes": (
        f"{STYLE}<p><span>Probe-Secret-</span><span>Canary-7f3a</span></p>",
        f"{STYLE}<p><span>&lt;redacted&gt;</span><span></span></p>",
        SECRET,
    ),
    "shown_in_another_case": (f"{STYLE}<p>{SECRET.lower()}</p>", f"{STYLE}<p>&lt;redacted&gt;</p>", SECRET),
    "closed_shadow_root": (
        f"{STYLE}<div id=h></div><script>document.getElementById('h')"
        f".attachShadow({{mode:'closed'}}).innerHTML='<p>{SECRET}</p>'</script>",
        f"{STYLE}<div id=h></div><script>document.getElementById('h')"
        f".attachShadow({{mode:'closed'}}).innerHTML='<p>&lt;redacted&gt;</p>'</script>",
        SECRET,
    ),
    "declarative_closed_shadow_root": (
        f"{STYLE}<div><template shadowrootmode=closed><p>{SECRET}</p></template></div>",
        f"{STYLE}<div><template shadowrootmode=closed><p>&lt;redacted&gt;</p></template></div>",
        SECRET,
    ),
    "generated_content": (
        f"{STYLE}<p id=x>.</p><script>const s=new CSSStyleSheet();"
        f"s.replaceSync('#x::before{{content:\"{SECRET}\"}}');document.adoptedStyleSheets=[s]</script>",
        f"{STYLE}<p id=x>.</p><script>const s=new CSSStyleSheet();"
        "s.replaceSync('#x::before{content:\"<redacted>\"}');document.adoptedStyleSheets=[s]</script>",
        SECRET,
    ),
    "timer_rerenders": (
        f"{STYLE}<p id=t>{SECRET}</p><script>setInterval(()=>{{document.getElementById('t')"
        f".textContent='{SECRET}'}},0)</script>",
        f"{STYLE}<p id=t>&lt;redacted&gt;</p>",
        SECRET,
    ),
    "animation_frame_rerenders": (
        f"{STYLE}<p id=t>{SECRET}</p><script>const f=()=>{{document.getElementById('t')"
        f".textContent='{SECRET}';requestAnimationFrame(f)}};f()</script>",
        f"{STYLE}<p id=t>&lt;redacted&gt;</p>",
        SECRET,
    ),
    "mutation_observer_restores": (
        f"{STYLE}<p id=t>{SECRET}</p><script>new MutationObserver(()=>{{const t=document.getElementById('t');"
        f"if(t.textContent!=='{SECRET}')t.textContent='{SECRET}'}})"
        ".observe(document.body,{subtree:true,characterData:true,childList:true})</script>",
        f"{STYLE}<p id=t>&lt;redacted&gt;</p>",
        SECRET,
    ),
    "input_value_set_by_script": (
        f"{STYLE}<input id=i style='font:40px monospace;width:880px'><script>document.getElementById('i').value={SECRET!r}</script>",
        f"{STYLE}<input id=i style='font:40px monospace;width:880px'><script>document.getElementById('i').value='<redacted>'</script>",
        SECRET,
    ),
    "html_special_characters": (
        f"{STYLE}<p id=t></p><script>document.getElementById('t').textContent={AMP!r}</script>",
        f"{STYLE}<p id=t></p><script>document.getElementById('t').textContent='<redacted>'</script>",
        AMP,
    ),
    "open_shadow_root": (
        f"{STYLE}<div id=h></div><script>document.getElementById('h')"
        f".attachShadow({{mode:'open'}}).innerHTML='<p>{SECRET}</p>'</script>",
        f"{STYLE}<div id=h></div><script>document.getElementById('h')"
        f".attachShadow({{mode:'open'}}).innerHTML='<p>&lt;redacted&gt;</p>'</script>",
        SECRET,
    ),
}

_CANVAS = (
    "<canvas id=c width=800 height=80></canvas><script>const x=document.getElementById('c').getContext('2d');"
    f"x.font='40px monospace';x.fillText('{SECRET}',0,50)</script>"
)
_HIDDEN_CANVAS = "<style>canvas{visibility:hidden}</style>" + _CANVAS
_IFRAME = f"<iframe srcdoc='<p style=font-size:40px>{SECRET}</p>' width=800 height=120></iframe>"
_HIDDEN_IFRAME = "<style>iframe{visibility:hidden}</style>" + _IFRAME

_OPAQUE_CASES = {
    "canvas": (_CANVAS, _HIDDEN_CANVAS),
    "canvas_with_page_transitions": ("<style>*{transition:all 5s}</style>" + _CANVAS, _HIDDEN_CANVAS),
    "iframe": (_IFRAME, _HIDDEN_IFRAME),
    "iframe_with_page_transitions": ("<style>iframe{transition:visibility 5s}</style>" + _IFRAME, _HIDDEN_IFRAME),
}


@pytest.mark.parametrize("name", sorted(_CASES))
async def test_a_taken_screenshot_matches_a_page_that_never_held_the_value(name: str, tmp_path: Path) -> None:
    html, never_held, secret = _CASES[name]
    async with _browser() as browser:
        reference = await _render(browser, never_held, tmp_path / f"{name}.reference.png")
        raw = await _render(browser, html, tmp_path / f"{name}.raw.png")
        assert raw != reference, "the case must render the value visibly, or it proves nothing"
        redacted = await _redacted(browser, html, secret, tmp_path, name)
    assert redacted is None or redacted == reference


async def test_an_open_shadow_root_is_redacted_rather_than_refused(tmp_path: Path) -> None:
    html, never_held, secret = _CASES["open_shadow_root"]
    async with _browser() as browser:
        reference = await _render(browser, never_held, tmp_path / "reference.png")
        redacted = await _redacted(browser, html, secret, tmp_path, "open_shadow")
    assert redacted == reference


@pytest.mark.parametrize("name", sorted(_OPAQUE_CASES))
async def test_unreadable_pixels_are_hidden_for_the_screenshot(name: str, tmp_path: Path) -> None:
    html, hidden = _OPAQUE_CASES[name]
    async with _browser() as browser:
        reference = await _render(browser, hidden, tmp_path / f"{name}.reference.png")
        redacted = await _redacted(browser, html, SECRET, tmp_path, name)
    assert redacted is None or redacted == reference


async def test_a_frame_address_holding_the_value_is_never_navigated(tmp_path: Path) -> None:
    async with _browser() as browser:
        page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
        requests: list[str] = []
        page.on("request", lambda request: requests.append(request.url))
        await page.route(
            "https://app.test/**", lambda route: route.fulfill(body="<p>frame</p>", content_type="text/html")
        )
        await page.goto("https://app.test/")
        await page.set_content(f"<iframe src='https://app.test/embed?user={SECRET}'></iframe>")
        await page.wait_for_timeout(300)
        requests.clear()
        target = tmp_path / "frame.png"
        with contextlib.suppress(RuntimeError):
            await redacted_screenshot(_PageSession(page), {"path": str(target)}, (SECRET,), root=tmp_path)
        await page.wait_for_timeout(300)
        assert requests == []
        assert await page.get_attribute("iframe", "src") == f"https://app.test/embed?user={SECRET}"


async def test_page_script_cannot_disable_the_restore(tmp_path: Path) -> None:
    async with _browser() as browser:
        page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
        await page.set_content(
            f"<p id=t>{SECRET}</p><script>Object.defineProperty(globalThis,'__octowrightRedactedScreenshotState',"
            "{get(){return undefined},set(v){}})</script>"
        )
        target = tmp_path / "tamper.png"
        with contextlib.suppress(RuntimeError):
            await redacted_screenshot(_PageSession(page), {"path": str(target)}, (SECRET,), root=tmp_path)
        assert await page.inner_text("#t") == SECRET
