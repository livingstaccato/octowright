# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The page controller and the DevTools change count on a real page: what counts, what matches, what is restored."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from octowright.macros.page_devtools import PageChanges, PageController
from octowright.macros.privacy import sensitive_value_variants
from octowright.macros.redaction_text import IGNORABLE_RANGES

pytestmark = pytest.mark.live_browser

SECRET = "Controller-Canary-3b8d"  # pragma: allowlist secret

_NO_ENGINE = ("executable doesn't exist", "missing x server", "no protocol specified", "playwright install")

_PAGE = (
    "<style id=st>#a::after{content:'x'} @media all { .m{color:red} }</style>"
    "<p id=t>hello</p><p id=a></p><input id=i value=v><input id=c type=checkbox><div popover id=pop>p</div>"
    "<select id=s><option>a</option><option>b</option></select><div id=h></div><div id=k></div>"
    "<select id=ms multiple><option>a</option><option>b</option></select>"
    "<script>window.__closed = document.getElementById('k').attachShadow({mode: 'closed'});"
    "window.__closed.innerHTML = '<p id=ct>inside</p>';</script>"
)

_SETTLED = "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))"


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


@dataclass
class _Watched:
    page: Any
    controller: PageController
    changes: PageChanges

    async def changed(self) -> int:
        await self.page.evaluate(_SETTLED)
        report = await self.controller.verify()
        return int(report["changed"]) + self.changes.count

    async def remaining(self) -> int:
        return int((await self.controller.verify())["remaining"])

    async def restore(self) -> None:
        self.changes.end()
        await self.controller.restore()


@contextlib.asynccontextmanager
async def _watched(page: Any, values: tuple[str, ...] = (SECRET,)) -> AsyncIterator[_Watched]:
    """Redact as the screenshot does, then count; restore and release on the way out."""
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Animation.enable")
    changes = PageChanges(cdp)
    spellings = list(sensitive_value_variants(values))
    closed_roots = await changes.start()
    controller = await PageController.create(cdp, spellings)
    try:
        await controller.redact(closed_roots)
        await controller.watch(await changes.sheets_hold(spellings))
        changes.begin()
        yield _Watched(page, controller, changes)
    finally:
        changes.end()
        await controller.restore()
        await controller.dispose()
        await changes.close()
        await cdp.detach()


_CHANGES = {
    "child added": "() => document.body.append(document.createElement('i'))",
    "text edited": "() => { document.getElementById('t').firstChild.nodeValue = 'other'; }",
    "attribute set": "() => document.getElementById('t').setAttribute('data-x', '1')",
    "input value toggled back": "() => { const i = document.getElementById('i'); i.value = 'x'; i.value = 'v'; }",
    "selection toggled back": "() => { const s = document.getElementById('s'); s.selectedIndex = 1; s.selectedIndex = 0; }",
    "stylesheet rule": "() => { const s = document.styleSheets[0]; s.insertRule('p{color:red}'); s.deleteRule(0); }",
    # A sheet adopted and dropped before the next frame never renders and is not reported; one that
    # lasts into a frame is.
    "adopted stylesheets": (
        "() => { const s = new CSSStyleSheet(); s.replaceSync('p{}'); document.adoptedStyleSheets = [s];"
        " requestAnimationFrame(() => requestAnimationFrame(() => { document.adoptedStyleSheets = []; })); }"
    ),
    "adopted push and pop": (
        "() => { const s = new CSSStyleSheet(); s.replaceSync('p{}'); document.adoptedStyleSheets.push(s);"
        " requestAnimationFrame(() => requestAnimationFrame(() => document.adoptedStyleSheets.pop())); }"
    ),
    "selector text": "() => { document.getElementById('st').sheet.cssRules[0].selectorText = '#zz::after'; }",
    "grouping rule insert": "() => { document.getElementById('st').sheet.cssRules[1].insertRule('.n{color:blue}', 0); }",
    "rule style by property name": "() => { document.getElementById('st').sheet.cssRules[0].style.color = 'green'; }",
    "stylesheet disabled": "() => { document.getElementById('st').sheet.disabled = true; }",
    "open shadow root": "() => document.getElementById('h').attachShadow({mode: 'open'})",
    "closed shadow root": "() => { document.getElementById('h').attachShadow({mode: 'closed'}).innerHTML = '<b>x</b>'; }",
    "closed shadow text": "() => { window.__closed.getElementById('ct').firstChild.nodeValue = 'x'; }",
    "checked": "() => { document.getElementById('c').checked = true; }",
    "option selected in a list box": "() => { document.getElementById('ms').options[1].selected = true; }",
    "indeterminate": "() => { document.getElementById('c').indeterminate = true; }",
    "custom validity": "() => document.getElementById('i').setCustomValidity('bad')",
    "focus": "() => document.getElementById('i').focus()",
    "location hash": "() => { location.hash = 'moved'; }",
    "popover": "() => document.getElementById('pop').showPopover()",
    "script animation": "() => { document.getElementById('t').animate([{opacity: 0}, {opacity: 1}], 1000); }",
}

