# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Viewport sizing: resize, status, and sync.

Split out of ``core_ops_mixin`` — it was within a dozen lines of the 550-LOC
ceiling, and these three belong together anyway: they are the only ops that
reason about the relationship between the page's viewport and the OS window
around it.
"""

from __future__ import annotations

from typing import Any

from provide.telemetry import get_logger

from octowright.session._protocols import SessionLike
from octowright.session.operation.gate import gated_operation
from octowright.session.timeouts import bounded

log = get_logger(__name__)

__all__ = ["VIEWPORT_ROUNDING_SLACK", "SessionViewportMixin"]

# CSS pixels of slack allowed when comparing the viewport against the window's
# content area. Absorbs rounding under a fractional devicePixelRatio, where
# innerWidth/outerWidth are reported as rounded CSS pixels. Anything larger is
# real drift and worth a warning. Keep this small and keep it about rounding —
# widening it to cover browser chrome is the bug this replaced.
#
# Mirrored as ROUNDING_SLACK in browser_pool/_assets/viewport_pill.js, which
# renders the same verdict in-page; change both together.
VIEWPORT_ROUNDING_SLACK = 2

_MEASURE = """() => ({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    devicePixelRatio: window.devicePixelRatio
})"""


class SessionViewportMixin(SessionLike):
    viewport_mode: str
    viewport_width: int | None
    viewport_height: int | None
    viewport_frame_inset_w: int | None
    viewport_frame_inset_h: int | None

    @gated_operation("browser_measure_frame_inset")
    async def measure_frame_inset(self, page: Any = None) -> None:
        """Record the browser chrome around the content area.

        Valid at exactly one kind of moment: just after Playwright has WELDED
        the OS window to the viewport, which it does at launch and again on
        every ``set_viewport_size``. At that instant ``outer - inner`` is the
        chrome and nothing else. At any other time the same difference may also
        carry drift -- a tiling WM, a maximise the emulated viewport did not
        follow -- which is precisely what ``viewport_status`` reports by
        subtracting this baseline, and would hide if it re-measured then.

        Best-effort by design: a failure leaves the inset None, and every
        consumer treats None as "cannot see the window" and declines to warn.
        Neither launching a browser nor resizing one may fail over a diagnostic
        measurement.
        """
        target = self.page if page is None else page
        try:
            measured = await bounded(
                target.evaluate(
                    """() => ({
                        dw: window.outerWidth - window.innerWidth,
                        dh: window.outerHeight - window.innerHeight
                    })"""
                ),
                operation="browser_measure_frame_inset",
            )
            inset_w = int(measured["dw"])
            inset_h = int(measured["dh"])
        except Exception as exc:
            log.debug("octowright.viewport.frame_inset_unavailable", error=repr(exc))
            return
        # Headless reports outer == inner (no window, no chrome), which is a
        # true zero inset rather than a failed measurement. Negative is
        # nonsense -- a window cannot be smaller than its own content area --
        # so treat it as unmeasured rather than storing a number that would
        # overstate the content area and invent a mismatch.
        if inset_w < 0 or inset_h < 0:
            log.debug("octowright.viewport.frame_inset_negative", width=inset_w, height=inset_h)
            return
        self.viewport_frame_inset_w = inset_w
        self.viewport_frame_inset_h = inset_h

    @gated_operation("browser_resize")
    async def resize(self, width: int, height: int) -> dict[str, Any]:
        await self.page.set_viewport_size({"width": width, "height": height})
        # Re-measure: Playwright has just re-welded the window to the new
        # viewport, so the chrome is measurable again -- and it may genuinely
        # have CHANGED. A fluid session reports the real layout viewport, which
        # excludes the classic scrollbar; a fixed one is emulated and does not.
        # Measured on the same browser: 24x112 fluid against 8x85 fixed.
        # Keeping the launch figure across the mode change would understate the
        # content area by the difference and report a mismatch that is not
        # there -- the exact false positive this check was rewritten to remove.
        await self.measure_frame_inset()
        # Record the new size as the session's own. Leaving it stale meant
        # viewport_status reported a `configured` the page had not had since
        # the resize, and the in-page pill announced a size the page was not.
        #
        # The mode moves to fixed because that is what happened: set_viewport_size
        # pins the viewport, and a pinned viewport no longer follows its window.
        # Saying "fluid" afterwards was not merely a wrong label -- `mismatch`
        # only evaluates for fixed sessions, so a resized fluid session had
        # drift detection silently switched off with nothing to say so.
        self.viewport_mode = "fixed"
        self.viewport_width = width
        self.viewport_height = height
        self.recorder.record("resize", width=width, height=height)
        return {"ok": True, "mode": "fixed", "width": width, "height": height}

    def _content_area(self, outer: dict[str, int]) -> dict[str, int] | None:
        """The window's content area: the outer window less the browser chrome.

        This is the size the page WOULD fill if the viewport followed the
        window. Comparing it against the actual viewport is what makes a drift
        warning possible; comparing the outer window against the viewport only
        ever measures the chrome, which is constant.

        None when the chrome inset was never measured (see
        ``BrowserSession.viewport_frame_inset_w``) or when the window reports
        no size at all, as headless does.
        """
        inset_w = self.viewport_frame_inset_w
        inset_h = self.viewport_frame_inset_h
        if inset_w is None or inset_h is None:
            return None
        if outer["width"] <= 0 or outer["height"] <= 0:
            return None
        return {
            "width": max(0, outer["width"] - inset_w),
            "height": max(0, outer["height"] - inset_h),
        }

    @gated_operation("browser_viewport_status")
    async def viewport_status(self) -> dict[str, Any]:
        measured = await bounded(self.page.evaluate(_MEASURE), operation="browser_viewport_status")
        page = {
            "width": int(measured.get("innerWidth") or 0),
            "height": int(measured.get("innerHeight") or 0),
        }
        outer = {
            "width": int(measured.get("outerWidth") or 0),
            "height": int(measured.get("outerHeight") or 0),
        }
        content = self._content_area(outer)
        # ``mismatch`` answers one question: is the page rendering at a
        # different size from the window that contains it, so that a screenshot
        # is not what a human at this window would see?
        #
        # It therefore compares the viewport against the CONTENT AREA. It used
        # to compare the viewport against the OUTER window with a 24x80px
        # allowance for chrome, which was unsound twice over: the difference it
        # measured was the chrome itself rather than any drift, and the chrome
        # on Linux/Wayland chromium is ~85px tall, over the bar. The badge read
        # "fixed mismatch" on every headed fixed-viewport session from the
        # moment it launched, so the real signal was unreachable — a genuine
        # drift looked exactly like the permanent false positive.
        mismatch = (
            self.viewport_mode == "fixed"
            and content is not None
            and page["width"] > 0
            and page["height"] > 0
            and (
                abs(content["width"] - page["width"]) > VIEWPORT_ROUNDING_SLACK
                or abs(content["height"] - page["height"]) > VIEWPORT_ROUNDING_SLACK
            )
        )
        return {
            "mode": self.viewport_mode,
            "fixed": self.viewport_mode == "fixed",
            "fluid": self.viewport_mode == "fluid",
            "configured": {"width": self.viewport_width, "height": self.viewport_height},
            "page": page,
            "outer": outer,
            "content": content,
            "frame_inset": {"width": self.viewport_frame_inset_w, "height": self.viewport_frame_inset_h},
            "device_pixel_ratio": measured.get("devicePixelRatio"),
            "mismatch": mismatch,
        }

    @gated_operation("browser_viewport_sync")
    async def viewport_sync(self) -> dict[str, Any]:
        status = await self.viewport_status()
        # Target the CONTENT AREA, never the outer window. Sizing the viewport
        # to the outer window makes Playwright grow the window to fit it — it
        # welds the window to the viewport — so the next measurement is a whole
        # chrome larger, and the one after that larger again. Measured on
        # Linux/Wayland chromium from 1000x700: 1008x785, 1016x870, 1024x955,
        # 1032x1040. Sync never converged; each call added 85px of height for
        # as long as anyone kept pressing the button.
        #
        # Against the content area it converges in one step and is idempotent
        # thereafter, because the content area is what the viewport becomes.
        # With no measured chrome inset there is nothing to sync to, so fall
        # back to the viewport's own size — a no-op, which is the right answer
        # when we cannot see the window.
        target = status["content"] or status["page"]
        width = int(target["width"] or status["page"]["width"])
        height = int(target["height"] or status["page"]["height"])
        if width <= 0 or height <= 0:
            raise ValueError("unable to measure a usable viewport size")
        await self.page.set_viewport_size({"width": width, "height": height})
        # Same reason as resize(): the window has just been re-welded, which is
        # both when the chrome is measurable and when it may have changed.
        await self.measure_frame_inset()
        self.viewport_mode = "fixed"
        self.viewport_width = width
        self.viewport_height = height
        self.recorder.record("resize", width=width, height=height)
        return {"ok": True, "mode": "fixed", "width": width, "height": height}
