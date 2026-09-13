# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The in-page controller on a real page: what it counts as a change, what it matches, what it restores."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from octowright.macros import redaction_page_js as page_js
from octowright.macros.privacy import sensitive_value_variants
from octowright.macros.redaction_text import IGNORABLE_RANGES, JS_IGNORABLE_CLASS, normalize

pytestmark = pytest.mark.live_browser

SECRET = "Controller-Canary-3b8d"  # pragma: allowlist secret

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")

_PAGE = (
    "<style>p{}</style><p id=t>hello</p><input id=i value=v>"
    "<select id=s><option>a</option><option>b</option></select><div id=h></div>"
)


@contextlib.asynccontextmanager
async def _page(html: str) -> AsyncIterator[Any]:
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(html)
                yield page
            finally:
                await browser.close()
    except Exception as exc:
        if any(snippet in str(exc).lower() for snippet in _NO_ENGINE):
            pytest.skip(f"live browser engine unavailable: {exc}")
        raise


async def _redacted(page: Any, values: tuple[str, ...] = (SECRET,)) -> Any:
    argument = page_js.controller_argument(list(sensitive_value_variants(values)))
    controller = await page.evaluate_handle(page_js.CONTROLLER_JS, argument)
    await controller.evaluate(page_js.REDACT_CALL)
    return controller


_CHANGES = {
    "child added": "() => document.body.append(document.createElement('i'))",
    "text edited": "() => { document.getElementById('t').firstChild.nodeValue = 'other'; }",
    "attribute set": "() => document.getElementById('t').setAttribute('data-x', '1')",
    "inline style": "() => { document.getElementById('t').style.width = '10px'; }",
    "input value toggled back": "() => { const i = document.getElementById('i'); i.value = 'x'; i.value = 'v'; }",
    "selection toggled back": "() => { const s = document.getElementById('s'); s.selectedIndex = 1; s.selectedIndex = 0; }",
    "stylesheet rule": "() => { document.styleSheets[0].insertRule('p{color:red}'); document.styleSheets[0].deleteRule(0); }",
    "adopted stylesheets": "() => { document.adoptedStyleSheets = [new CSSStyleSheet()]; document.adoptedStyleSheets = []; }",
    "open shadow root": "() => document.getElementById('h').attachShadow({mode: 'open'})",
}


async def test_an_untouched_page_reports_no_change() -> None:
    async with _page(_PAGE) as page:
        controller = await _redacted(page)
        await page.evaluate("() => [document.getElementById('i').value, document.styleSheets.length]")
        assert await controller.evaluate(page_js.VERIFY_CALL) == {"changed": 0, "remaining": 0}


@pytest.mark.parametrize("name", sorted(_CHANGES))
async def test_every_kind_of_page_change_is_counted(name: str) -> None:
    async with _page(_PAGE) as page:
        controller = await _redacted(page)
        await page.evaluate(_CHANGES[name])
        report = await controller.evaluate(page_js.VERIFY_CALL)
        assert report["changed"] >= 1


async def test_restore_removes_every_hook_it_installed() -> None:
    async with _page(_PAGE) as page:
        await page.evaluate(
            "() => { window.__native = [Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set,"
            " CSSStyleSheet.prototype.insertRule, Object.getOwnPropertyDescriptor(Document.prototype,"
            " 'adoptedStyleSheets').set]; }"
        )
        controller = await _redacted(page)
        hooked = "() => { const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;"
        assert await page.evaluate(hooked + " return d !== window.__native[0]; }") is True
        await controller.evaluate(page_js.RESTORE_CALL)
        assert await page.evaluate(
            "() => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set === window.__native[0]"
            " && CSSStyleSheet.prototype.insertRule === window.__native[1]"
            " && Object.getOwnPropertyDescriptor(Document.prototype, 'adoptedStyleSheets').set === window.__native[2]"
        )


@pytest.mark.parametrize(
    ("shown", "expected"),
    [
        (SECRET.lower(), "x <redacted> y"),
        (SECRET[:11] + "\u200b" + SECRET[11:], "x <redacted> y"),
        (SECRET[:11] + "\u00ad" + SECRET[11:], "x <redacted> y"),
        (SECRET[:11] + " " + SECRET[11:], "x <redacted> y"),
    ],
    ids=["other-case", "zero-width-space", "soft-hyphen", "space"],
)
async def test_the_page_redacts_every_spelling_of_the_value(shown: str, expected: str) -> None:
    async with _page("<p id=t></p>") as page:
        await page.evaluate("(text) => { document.getElementById('t').textContent = text; }", f"x {shown} y")
        controller = await _redacted(page)
        assert await page.evaluate("() => document.getElementById('t').textContent") == expected
        assert (await controller.evaluate(page_js.VERIFY_CALL))["remaining"] == 0