_HARMLESS = {
    "inline style on an element the redaction did not touch": (
        "() => { const t = document.getElementById('t').style; t.transform = 'rotate(2deg)'; t.setProperty('--n', '1'); }"
    ),
    "style attribute rewritten on an element the redaction did not touch": (
        "() => document.getElementById('t').setAttribute('style', 'color: red')"
    ),
    "the same input value written again": "() => { const i = document.getElementById('i'); i.value = i.value; }",
    "checked written unchanged": "() => { document.getElementById('c').checked = false; }",
    "the same validity message": "() => document.getElementById('i').setCustomValidity('')",
}


async def test_an_untouched_page_reports_no_change() -> None:
    async with _page(_PAGE) as page, _watched(page) as watched:
        await page.evaluate("() => [document.getElementById('i').value, document.styleSheets.length]")
        assert await watched.changed() == 0
        assert await watched.remaining() == 0


@pytest.mark.parametrize("name", sorted(_CHANGES))
async def test_every_kind_of_page_change_is_counted(name: str) -> None:
    async with _page(_PAGE) as page, _watched(page) as watched:
        await page.evaluate(_CHANGES[name])
        assert await watched.changed() >= 1


@pytest.mark.parametrize("name", sorted(_HARMLESS))
async def test_a_change_that_cannot_reveal_a_value_is_not_counted(name: str) -> None:
    async with _page(_PAGE) as page, _watched(page) as watched:
        await page.evaluate(_HARMLESS[name])
        assert await watched.changed() == 0


@pytest.mark.parametrize(
    ("html", "script"),
    [
        ("<canvas id=x></canvas>", "() => { document.getElementById('x').style.width = '3px'; }"),
        (
            "<p id=x>.</p>",
            f"() => {{ document.getElementById('x').style.backgroundImage = 'url(\"https://app.test/{SECRET}\")'; }}",
        ),
        (
            "<p id=x>.</p><script>const s = new CSSStyleSheet();"
            f"s.replaceSync('#x::after{{content:\"{SECRET}\";display:none}}'); document.adoptedStyleSheets = [s];</script>",
            "() => { document.getElementById('x').style.transform = 'rotate(1deg)'; }",
        ),
    ],
    ids=["styled-element", "style-holding-the-value", "stylesheet-holding-the-value"],
)
async def test_an_inline_style_change_that_could_reveal_a_value_is_counted(html: str, script: str) -> None:
    async with _page(html) as page, _watched(page) as watched:
        await page.evaluate(script)
        assert await watched.changed() >= 1


async def test_restore_removes_every_hook_and_listener_it_installed() -> None:
    native = (
        "() => [Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked').set,"
        " HTMLInputElement.prototype.setCustomValidity,"
        " Object.getOwnPropertyDescriptor(HTMLOptionElement.prototype, 'selected').set]"
    )
    async with _page(_PAGE) as page:
        await page.evaluate(f"() => {{ window.__native = ({native})(); }}")
        async with _watched(page) as watched:
            assert await page.evaluate(f"() => ({native})()[0] !== window.__native[0]") is True
            await watched.restore()
            assert await page.evaluate(f"() => ({native})().every((fn, index) => fn === window.__native[index])")
            await page.evaluate(_CHANGES["focus"])
            assert watched.changes.count == 0


