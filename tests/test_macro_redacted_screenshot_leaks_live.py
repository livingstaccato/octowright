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
import json
import urllib.parse
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

    # No ``screenshot`` method: the redacted path captures through DevTools itself, and a
    # call to Playwright's screenshot helper (which writes styles onto the page) fails here.
    @contextlib.asynccontextmanager
    async def operation(self, _name: str) -> AsyncIterator[None]:
        yield


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

_R = "&lt;redacted&gt;"


def _closed(inner: str) -> str:
    return (
        "<div id=h></div><script>document.getElementById('h')"
        f".attachShadow({{mode:'closed'}}).innerHTML={json.dumps(inner)}</script>"
    )


def _adopted(css: str) -> str:
    return (
        f"<script>const s=new CSSStyleSheet();s.replaceSync({json.dumps(css)});document.adoptedStyleSheets=[s]</script>"
    )


def _svg(text: str) -> str:
    svg = f"<svg xmlns='http://www.w3.org/2000/svg' width='880' height='60'><text x='0' y='45' font-size='40'>{text}</text></svg>"
    return "data:image/svg+xml," + urllib.parse.quote(svg, safe="")


_FIELD = "style='font:40px monospace;width:880px'"
_BROKEN = "data:image/png;base64,AAAA"

# Spellings, arrangements and drawn attributes a text-node scan misses (review of a64ea07b).
_CASES.update(
    {
        "zero_width_space_inside": (f"{STYLE}<p>Probe-Secret-&#x200B;Canary-7f3a</p>", f"{STYLE}<p>{_R}</p>", SECRET),
        "soft_hyphen_inside": (f"{STYLE}<p>Probe-Secret-&shy;Canary-7f3a</p>", f"{STYLE}<p>{_R}</p>", SECRET),
        "word_joiner_inside": (f"{STYLE}<p>Probe-Secret-&#x2060;Canary-7f3a</p>", f"{STYLE}<p>{_R}</p>", SECRET),
        "decomposed_accent": (f"{STYLE}<p>Jose&#x301;-Canary-Name</p>", f"{STYLE}<p>{_R}</p>", "Jos\u00e9-Canary-Name"),
        "bidi_override": (f"{STYLE}<p><bdo dir=rtl>{SECRET[::-1]}</bdo></p>", f"{STYLE}<p>{_R}</p>", SECRET),
        "flex_order": (
            f"{STYLE}<p style='display:flex;margin:0'><span style=order:2>Canary-7f3a</span>"
            "<span style=order:1>Probe-Secret-</span></p>",
            f"{STYLE}<p style='margin:0'>{_R}</p>",
            SECRET,
        ),
        "offscreen_text_between": (
            f"{STYLE}<p><span>Probe-Secret-</span><span style='position:absolute;left:-9999px'>at</span>"
            "<span>Canary-7f3a</span></p>",
            f"{STYLE}<p>{_R}</p>",
            SECRET,
        ),
        "clipped_text_between": (
            f"{STYLE}<p><span>Probe-Secret-</span><span style='position:absolute;width:1px;height:1px;"
            "overflow:hidden;clip:rect(0,0,0,0)'>screen reader</span><span>Canary-7f3a</span></p>",
            f"{STYLE}<p>{_R}</p>",
            SECRET,
        ),
        "picture_source": (
            f'{STYLE}<picture><source srcset="{_svg(SECRET)}"><img src="{_svg("x")}"></picture>',
            f'{STYLE}<picture><source srcset="{_svg("x")}"><img src="{_svg("x")}"></picture>',
            SECRET,
        ),
        "adopted_background_image": (
            f"{STYLE}<div id=b style='width:880px;height:60px'></div>"
            + _adopted(f'#b{{background-image:url("{_svg(SECRET)}")}}'),
            f"{STYLE}<div id=b style='width:880px;height:60px'></div>",
            SECRET,
        ),
        "placeholder_in_closed_shadow": (
            STYLE + _closed(f"<input placeholder='{SECRET}' {_FIELD}>"),
            STYLE + _closed(f"<input placeholder='<redacted>' {_FIELD}>"),
            SECRET,
        ),
        "textarea_placeholder_in_closed_shadow": (
            STYLE + _closed(f"<textarea placeholder='{SECRET}' {_FIELD}></textarea>"),
            STYLE + _closed(f"<textarea placeholder='<redacted>' {_FIELD}></textarea>"),
            SECRET,
        ),
        "broken_image_alt_in_closed_shadow": (
            STYLE + _closed(f"<img alt='{SECRET}' src='{_BROKEN}' style='width:880px;height:80px'>"),
            STYLE + _closed(f"<img alt='<redacted>' src='{_BROKEN}' style='width:880px;height:80px'>"),
            SECRET,
        ),
        "option_label_in_closed_shadow": (
            STYLE + _closed(f"<select style='font:40px monospace'><option label='{SECRET}' value=1></option></select>"),
            STYLE
            + _closed("<select style='font:40px monospace'><option label='<redacted>' value=1></option></select>"),
            SECRET,
        ),
    }
)

# Pages that change what they show on their own; every attempt must refuse or show nothing.
_REPEATED = {
    "input_value_toggled_by_a_timer": (
        f"{STYLE}<input id=i {_FIELD}><script>const e=document.getElementById('i');"
        f"setInterval(()=>{{e.value=e.value?'':{json.dumps(SECRET)}}},7)</script>",
        f"{STYLE}<input id=i {_FIELD}>",
    ),
    "generated_content_animated_by_keyframes": (
        f"{STYLE}<p id=a style='margin:0'></p>"
        + _adopted(
            "@keyframes k{0%{content:'x'}50%{content:'" + SECRET + "'}} "
            "#a::before{content:'x';animation:k 120ms steps(1) infinite}"
        ),
        f"{STYLE}<p id=a style='margin:0'>x</p>",
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


@pytest.mark.parametrize("name", sorted(_REPEATED))
async def test_a_page_that_changes_itself_never_keeps_a_screenshot_showing_the_value(name: str, tmp_path: Path) -> None:
    html, never_held = _REPEATED[name]
    async with _browser() as browser:
        reference = await _render(browser, never_held, tmp_path / f"{name}.reference.png")
        outcomes = [await _redacted(browser, html, SECRET, tmp_path, f"{name}-{attempt}") for attempt in range(4)]
    assert all(outcome is None or outcome == reference for outcome in outcomes)