@pytest.mark.parametrize(
    ("setup", "read"),
    [
        (
            "(text) => { document.getElementById('f').setAttribute('placeholder', text); }",
            "() => document.getElementById('f').getAttribute('placeholder')",
        ),
        (
            "(text) => { document.getElementById('f').value = text; }",
            "() => document.getElementById('f').value",
        ),
    ],
    ids=["attribute", "field-value"],
)
async def test_an_attribute_or_field_spelled_with_invisible_characters_is_redacted(setup: str, read: str) -> None:
    async with _page("<input id=f>") as page:
        await page.evaluate(setup, SECRET[:11] + "\u200b" + SECRET[11:])
        controller = await _redacted(page)
        assert await page.evaluate(read) == "<redacted>"
        assert (await controller.evaluate(page_js.VERIFY_CALL))["remaining"] == 0


async def test_a_spelling_the_pattern_cannot_replace_is_redacted_whole() -> None:
    async with _page("<p id=t>Jose\u0301-Controller-Canary</p>") as page:
        controller = await _redacted(page, ("Jos\u00e9-Controller-Canary",))
        assert await page.evaluate("() => document.getElementById('t').textContent") == "<redacted>"
        assert (await controller.evaluate(page_js.VERIFY_CALL))["remaining"] == 0
        await controller.evaluate(page_js.RESTORE_CALL)
        assert await page.evaluate("() => document.getElementById('t').textContent") == "Jose\u0301-Controller-Canary"


async def test_a_focused_input_keeps_its_value_and_selection() -> None:
    async with _page(f"<input id=i value='x {SECRET} y'>") as page:
        await page.evaluate(
            "() => { const i = document.getElementById('i'); i.focus(); i.setSelectionRange(2, 5, 'backward'); }"
        )
        controller = await _redacted(page)
        assert await page.evaluate("() => document.getElementById('i').value") == "x <redacted> y"
        await controller.evaluate(page_js.RESTORE_CALL)
        state = await page.evaluate(
            "() => { const i = document.getElementById('i');"
            " return [i.value, i.selectionStart, i.selectionEnd, i.selectionDirection, document.activeElement === i]; }"
        )
        assert state == [f"x {SECRET} y", 2, 5, "backward", True]


async def test_a_frame_document_holding_the_value_is_hidden_and_never_reloaded() -> None:
    async with _page(f"<iframe id=f srcdoc='<p>{SECRET}</p>'></iframe>") as page:
        await page.wait_for_function("() => document.getElementById('f').contentDocument.body")
        await page.evaluate(
            "() => { const f = document.getElementById('f'); f.contentWindow.__marker = 42; window.__loads = 0;"
            " f.addEventListener('load', () => { window.__loads += 1; }); }"
        )
        controller = await _redacted(page)
        assert await page.evaluate("() => getComputedStyle(document.getElementById('f')).visibility") == "hidden"
        assert (await controller.evaluate(page_js.VERIFY_CALL))["remaining"] == 0
        await controller.evaluate(page_js.RESTORE_CALL)
        await page.wait_for_timeout(300)
        state = await page.evaluate(
            "() => { const f = document.getElementById('f');"
            " return [window.__loads, f.contentWindow.__marker, f.getAttribute('srcdoc'), f.getAttribute('style')]; }"
        )
        assert state == [0, 42, f"<p>{SECRET}</p>", None]


async def test_restore_keeps_a_style_change_the_page_made_meanwhile() -> None:
    async with _page("<canvas id=c style='width:100px'></canvas><canvas id=u></canvas>") as page:
        controller = await _redacted(page)
        await page.evaluate("() => { document.getElementById('c').style.width = '300px'; }")
        await controller.evaluate(page_js.RESTORE_CALL)
        state = await page.evaluate(
            "() => { const c = document.getElementById('c').style;"
            " return [c.width, c.visibility, c.transition, c.animation, document.getElementById('u').getAttribute('style')]; }"
        )
        assert state == ["300px", "", "", "", None]


async def test_a_picture_source_holding_the_value_hides_the_picture_image() -> None:
    svg = f"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'><text>{SECRET}</text></svg>"
    html = f'<picture><source srcset="{svg}"><img id=m src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></picture>'
    async with _page(html) as page:
        controller = await _redacted(page)
        assert await page.evaluate("() => getComputedStyle(document.getElementById('m')).visibility") == "hidden"
        assert (await controller.evaluate(page_js.VERIFY_CALL))["remaining"] == 0
        await controller.evaluate(page_js.RESTORE_CALL)
        assert await page.evaluate("() => document.getElementById('m').getAttribute('style')") is None
        assert await page.evaluate("() => document.querySelector('source').getAttribute('srcset')") == svg


async def test_the_page_and_python_normalize_identically() -> None:
    samples = [f"a{chr(codepoint)}B" for pair in IGNORABLE_RANGES for codepoint in pair]
    samples += ["Jose\u0301", "\uff30\uff32\uff2f\uff22\uff25", " x\ty\u00a0z "]
    async with _page("<p></p>") as page:
        in_page = await page.evaluate(
            "([samples, ignorable]) => { const invisible = new RegExp(`[\\\\s${ignorable}]+`, 'gu');"
            " return samples.map((text) => text.normalize('NFKC').replace(invisible, '').toLowerCase()); }",
            [samples, JS_IGNORABLE_CLASS],
        )
    assert in_page == [normalize(sample) for sample in samples]