@pytest.mark.parametrize(
    "shown",
    [SECRET.lower(), SECRET[:11] + "​" + SECRET[11:], SECRET[:11] + "­" + SECRET[11:], SECRET[:11] + " " + SECRET[11:]],
    ids=["other-case", "zero-width-space", "soft-hyphen", "space"],
)
async def test_the_page_redacts_every_spelling_of_the_value(shown: str) -> None:
    async with _page("<p id=t></p>") as page:
        await page.evaluate("(text) => { document.getElementById('t').textContent = text; }", f"x {shown} y")
        async with _watched(page) as watched:
            assert await page.evaluate("() => document.getElementById('t').textContent") == "x <redacted> y"
            assert await watched.remaining() == 0


async def test_every_invisible_character_inside_the_value_is_redacted_in_the_page() -> None:
    characters = [chr(codepoint) for pair in IGNORABLE_RANGES for codepoint in pair]
    async with _page("<main id=m></main>") as page:
        await page.evaluate(
            "([parts, characters]) => { const m = document.getElementById('m'); for (const c of characters) {"
            " const p = document.createElement('p'); p.textContent = `x ${parts[0]}${c}${parts[1]} y`; m.append(p); } }",
            [[SECRET[:11], SECRET[11:]], characters],
        )
        async with _watched(page) as watched:
            texts = await page.evaluate("() => Array.from(document.querySelectorAll('p'), (p) => p.textContent)")
            assert texts == ["x <redacted> y"] * len(characters)
            assert await watched.remaining() == 0


async def test_a_value_the_page_patterns_cannot_find_is_replaced_whole() -> None:
    # Full-width letters equal the value only after compatibility normalization, which the page's
    # patterns do not apply, so only the whole-string fallback removes them.
    shown = "".join(chr(ord(character) + 0xFEE0) if "!" <= character <= "~" else character for character in SECRET)
    async with _page("<p id=t></p>") as page:
        await page.evaluate("(text) => { document.getElementById('t').textContent = text; }", f"x {shown} y")
        async with _watched(page) as watched:
            assert await page.evaluate("() => document.getElementById('t').textContent") == "<redacted>"
            assert await watched.remaining() == 0


def _svg_image(animation: str) -> str:
    return (
        "<svg width=100 height=20><image id=im href='data:image/svg+xml,benign' width=100 height=20>"
        f"{animation}</image></svg>"
    )


_VISIBILITY = "() => getComputedStyle(document.getElementById('im')).visibility"


async def test_an_svg_image_whose_link_an_animation_sets_to_the_value_is_hidden_and_restored() -> None:
    html = _svg_image(f"<set attributeName='href' to='data:image/svg+xml,{SECRET}' begin='0s' fill='freeze'/>")
    async with _page(html) as page:
        await page.wait_for_timeout(100)
        assert SECRET in await page.evaluate("() => document.getElementById('im').href.animVal")
        async with _watched(page) as watched:
            assert await page.evaluate(_VISIBILITY) == "hidden"
            assert await watched.remaining() == 0
        assert await page.evaluate(_VISIBILITY) == "visible"


async def test_an_svg_link_animation_hides_its_image_before_it_begins() -> None:
    html = _svg_image("<animate attributeName='xlink:href' to='data:image/svg+xml,later' begin='indefinite' dur='1s'/>")
    async with _page(html) as page, _watched(page) as watched:
        assert await page.evaluate(_VISIBILITY) == "hidden"
        assert await watched.remaining() == 0


async def test_an_svg_image_animated_in_another_attribute_stays_visible() -> None:
    html = _svg_image("<animate attributeName='opacity' from='0' to='1' begin='0s' dur='1s'/>")
    async with _page(html) as page, _watched(page) as watched:
        assert await page.evaluate(_VISIBILITY) == "visible"
        assert await watched.remaining() == 0


async def test_a_link_animation_on_an_svg_element_that_draws_no_resource_hides_nothing() -> None:
    html = (
        "<svg width=100 height=20><a id=lk href='#one'><text y=15>link</text>"
        "<animate attributeName='href' to='#two' begin='indefinite' dur='1s'/></a></svg>"
    )
    async with _page(html) as page, _watched(page) as watched:
        assert await page.evaluate("() => getComputedStyle(document.getElementById('lk')).visibility") == "visible"
        assert await watched.remaining() == 0


