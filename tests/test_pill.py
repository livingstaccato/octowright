# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Macro status pill — chip helpers, action description, and live DOM verification.

Covers the bottom-center pill that appears while macros are running:
  - the chip shown on the left (matches the corner-badge color)
  - the action-description formatter (used as the pill label)
  - end-to-end render / push lifecycle / Alt-modifier click-through / modal
  - per-action slowmo delay

Init-script renders need a real Playwright launch; those tests skip when
Playwright is not installed.
"""

from __future__ import annotations

from typing import Any

import pytest

# The pill renders its contents inside a CLOSED shadow root (so page automation
# cannot pierce it — see test_macro_pill_overlay.py). Closed roots are not
# exposed via host.shadowRoot, so to introspect the internal DOM a test installs
# this hook BEFORE the pill builds: it wraps attachShadow to stash the pill's
# root on window. This inspects production behavior without weakening it.
_CAPTURE_PILL_SHADOW = """() => {
    const orig = Element.prototype.attachShadow;
    window.__pillShadow = null;
    Element.prototype.attachShadow = function (init) {
        const root = orig.call(this, init);
        if (this.id === '__octowright_macro_status__') window.__pillShadow = root;
        return root;
    };
}"""


def _pill_inner_text(role: str) -> str:
    """JS expr: textContent of a [data-role] element inside the closed pill shadow."""
    return f"window.__pillShadow.querySelector('[data-role=\"{role}\"]').textContent"


def test_describe_action_uses_first_informative_field() -> None:
    """First non-empty hint field wins so the pill stays single-line."""
    from octowright.browser_pool.visuals import _describe_action

    # name beats text/role
    assert _describe_action({"action": "click", "name": "Sign in", "text": "x"}) == "click name=Sign in"
    # falls through to selector when locator fields absent
    assert _describe_action({"action": "fill", "selector": "#q", "value": "v"}) == "fill selector=#q"
    # bare action when no hint fields are present
    assert _describe_action({"action": "wait_for"}) == "wait_for"
    # long values get clipped with an ellipsis
    long_url = "https://octowright.com/" + "a" * 80
    out = _describe_action({"action": "navigate", "url": long_url})
    assert out.startswith("navigate url=")
    assert out.endswith("…")
    assert len(out) <= len("navigate url=") + 40


def test_macro_pill_chip_uses_label_and_stable_color() -> None:
    from octowright.browser_pool.visuals import _badge_color_for, _macro_pill_chip_for

    text, color = _macro_pill_chip_for(profile=None, label="probe", instance_id="abcdef123456")
    assert text == "probe"
    # Same seed as the corner badge so both indicators match colors.
    assert color == _badge_color_for("probe")


def test_macro_pill_chip_falls_back_to_short_id() -> None:
    from octowright.browser_pool.visuals import _macro_pill_chip_for

    text, _color = _macro_pill_chip_for(profile=None, label=None, instance_id="abcdef123456")
    # 4-char slice (shorter than the badge's 6) keeps the pill compact.
    assert text == "abcd"


@pytest.mark.asyncio
async def test_macro_status_pill_renders_and_responds(tmp_path) -> None:
    """Init script must inject the pill API; show/hide round-trip must work,
    chip must be wired with launch metadata, elapsed must tick, and slowmo
    must actually delay action dispatch."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool

    monkey_recordings = tmp_path / "rec"
    monkey_recordings.mkdir()
    original = _defaults.RECORDINGS_DIR
    _defaults.RECORDINGS_DIR = monkey_recordings
    import octowright.browser_pool.pool as _pool

    original_pool = _pool.RECORDINGS_DIR
    _pool.RECORDINGS_DIR = monkey_recordings

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>Probe</h1></body></html>",
            headed=False,
            label="status",
            viewport_w=400,
            viewport_h=300,
        )
        session = pool.get(result["instance_id"])
        page = session.page

        # API must be installed by addInitScript before goto resolves.
        api_present = await page.evaluate("typeof window.__octowright_macro_status === 'function'")
        assert api_present is True

        # Pill is hidden by default (root element doesn't exist until first show()).
        assert await page.evaluate("!!document.getElementById('__octowright_macro_status__')") is False

        # Capture the pill's CLOSED shadow root before it is built, so the
        # internal-DOM assertions below can read it (the page itself cannot).
        await page.evaluate(_CAPTURE_PILL_SHADOW)

        # Push a status — root appears, opacity rises, structured DOM is built.
        await page.evaluate(
            "(p) => window.__octowright_macro_status(p)",
            {"text": "demo | click name=Sign in", "visible": True, "start": True},
        )
        assert await page.evaluate("!!document.getElementById('__octowright_macro_status__')") is True

        chip_text = await page.evaluate(_pill_inner_text("chip"))
        assert chip_text == "status"  # matches the launch label

        label_text = await page.evaluate(_pill_inner_text("label"))
        assert label_text == "demo | click name=Sign in"

        opacity = await page.evaluate("document.getElementById('__octowright_macro_status__').style.opacity")
        assert float(opacity) > 0

        # Elapsed segment ticks. Sleep > 100ms (the tick interval) and check it
        # advances past 0.0s.
        import asyncio

        await asyncio.sleep(0.25)
        elapsed_text = await page.evaluate(_pill_inner_text("elapsed"))
        # Format is e.g. "0.2s" or "0.3s" — non-empty, ends with 's'.
        assert elapsed_text.endswith("s") and elapsed_text != "0.0s"

        # Pill is anchored bottom-center via inline style.
        css = await page.evaluate(
            "(() => { const e = document.getElementById('__octowright_macro_status__');"
            " return JSON.stringify({left: e.style.left, bottom: e.style.bottom,"
            " transform: e.style.transform, top: e.style.top, right: e.style.right}); })()"
        )
        import json as _json

        css_dict = _json.loads(css)
        assert css_dict["left"] == "50%"
        assert css_dict["bottom"] == "14px"
        assert "translateX(-50%)" in css_dict["transform"]
        assert css_dict["top"] == ""
        assert css_dict["right"] == ""

        # `done` push keeps the pill visible and freezes the elapsed counter.
        await page.evaluate(
            "(p) => window.__octowright_macro_status(p)",
            {"text": "demo | done", "done": True},
        )
        opacity_done = await page.evaluate("document.getElementById('__octowright_macro_status__').style.opacity")
        assert float(opacity_done) > 0, "done push should leave pill visible"
        frozen_text = await page.evaluate(_pill_inner_text("elapsed"))
        # Sleep past the AUTO_HIDE_MS budget; pill must stay visible because
        # `done` suspends the auto-hide timer.
        await asyncio.sleep(0.6)
        opacity_after = await page.evaluate("document.getElementById('__octowright_macro_status__').style.opacity")
        assert float(opacity_after) > 0, "done state must not auto-hide"
        # Elapsed text should NOT change after done — the counter is frozen.
        elapsed_after = await page.evaluate(_pill_inner_text("elapsed"))
        assert elapsed_after == frozen_text, f"elapsed kept ticking after done: {frozen_text!r} -> {elapsed_after!r}"

        # Explicit visible:false still drops opacity back to 0.
        await page.evaluate("(p) => window.__octowright_macro_status(p)", {"visible": False})
        opacity_hidden = await page.evaluate("document.getElementById('__octowright_macro_status__').style.opacity")
        assert float(opacity_hidden) == 0
    finally:
        await pool.shutdown()
        _defaults.RECORDINGS_DIR = original
        _pool.RECORDINGS_DIR = original_pool


