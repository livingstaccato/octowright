# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Viewport/input emulation: physical mouse wheel and phone safe-area insets.

These are session operations rather than page-script shims so callers can
replay them through the recorder, just like click/fill/resize: a wheel remains
a native input event, optional selector positioning uses only Playwright's
locator API, and safe-area emulation uses Chromium's CDP command with an
explicit error on other engines rather than a no-op in-page workaround.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import gated_operation
from octowright.session.timeouts import bounded

__all__ = ["SessionEmulationMixin"]

_SCROLL_METRICS = """() => ({
    x: window.scrollX,
    y: window.scrollY,
    height: document.documentElement.scrollHeight,
    innerHeight: window.innerHeight
})"""

# Arm before mouse.wheel: the input command acknowledges dispatch, not rendering.
# Observe the wheel's ancestor chain as well as the window so nested scrollers
# and smooth scrolling participate in stability. Main-world observation is best
# effort: the host bounds setup and waiting independently of page callbacks.
_SCROLL_SETTLE = """() => {
    let finish;
    let frame;
    let armed = false;
    let closed = false;
    const done = new Promise(resolve => { finish = resolve; });
    const complete = settled => {
        closed = true;
        window.removeEventListener('wheel', onWheel, true);
        cancelAnimationFrame(frame);
        finish(settled);
    };
    const cancel = () => complete(false);
    const onWheel = event => {
        if (closed) return;
        window.removeEventListener('wheel', onWheel, true);
        const elements = event.composedPath().filter(node => node instanceof Element);
        let previous;
        let stable = 0;
        const sample = () => {
            const current = JSON.stringify([
                window.scrollX, window.scrollY,
                ...elements.flatMap(node => [node.scrollLeft, node.scrollTop])
            ]);
            stable = current === previous ? stable + 1 : 0;
            previous = current;
            if (stable >= 2) complete(true);
            else frame = requestAnimationFrame(sample);
        };
        frame = requestAnimationFrame(sample);
    };
    const arm = () => {
        if (closed || armed) return;
        armed = true;
        window.addEventListener('wheel', onWheel, {capture: true, passive: true});
    };
    return {done, arm, cancel};
}"""

_SAFE_AREA_EDGES = ("top", "right", "bottom", "left")


def _finite_scroll_metric(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        # Integers are finite; converting a large one to float can overflow.
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def _normalize_scroll_metrics(raw: Any, *, settled: bool) -> tuple[dict[str, int | float | None], bool]:
    source = raw if isinstance(raw, Mapping) else {}
    metrics = {key: _finite_scroll_metric(source.get(key)) for key in ("x", "y", "height", "innerHeight")}
    return metrics, settled and all(value is not None for value in metrics.values())


def _validate_scroll_deltas(delta_x: int, delta_y: int) -> None:
    for name, value in (("delta_x", delta_x), ("delta_y", delta_y)):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name} must be an integer, got {value!r}")


def _validate_edge_insets(insets: dict[str, int]) -> None:
    for edge, value in insets.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"safe-area inset {edge} must be a non-negative integer, got {value!r}")


class SessionEmulationMixin(SessionLike):
    @gated_operation("browser_scroll")
    async def scroll(self, *, delta_x: int, delta_y: int, selector: str | None = None) -> dict[str, Any]:
        """Deliver one native wheel over ``selector`` or the current pointer.

        Observe rendered stability for up to one second after dispatch. ``ok``
        means input was delivered; ``settled`` reports best-effort observation.
        Window metrics can be provisional when unsettled, or null when their
        observation fails; panels remain local. Cancellation still propagates.
        """
        _validate_scroll_deltas(delta_x, delta_y)
        if selector is not None:
            if not selector.strip():
                raise ValueError("selector must not be empty when provided")
            locator = self.page.locator(selector)
            await bounded(locator.scroll_into_view_if_needed(), operation="browser_scroll")
            await bounded(locator.hover(), operation="browser_scroll")
        settling = None
        settled = True
        if delta_x or delta_y:
            # Allocation is inert: if its acknowledgement arrives after timeout
            # or cancellation, it cannot leave listeners or frame work behind.
            settling = await bounded(self.page.evaluate_handle(_SCROLL_SETTLE), operation="browser_scroll", timeout=1)
        try:
            if settling is not None:
                # Own the handle before adding a listener. cancel() permanently
                # closes it, including an arm request that executes late.
                await bounded(settling.evaluate("state => state.arm()"), operation="browser_scroll", timeout=1)
            await bounded(self.page.mouse.wheel(delta_x, delta_y), operation="browser_scroll")
            self.recorder.record("scroll", delta_x=delta_x, delta_y=delta_y, selector=selector)
            if settling is not None:
                try:
                    settled = (
                        await bounded(settling.evaluate("state => state.done"), operation="browser_scroll", timeout=1)
                    ) is True
                except Exception:
                    # Input was delivered; observation errors must not invite a retry.
                    settled = False
        finally:
            if settling is not None:
                # Cancellation must release page listeners too, even when the
                # wheel itself failed or the renderer never delivered a frame.
                try:
                    with suppress(Exception):
                        await bounded(
                            settling.evaluate("state => state.cancel()"), operation="browser_scroll", timeout=1
                        )
                finally:
                    with suppress(Exception):
                        await bounded(settling.dispose(), operation="browser_scroll", timeout=1)
        try:
            raw_metrics = await bounded(self.page.evaluate(_SCROLL_METRICS), operation="browser_scroll", timeout=1)
        except Exception:
            raw_metrics = None
        metrics, settled = _normalize_scroll_metrics(raw_metrics, settled=settled)
        return {"ok": True, "delta_x": delta_x, "delta_y": delta_y, "settled": settled, **metrics}

    @gated_operation("browser_set_safe_area")
    async def set_safe_area(self, *, top: int = 0, right: int = 0, bottom: int = 0, left: int = 0) -> dict[str, Any]:
        """Set all four CSS-pixel safe-area insets at once.

        CSS-side result: ``env(safe-area-inset-top/right/bottom/left)`` now
        matches the phone being emulated, including the bottom inset most
        clients care about for fixed bottom bars. Only Chromium understands the
        CDP command; on other engines the call fails explicitly rather than
        silently leaving the variables at zero.
        """
        insets = dict(zip(_SAFE_AREA_EDGES, (top, right, bottom, left), strict=True))
        _validate_edge_insets(insets)
        if self.kind != "chromium":
            raise ValueError("safe-area emulation is Chromium-only (CDP Emulation.setSafeAreaInsets)")
        cdp = await bounded(self.context.new_cdp_session(self.page), operation="browser_set_safe_area")
        await bounded(cdp.send("Emulation.setSafeAreaInsets", {"insets": insets}), operation="browser_set_safe_area")
        self.recorder.record("set_safe_area", **insets)
        return {"ok": True, "insets": insets}