async def test_an_svg_link_animation_added_after_redaction_is_left_in_the_page() -> None:
    async with _page(_svg_image("")) as page, _watched(page) as watched:
        await page.evaluate(
            "() => { const a = document.createElementNS('http://www.w3.org/2000/svg', 'set');"
            " a.setAttribute('attributeName', 'href'); a.setAttribute('to', 'data:image/svg+xml,x');"
            " document.getElementById('im').append(a); }"
        )
        assert await watched.remaining() >= 1


async def test_a_filter_image_whose_link_is_animated_is_refused() -> None:
    html = (
        "<svg width=100 height=20><filter id=f><feImage href='data:image/svg+xml,benign'>"
        "<animate attributeName='href' to='data:image/svg+xml,x' begin='indefinite' dur='1s'/></feImage></filter>"
        "<rect width=100 height=20 filter='url(#f)'/></svg>"
    )
    async with _page(html) as page, _watched(page) as watched:
        assert await watched.remaining() >= 1


async def test_a_formatted_number_is_redacted_by_its_digits_and_restored() -> None:
    async with _page("<p id=t>Call +1 (555) 013-7788 or 013-7788 now</p>") as page:
        async with _watched(page, ("+15550137788",)) as watched:
            assert (
                await page.evaluate("() => document.getElementById('t').textContent")
                == "Call +<redacted> or <redacted> now"
            )
            assert await watched.remaining() == 0
        assert (
            await page.evaluate("() => document.getElementById('t').textContent")
            == "Call +1 (555) 013-7788 or 013-7788 now"
        )


async def test_a_placeholder_spelled_with_invisible_characters_is_redacted() -> None:
    async with _page("<input id=f>") as page:
        await page.evaluate(
            "(text) => document.getElementById('f').setAttribute('placeholder', text)", SECRET[:11] + "​" + SECRET[11:]
        )
        async with _watched(page) as watched:
            assert await page.evaluate("() => document.getElementById('f').getAttribute('placeholder')") == "<redacted>"
            assert await watched.remaining() == 0


@pytest.mark.parametrize("kind", ["text", "email", "tel", "password", "search", "url", "no-such-type"])
async def test_a_text_control_holding_the_value_is_masked_and_keeps_its_value(kind: str) -> None:
    async with _page(f"<input id=f type={kind}>") as page:
        await page.evaluate("(text) => { document.getElementById('f').value = text; }", SECRET[:11] + "​" + SECRET[11:])
        async with _watched(page) as watched:
            state = await page.evaluate(
                "() => { const f = document.getElementById('f');"
                " return [f.value, getComputedStyle(f).getPropertyValue('-webkit-text-security')]; }"
            )
            assert state == [SECRET[:11] + "​" + SECRET[11:], "disc"]
            assert await watched.remaining() == 0
        assert await page.evaluate("() => document.getElementById('f').getAttribute('style')") is None


async def test_a_button_or_hidden_value_is_replaced_and_restored() -> None:
    async with _page(f"<input id=b type=button value='{SECRET}'><input id=h type=hidden value='{SECRET}'>") as page:
        async with _watched(page) as watched:
            assert await page.evaluate("() => [b.value, h.value]") == ["<redacted>", "<redacted>"]
            assert await watched.remaining() == 0
        assert await page.evaluate("() => [b.value, h.value, b.getAttribute('value')]") == [SECRET, SECRET, SECRET]


async def test_a_focused_text_input_keeps_its_value_and_selection() -> None:
    async with _page(f"<input id=i value='x {SECRET} y'>") as page:
        await page.evaluate(
            "() => { const i = document.getElementById('i'); i.focus(); i.setSelectionRange(2, 5, 'backward'); }"
        )
        async with _watched(page):
            pass
        state = await page.evaluate(
            "() => { const i = document.getElementById('i');"
            " return [i.value, i.selectionStart, i.selectionEnd, i.selectionDirection, document.activeElement === i]; }"
        )
        assert state == [f"x {SECRET} y", 2, 5, "backward", True]


