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

import asyncio
import contextlib
import json
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from octowright.macros import safe_screenshot
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
        # A masked control shows only as many mask characters as its value is long.
        f"{STYLE}<input id=i style='font:40px monospace;width:880px;-webkit-text-security:disc'>"
        f"<script>document.getElementById('i').value={'x' * len(SECRET)!r}</script>",
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
    # A view transition draws a raster of the page taken before the redaction.
    "view_transition_running_from_the_old_state": (
        f"{STYLE}<style>::view-transition-group(root),::view-transition-old(root),::view-transition-new(root){{animation-duration:60s}}</style><p id=t>{SECRET}</p><script>setTimeout(()=>document.startViewTransition(()=>"
        "{document.getElementById('t').textContent='benign-new-state'}),50)</script>",
        f"{STYLE}<p id=t>benign-new-state</p>",
        SECRET,
    ),
    "view_transition_running_with_the_value_still_in_the_page": (
        f"{STYLE}<style>::view-transition-group(root),::view-transition-old(root),::view-transition-new(root){{animation-duration:60s}}</style><p id=t>{SECRET}</p><p id=o>old</p><script>setTimeout(()=>"
        "document.startViewTransition(()=>{document.getElementById('o').textContent='new'}),50)</script>",
        f"{STYLE}<p id=t>&lt;redacted&gt;</p><p id=o>new</p>",
        SECRET,
    ),
    "view_transition_started_again_during_the_capture": (
        f"{STYLE}<style>::view-transition-group(root),::view-transition-old(root),::view-transition-new(root){{animation-duration:60s}}</style><p id=t>{SECRET}</p><script>setInterval(()=>document.startViewTransition(()=>{{}}),20)</script>",
        f"{STYLE}<p id=t>&lt;redacted&gt;</p>",
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
            f'{STYLE}<picture><source srcset="{_svg("x")}"><img src="{_svg("x")}" style="visibility:hidden"></picture>',
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


# An SVG image link set by an animation draws the value while the attribute stays benign (review of 9ecfd5d6).
_SVG_BOX = "width=880 height=60"
_CASES.update(
    {
        f"svg_image_link_{kind}": (
            f"{STYLE}<svg {_SVG_BOX}><image href='{_svg('benign')}' {_SVG_BOX}>{animation}</image></svg>",
            f"{STYLE}<svg {_SVG_BOX}><image href='{_svg('benign')}' {_SVG_BOX} style='visibility:hidden'></image></svg>",
            SECRET,
        )
        for kind, animation in (
            ("animated", f"<animate attributeName='href' to='{_svg(SECRET)}' begin='0s' dur='0.05s' fill='freeze'/>"),
            ("set", f"<set attributeName='href' to='{_svg(SECRET)}' begin='0s' fill='freeze'/>"),
        )
    }
)


# Prefixed names Chrome resolves through their namespace (review of c7b4f6d5).
_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_HIDDEN_IMAGE = (
    f"{STYLE}<svg {_SVG_BOX}><image href='{_svg('benign')}' {_SVG_BOX} style='visibility:hidden'></image></svg>"
)


def _after_image(script: str) -> str:
    return (
        f"{STYLE}<svg {_SVG_BOX}><image id=im href='{_svg('benign')}' {_SVG_BOX}></image></svg>"
        f"<script>const svg=document.querySelector('svg');const im=document.getElementById('im');{script}</script>"
    )


_SET_SECRET = f"a.setAttribute('to',{json.dumps(_svg(SECRET))});a.setAttribute('begin','0s');a.setAttribute('fill','freeze');im.append(a);"
_CASES.update(
    {
        "svg_image_link_prefixed_set_element": (
            _after_image(
                f"const a=document.createElementNS('{_SVG_NS}','x:set');a.setAttribute('attributeName','href');{_SET_SECRET}"
            ),
            _HIDDEN_IMAGE,
            SECRET,
        ),
        "svg_image_link_custom_xlink_prefix": (
            _after_image(
                f"svg.setAttributeNS('http://www.w3.org/2000/xmlns/','xmlns:q','{_XLINK_NS}');"
                f"const a=document.createElementNS('{_SVG_NS}','set');a.setAttribute('attributeName','q:href');{_SET_SECRET}"
            ),
            _HIDDEN_IMAGE,
            SECRET,
        ),
        "prefixed_canvas": (
            f"{STYLE}<div id=h></div><script>const c=document.createElementNS('http://www.w3.org/1999/xhtml','x:canvas');"
            "c.width=880;c.height=80;document.getElementById('h').append(c);const x=c.getContext('2d');"
            f"x.font='40px monospace';x.fillText({json.dumps(SECRET)},0,50);</script>",
            f"{STYLE}<div id=h><canvas width=880 height=80 style='visibility:hidden'></canvas></div>",
            SECRET,
        ),
    }
)

#: A same-origin address the XHTML cases are served at, through a route; nothing is fetched from it.
_XHTML_ADDRESS = "https://octowright.test/page.xhtml"


def _xhtml(image_style: str, animation: str) -> str:
    return (
        f"<html xmlns='http://www.w3.org/1999/xhtml' xmlns:svg='{_SVG_NS}'><head><style>body{{margin:0}}</style></head>"
        f"<body><svg:svg width='880' height='60'><svg:image href='{_svg('benign')}' width='880' height='60' {image_style}>"
        f"{animation}</svg:image></svg:svg></body></html>"
    )


async def _load_xhtml(page: object, body: str) -> None:
    await page.route(_XHTML_ADDRESS, lambda route: route.fulfill(body=body, content_type="application/xhtml+xml"))  # type: ignore[attr-defined]
    await page.goto(_XHTML_ADDRESS)  # type: ignore[attr-defined]
    await page.wait_for_timeout(300)  # type: ignore[attr-defined]


async def test_an_xhtml_page_with_prefixed_svg_elements_never_keeps_the_value(tmp_path: Path) -> None:
    leaking = _xhtml("", f"<svg:set attributeName='href' to='{_svg(SECRET)}' begin='0s' fill='freeze'/>")
    never_held = _xhtml("style='visibility:hidden'", "")
    async with _browser() as browser:
        shots = {}
        for name, body in (("reference", never_held), ("raw", leaking)):
            page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
            try:
                await _load_xhtml(page, body)
                await page.screenshot(path=str(tmp_path / f"{name}.png"))
                shots[name] = (tmp_path / f"{name}.png").read_bytes()
            finally:
                await page.close()
        assert shots["raw"] != shots["reference"], "the case must render the value visibly, or it proves nothing"
        page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
        target = tmp_path / "redacted.png"
        try:
            await _load_xhtml(page, leaking)
            try:
                await redacted_screenshot(_PageSession(page), {"path": str(target)}, (SECRET,), root=tmp_path)
            except RuntimeError:
                assert not target.exists()
                return
        finally:
            await page.close()
    assert target.read_bytes() == shots["reference"]


def _closed_with(inner: str, script: str) -> str:
    """A closed shadow root holding ``inner``, and a script that can reach it as ``root``."""
    return (
        "<div id=h></div><script>const root=document.getElementById('h').attachShadow({mode:'closed'});"
        f"root.innerHTML={json.dumps(inner)};{script}</script>"
    )


# Leaks a review of 87d192b8 found in kept screenshots.
_CASES.update(
    {
        "svg_image_link_in_closed_shadow": (
            STYLE + _closed(f"<svg width=880 height=60><image href='{_svg(SECRET)}' width=880 height=60 /></svg>"),
            STYLE
            + _closed(
                f"<svg width=880 height=60><image href='{_svg('x')}' width=880 height=60 style='visibility:hidden' /></svg>"
            ),
            SECRET,
        ),
        "content_url_from_an_adopted_sheet": (
            f"{STYLE}<p id=a style='margin:0'></p>" + _adopted(f'#a::before{{content:url("{_svg(SECRET)}")}}'),
            f"{STYLE}<p id=a style='margin:0'></p>",
            SECRET,
        ),
        "content_url_in_closed_shadow": (
            STYLE + _closed(f'<style>p::before{{content:url("{_svg(SECRET)}")}}</style><p style=margin:0></p>'),
            STYLE + _closed("<p style=margin:0></p>"),
            SECRET,
        ),
        **{
            f"control_character_u{ord(control):04x}_inside": (
                f"{STYLE}<p id=t></p><script>document.getElementById('t').textContent="
                f"{json.dumps(SECRET[:13] + control + SECRET[13:])}</script>",
                f"{STYLE}<p>{_R}</p>",
                SECRET,
            )
            for control in ("\u0001", "\u007f", "\u0081")
        },
        "phone_shown_formatted": (f"{STYLE}<p>+1 (555) 013-7788</p>", f"{STYLE}<p>+{_R}</p>", "+15550137788"),
    }
)

_CANVAS_MARKUP = (
    "<canvas id=c width=800 height=80></canvas><script>const x=document.getElementById('c').getContext('2d');"
    f"x.font='40px monospace';x.fillText('{SECRET}',0,50)</script>"
)
_HIDDEN_CANVAS_MARKUP = "<style>canvas{visibility:hidden}</style>" + _CANVAS_MARKUP

_OFF = f"{STYLE}<p id=a style='margin:0'></p>"
_REVEAL = "#a::after{content:'" + SECRET + "'}"

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
    "closed_shadow_text_toggled_by_a_timer": (
        STYLE
        + _closed_with(
            "<p id=s style=margin:0></p>",
            f"const s=root.getElementById('s');setInterval(()=>{{s.textContent=s.textContent?'':{json.dumps(SECRET)}}},3)",
        ),
        STYLE + _closed_with("<p id=s style=margin:0></p>", ""),
    ),
    "closed_shadow_shown_for_each_frame": (
        STYLE
        + _closed_with(
            f"<p id=s style='margin:0' hidden>{SECRET}</p>",
            "const s=root.getElementById('s');"
            "const f=()=>{s.hidden=false;setTimeout(()=>{s.hidden=true},0);requestAnimationFrame(f)};requestAnimationFrame(f)",
        ),
        STYLE + _closed_with(f"<p id=s style='margin:0' hidden>{SECRET}</p>", ""),
    ),
    "closed_shadow_canvas_added_and_removed": (
        STYLE
        + _closed_with(
            "<div id=d></div>",
            "const d=root.getElementById('d');setInterval(()=>{const c=document.createElement('canvas');"
            f"c.width=880;c.height=80;const x=c.getContext('2d');x.font='40px monospace';x.fillText({json.dumps(SECRET)},0,50);"
            "d.append(c);setTimeout(()=>c.remove(),2)},6)",
        ),
        STYLE + _closed_with("<div id=d></div>", ""),
    ),
    "selector_text_toggled": (
        _OFF + f"<script>const st=new CSSStyleSheet();st.replaceSync({json.dumps(_REVEAL.replace('#a', '#zz'))});"
        "document.adoptedStyleSheets=[st];const r=st.cssRules[0];"
        "setInterval(()=>{r.selectorText=r.selectorText==='#a::after'?'#zz::after':'#a::after'},3)</script>",
        _OFF,
    ),
    "grouping_rule_inserted_and_deleted": (
        _OFF + "<script>const gs=new CSSStyleSheet();gs.replaceSync('@media all{}');document.adoptedStyleSheets=[gs];"
        f"const m=gs.cssRules[0];setInterval(()=>{{if(m.cssRules.length)m.deleteRule(0);else m.insertRule({json.dumps(_REVEAL)})}},3)</script>",
        _OFF,
    ),
    "adopted_sheet_pushed_and_popped": (
        _OFF + f"<script>const rv=new CSSStyleSheet();rv.replaceSync({json.dumps(_REVEAL)});let on=false;"
        "setInterval(()=>{if(on)document.adoptedStyleSheets.pop();else document.adoptedStyleSheets.push(rv);on=!on},3)</script>",
        _OFF,
    ),
    "stylesheet_disabled_toggled": (
        _OFF
        + "<style id=hide>#a::after{display:none}</style>"
        + _adopted(_REVEAL)
        + "<script>const hs=document.getElementById('hide').sheet;setInterval(()=>{hs.disabled=!hs.disabled},3)</script>",
        _OFF,
    ),
    "rule_style_toggled_by_property_name": (
        _OFF + f"<script>const ns=new CSSStyleSheet();ns.replaceSync({json.dumps(_REVEAL[:-1] + ';display:none}')});"
        "document.adoptedStyleSheets=[ns];const rr=ns.cssRules[0];"
        "setInterval(()=>{rr.style.display=rr.style.display==='none'?'inline':'none'},3)</script>",
        _OFF,
    ),
    "focus_toggle_reveals_generated_content": (
        f"{STYLE}<input id=f style='width:1px;height:1px;border:0;padding:0;outline:none'><p id=a style='margin:0'></p>"
        + _adopted("#f:focus ~ " + _REVEAL)
        + "<script>const fi=document.getElementById('f');"
        "setInterval(()=>{if(document.activeElement===fi)fi.blur();else fi.focus()},3)</script>",
        f"{STYLE}<input id=f style='width:1px;height:1px;border:0;padding:0;outline:none'><p id=a style='margin:0'></p>",
    ),
    "script_animation_reveals_generated_content": (
        _OFF
        + _adopted(_REVEAL[:-1] + ";display:none}")
        + "<script>const ae=document.getElementById('a');setInterval(()=>{const an=ae.animate("
        "[{display:'inline'},{display:'inline'}],{duration:100000,pseudoElement:'::after'});setTimeout(()=>an.cancel(),2)},6)</script>",
        _OFF,
    ),
}