@pytest.mark.asyncio
async def test_macro_pill_alt_modifier_toggles_pointer_events(tmp_path) -> None:
    """Holding Alt must flip the pill from click-through to clickable."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool

    monkey_recordings = tmp_path / "rec"
    monkey_recordings.mkdir()
    original = _defaults.RECORDINGS_DIR
    _defaults.RECORDINGS_DIR = monkey_recordings
    import octowright.browser_pool.pool as _pool

    original_pool = _pool.RECORDINGS_DIR
    _pool.RECORDINGS_DIR = monkey_recordings

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>x</h1></body></html>",
            headed=False,
            label="modtest",
            viewport_w=400,
            viewport_h=300,
        )
        session = pool.get(result["instance_id"])
        page = session.page

        # Materialize the pill so the modifier listener has something to flip.
        await page.evaluate(
            "(p) => window.__octowright_macro_status(p)",
            {"text": "demo | step", "start": True},
        )

        # Default: clicks should fall through (pointer-events:none).
        default_pe = await page.evaluate("document.getElementById('__octowright_macro_status__').style.pointerEvents")
        assert default_pe == "none"

        # Press Alt — listener should set pointer-events:auto.
        await page.keyboard.down("Alt")
        await page.wait_for_function(
            "document.getElementById('__octowright_macro_status__').style.pointerEvents === 'auto'",
            timeout=2000,
        )
        modifier_pe = await page.evaluate("document.getElementById('__octowright_macro_status__').style.pointerEvents")
        assert modifier_pe == "auto"
        cursor = await page.evaluate("document.getElementById('__octowright_macro_status__').style.cursor")
        assert cursor == "pointer"

        # Release Alt — back to click-through.
        await page.keyboard.up("Alt")
        await page.wait_for_function(
            "document.getElementById('__octowright_macro_status__').style.pointerEvents === 'none'",
            timeout=2000,
        )

        # Click while Alt is down: handler must open the run-history modal.
        await page.keyboard.down("Alt")
        await page.wait_for_function(
            "document.getElementById('__octowright_macro_status__').style.pointerEvents === 'auto'",
            timeout=2000,
        )
        box = await page.evaluate(
            "(() => { const r = document.getElementById('__octowright_macro_status__').getBoundingClientRect();"
            " return { x: r.x + r.width/2, y: r.y + r.height/2 }; })()"
        )
        await page.mouse.click(box["x"], box["y"])
        await page.wait_for_function("!!document.getElementById('__octowright_macro_modal__')", timeout=2000)
        # Modal must list the start push we made earlier.
        modal_text = await page.evaluate("document.getElementById('__octowright_macro_modal__').textContent")
        assert "demo | step" in modal_text, f"modal missing run entry: {modal_text!r}"

        # Escape dismisses the modal.
        await page.keyboard.press("Escape")
        await page.wait_for_function("!document.getElementById('__octowright_macro_modal__')", timeout=2000)
        await page.keyboard.up("Alt")
    finally:
        await pool.shutdown()
        _defaults.RECORDINGS_DIR = original
        _pool.RECORDINGS_DIR = original_pool


@pytest.mark.asyncio
async def test_macro_pill_modal_close_button_and_backdrop(tmp_path) -> None:
    """X button and backdrop click both dismiss the run-history modal."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool

    monkey_recordings = tmp_path / "rec"
    monkey_recordings.mkdir()
    original = _defaults.RECORDINGS_DIR
    _defaults.RECORDINGS_DIR = monkey_recordings
    import octowright.browser_pool.pool as _pool

    original_pool = _pool.RECORDINGS_DIR
    _pool.RECORDINGS_DIR = monkey_recordings

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>x</h1></body></html>",
            headed=False,
            label="modaltest",
            viewport_w=600,
            viewport_h=500,
        )
        session = pool.get(result["instance_id"])
        page = session.page

        # Push a few entries so the modal has something to render.
        await page.evaluate(
            "(p) => window.__octowright_macro_status(p)",
            {"text": "demo | starting", "start": True},
        )
        await page.evaluate(
            "(p) => window.__octowright_macro_status(p)",
            {"text": "demo | click name=Sign in"},
        )
        await page.evaluate(
            "(p) => window.__octowright_macro_status(p)",
            {"text": "demo | done", "done": True},
        )

        async def _open_modal() -> None:
            await page.keyboard.down("Alt")
            await page.wait_for_function(
                "document.getElementById('__octowright_macro_status__').style.pointerEvents === 'auto'",
                timeout=2000,
            )
            box = await page.evaluate(
                "(() => { const r = document.getElementById('__octowright_macro_status__').getBoundingClientRect();"
                " return { x: r.x + r.width/2, y: r.y + r.height/2 }; })()"
            )
            await page.mouse.click(box["x"], box["y"])
            await page.wait_for_function("!!document.getElementById('__octowright_macro_modal__')", timeout=2000)
            await page.keyboard.up("Alt")

        # 1) X button dismisses.
        await _open_modal()
        modal_text = await page.evaluate("document.getElementById('__octowright_macro_modal__').textContent")
        assert "click name=Sign in" in modal_text
        assert "starting" in modal_text
        assert "done" in modal_text
        # Click the close button (the only <button> inside the modal).
        await page.evaluate("document.querySelector('#__octowright_macro_modal__ button').click()")
        await page.wait_for_function("!document.getElementById('__octowright_macro_modal__')", timeout=2000)

        # 2) Backdrop click dismisses (click on the overlay outside the card).
        await _open_modal()
        # Click at top-left of the viewport — outside the centered card.
        await page.mouse.click(5, 5)
        await page.wait_for_function("!document.getElementById('__octowright_macro_modal__')", timeout=2000)
    finally:
        await pool.shutdown()
        _defaults.RECORDINGS_DIR = original
        _pool.RECORDINGS_DIR = original_pool