async def test_a_focused_email_input_keeps_its_caret() -> None:
    async with _page(f"<input id=e type=email value='a{SECRET}@example.test'>") as page:
        await page.focus("#e")
        await page.keyboard.press("Home")
        for _ in range(3):
            await page.keyboard.press("ArrowRight")
        async with _watched(page) as watched:
            assert await watched.remaining() == 0
        await page.keyboard.insert_text("Z")
        assert (
            await page.evaluate("() => document.getElementById('e').value")
            == f"a{SECRET[:2]}Z{SECRET[2:]}@example.test"
        )


async def test_a_pristine_textarea_keeps_its_selection_direction() -> None:
    async with _page(f"<textarea id=t>x {SECRET} y</textarea>") as page:
        await page.evaluate(
            "() => { const t = document.getElementById('t'); t.focus(); t.setSelectionRange(2, 5, 'backward'); }"
        )
        async with _watched(page) as watched:
            assert await page.evaluate("() => getComputedStyle(t).getPropertyValue('-webkit-text-security')") == "disc"
            assert await watched.remaining() == 0
        state = await page.evaluate("() => [t.value, t.selectionStart, t.selectionEnd, t.selectionDirection]")
        assert state == [f"x {SECRET} y", 2, 5, "backward"]


async def test_a_closed_shadow_root_is_redacted_and_restored() -> None:
    html = f"<div id=k></div><script>window.__r = k.attachShadow({{mode: 'closed'}}); __r.innerHTML = '<p title=\"{SECRET}\">{SECRET}</p>';</script>"
    async with _page(html) as page:
        async with _watched(page) as watched:
            assert (
                await page.evaluate("() => [__r.querySelector('p').textContent, __r.querySelector('p').title]")
                == ["<redacted>"] * 2
            )
            assert await watched.remaining() == 0
        assert await page.evaluate("() => __r.innerHTML") == f'<p title="{SECRET}">{SECRET}</p>'


async def test_a_frame_document_holding_the_value_is_hidden_and_never_reloaded() -> None:
    async with _page(f"<iframe id=f srcdoc='<p>{SECRET}</p>'></iframe>") as page:
        await page.wait_for_function("() => document.getElementById('f').contentDocument.body")
        await page.evaluate(
            "() => { const f = document.getElementById('f'); f.contentWindow.__marker = 42; window.__loads = 0;"
            " f.addEventListener('load', () => { window.__loads += 1; }); }"
        )
        async with _watched(page) as watched:
            assert await page.evaluate("() => getComputedStyle(document.getElementById('f')).visibility") == "hidden"
            assert await watched.remaining() == 0
        await page.wait_for_timeout(300)
        state = await page.evaluate(
            "() => { const f = document.getElementById('f');"
            " return [window.__loads, f.contentWindow.__marker, f.getAttribute('srcdoc'), f.getAttribute('style')]; }"
        )
        assert state == [0, 42, f"<p>{SECRET}</p>", None]


async def test_restore_keeps_a_style_change_the_page_made_meanwhile() -> None:
    async with _page("<canvas id=c style='width:100px'></canvas><canvas id=u></canvas>") as page:
        async with _watched(page):
            await page.evaluate("() => { document.getElementById('c').style.width = '300px'; }")
        state = await page.evaluate(
            "() => { const c = document.getElementById('c').style;"
            " return [c.width, c.visibility, c.transition, c.animation, document.getElementById('u').getAttribute('style')]; }"
        )
        assert state == ["300px", "", "", "", None]


async def test_a_picture_source_holding_the_value_hides_the_picture_image() -> None:
    svg = f"data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'><text>{SECRET}</text></svg>"
    html = f'<picture><source srcset="{svg}"><img id=m src="data:image/gif;base64,R0lGODlhAQABAAAAACw="></picture>'
    async with _page(html) as page:
        async with _watched(page) as watched:
            assert await page.evaluate("() => getComputedStyle(document.getElementById('m')).visibility") == "hidden"
            assert await watched.remaining() == 0
        assert await page.evaluate("() => document.getElementById('m').getAttribute('style')") is None
        assert await page.evaluate("() => document.querySelector('source').getAttribute('srcset')") == svg
