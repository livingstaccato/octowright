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

_SAFE_AREA_EDGES = ("top", "right", "bottom", "left")


def _validate_edge_insets(insets: dict[str, int]) -> None:
    for edge, value in insets.items():
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"safe-area inset {edge} must be a non-negative integer, got {value!r}")


class SessionEmulationMixin(SessionLike):
    @gated_operation("browser_scroll")
    async def scroll(self, *, delta_x: int, delta_y: int, selector: str | None = None) -> dict[str, Any]:
        """Deliver one mouse-wheel delta, optionally after bringing ``selector``
        into view. Returns current post-scroll window metrics."""
        for name, value in (("delta_x", delta_x), ("delta_y", delta_y)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} must be an integer, got {value!r}")
        if selector is not None:
            if not selector.strip():
                raise ValueError("selector must not be empty when provided")
            locator = self.page.locator(selector)
            await bounded(locator.scroll_into_view_if_needed(), operation="browser_scroll")
        await bounded(self.page.mouse.wheel(delta_x, delta_y), operation="browser_scroll")
        self.recorder.record("scroll", delta_x=delta_x, delta_y=delta_y, selector=selector)
        metrics = await bounded(self.page.evaluate(_SCROLL_METRICS), operation="browser_scroll")
        return {"ok": True, "delta_x": delta_x, "delta_y": delta_y, **metrics}

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