@pytest.mark.asyncio
async def test_macro_slowmo_delays_dispatch(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """slowmo_ms must add a real per-action delay before dispatch.

    Asserted by RECORDING the delay the runtime asks for, not by timing two
    real browser runs against each other. The wall-clock version compared
    `slow_elapsed >= baseline_elapsed + 0.30`, using a single-sample baseline as
    its reference -- but the signal is 600ms while the baseline varies by
    seconds under CI contention, so it inverted and failed (observed on windows
    amd64: `baseline=2.750s slow=0.610s`). Widening the tolerance only moves the
    failure rate around; the measurement itself was the problem.

    `execution.py` awaits `asyncio.sleep` in exactly ONE place -- the slowmo call
    in `_dispatch_one` -- so recording it gives an exact assertion with no noise,
    and one strictly stronger than the timing version: it pins the per-action
    count and the delay value, not merely that some time passed.
    """
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright.browser_pool import BrowserPool
    from octowright.macros import execution as _execution
    from octowright.macros.execution import run_macro
    from octowright.macros.storage import MACROS_DIR

    class _RecordingAsyncio:
        """Stands in for `execution`'s own `asyncio` name, recording sleeps.

        Rebinding the module-level name affects ONLY execution.py. Patching
        `asyncio.sleep` itself would swap it process-wide, under a live
        Playwright browser -- the same trap that made a guard in
        tests/conftest.py accuse three innocent tests.
        """

        def __init__(self, real: Any) -> None:
            self._real = real
            self.sleeps: list[float] = []

        def __getattr__(self, name: str) -> Any:
            return getattr(self._real, name)

        async def sleep(self, delay: float, *args: Any, **kwargs: Any) -> Any:
            self.sleeps.append(delay)
            return await self._real.sleep(0)

    recorder = _RecordingAsyncio(_execution.asyncio)
    monkeypatch.setattr(_execution, "asyncio", recorder)

    monkey_recordings = tmp_path / "rec"
    monkey_recordings.mkdir()
    original = _defaults.RECORDINGS_DIR
    _defaults.RECORDINGS_DIR = monkey_recordings
    import octowright.browser_pool.pool as _pool

    original_pool = _pool.RECORDINGS_DIR
    _pool.RECORDINGS_DIR = monkey_recordings

    # Write a tiny 3-action macro to the macros dir so run_macro can load it.
    import json as _json

    macro_path = MACROS_DIR / "slowmo-probe.json"
    MACROS_DIR.mkdir(parents=True, exist_ok=True)
    macro_path.write_text(
        _json.dumps(
            {
                "name": "slowmo-probe",
                "description": "tiny 3-action macro",
                "parameters": [],
                "created_at": "2026-05-07T00:00:00Z",
                "updated_at": "2026-05-07T00:00:00Z",
                "actions": [
                    {"action": "evaluate", "expression": "1+1"},
                    {"action": "evaluate", "expression": "2+2"},
                    {"action": "evaluate", "expression": "3+3"},
                ],
            }
        )
    )

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body></body></html>",
            headed=False,
            label="slowmo",
            viewport_w=400,
            viewport_h=300,
        )
        session = pool.get(result["instance_id"])

        # Baseline — no slowmo. The runtime must not ask to sleep at all.
        recorder.sleeps.clear()
        baseline = await run_macro(session, "slowmo-probe")
        assert baseline["slowmo_ms"] == 0
        assert baseline["executed"] == 3
        assert recorder.sleeps == [], f"slowmo is off, so no delay should be requested; got {recorder.sleeps}"

        # Slowmo: one 200ms delay per action, three actions.
        recorder.sleeps.clear()
        slow = await run_macro(session, "slowmo-probe", slowmo_ms=200)
        assert slow["slowmo_ms"] == 200
        assert slow["executed"] == 3
        assert recorder.sleeps == [0.2, 0.2, 0.2], f"expected one 0.2s delay per action; got {recorder.sleeps}"
    finally:
        try:
            macro_path.unlink()
        except FileNotFoundError:
            pass
        await pool.shutdown()
        _defaults.RECORDINGS_DIR = original
        _pool.RECORDINGS_DIR = original_pool