# Ordinary pages that keep writing to themselves; the screenshot must be taken, not refused.
_BUSY = {
    "custom_property_written_every_frame": (
        f"{STYLE}<p>{SECRET}</p><div id=b></div><script>let n=0;"
        "const f=()=>{document.getElementById('b').style.setProperty('--n',String(n++));requestAnimationFrame(f)};f()</script>",
        f"{STYLE}<p>{_R}</p><div id=b></div>",
    ),
    "spinner_transform_written_every_frame": (
        f"{STYLE}<p>{SECRET}</p><div id=b style='width:0;height:0'></div><script>let n=0;"
        "const f=()=>{document.getElementById('b').style.transform='rotate('+(n++)+'deg)';requestAnimationFrame(f)};f()</script>",
        f"{STYLE}<p>{_R}</p><div id=b style='width:0;height:0'></div>",
    ),
    "input_value_written_again_every_frame": (
        f"{STYLE}<input id=i {_FIELD} value={json.dumps(SECRET)}><script>const i=document.getElementById('i');"
        "const f=()=>{i.value=i.value;requestAnimationFrame(f)};f()</script>",
        f"{STYLE}<input id=i style='font:40px monospace;width:880px;-webkit-text-security:disc' value={'x' * len(SECRET)}>",
    ),
    "stylesheet_text_holding_the_value": (
        f"{STYLE}<style>#a::after{{content:'{SECRET}'}}</style><p id=a style='margin:0'></p>",
        f"{STYLE}<style>#a::after{{content:'<redacted>'}}</style><p id=a style='margin:0'></p>",
    ),
    "picture_source": _CASES["picture_source"][:2],
    "closed_shadow_root": _CASES["closed_shadow_root"][:2],
    "phone_shown_formatted": _CASES["phone_shown_formatted"][:2],
    "canvas_with_page_transitions": ("<style>*{transition:all 5s}</style>" + _CANVAS_MARKUP, _HIDDEN_CANVAS_MARKUP),
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


@pytest.mark.parametrize("name", sorted(_BUSY))
async def test_an_ordinary_page_that_keeps_writing_to_itself_is_still_screenshotted(name: str, tmp_path: Path) -> None:
    html, never_held = _BUSY[name]
    secret = "+15550137788" if name == "phone_shown_formatted" else SECRET
    async with _browser() as browser:
        reference = await _render(browser, never_held, tmp_path / f"{name}.reference.png")
        redacted = await _redacted(browser, html, secret, tmp_path, name)
    assert redacted == reference


async def test_page_animations_hold_still_for_the_capture_and_resume_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    times: list[float] = []
    capture = safe_screenshot._capture
    read = {"expression": "document.getAnimations()[0].currentTime", "returnByValue": True}

    async def capture_while_watching_the_clock(cdp: object, target: Path) -> None:
        for _ in range(2):
            reply = await cdp.send("Runtime.evaluate", read)  # type: ignore[attr-defined]
            times.append(float(reply["result"]["value"]))
            await asyncio.sleep(0.2)
        await capture(cdp, target)

    monkeypatch.setattr(safe_screenshot, "_capture", capture_while_watching_the_clock)
    html = f"{STYLE}<style>@keyframes k{{to{{opacity:.5}}}}</style><div style='animation:k 10s linear infinite'>.</div><p>{SECRET}</p>"
    async with _browser() as browser:
        page = await browser.new_page(viewport=VIEWPORT)  # type: ignore[attr-defined]
        await page.set_content(html)
        await page.wait_for_timeout(100)
        assert await redacted_screenshot(
            _PageSession(page), {"path": str(tmp_path / "a.png")}, (SECRET,), root=tmp_path
        ) == (1, 0)
        assert len(times) == 2 and times[0] == times[1]
        after = await page.evaluate("() => document.getAnimations()[0].currentTime")
        await page.wait_for_timeout(200)
        assert await page.evaluate("() => document.getAnimations()[0].currentTime") > after


async def test_an_html_page_with_prefixed_office_tags_is_still_screenshotted(tmp_path: Path) -> None:
    html = f"{STYLE}<p>{SECRET}</p><w:frame>frame-text</w:frame>"
    never_held = f"{STYLE}<p>{_R}</p><w:frame style='visibility:hidden;opacity:0'>frame-text</w:frame>"
    async with _browser() as browser:
        reference = await _render(browser, never_held, tmp_path / "office.reference.png")
        redacted = await _redacted(browser, html, SECRET, tmp_path, "office")
    assert redacted == reference
