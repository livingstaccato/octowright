# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Hold a Chromium page's animations still for a redacted screenshot, and let them carry on after.

The page's document timeline is stopped with ``Animation.setPlaybackRate(0)``, so the scans
and the capture see one frame, including animations that start during the capture.

That alone breaks an animation that is still pending when the timeline stops, which is
every animation on a page screenshotted the moment it loaded. Chrome resolves the start
time of a pending composited animation when the compositor reports it started, and
under a stopped timeline it converts that report against the stopped clock: the start
time lands at about the host's uptime in milliseconds, hours or days ahead of the
timeline (measured on Chrome 153, Linux and macOS). The frame then jumps from the
animation's first keyframe to the unanimated style partway through the capture, and
after the timeline resumes the animation sits in its before phase until that time
comes round.

So the animations that are pending when the timeline stops are noted first, with the
time each one shows while pending. Once they have resolved, one whose time moved further
than its playback rate allows for the time that passed is set back to the time it showed,
which is what a start resolved at the stopped instant gives it. That runs once after
the timeline stops, before anything is scanned, and again just before it resumes, for
one that resolved later.

Limits: an animation that becomes pending between the note and the stop is not noted,
and one still pending when the bounded wait ends can resolve after the repair. Both
windows are one DevTools round trip or the wait's bound. The repair writes
``currentTime`` from the page's own world, so page script that replaced the Web
Animations accessors only affects its own animations.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from provide.telemetry import get_logger

from octowright.session.timeouts import bounded

log = get_logger(__name__)

OBJECT_GROUP = "octowright-animation-freeze"
_OPERATION = "macro_redacted_screenshot"
# How long to wait for noted animations to resolve, polled from here rather than with page timers.
_SETTLE_POLLS = 20
_SETTLE_POLL_SECONDS = 0.025
# Slack for float rounding when comparing an animation's time with the one it showed.
_TOLERANCE_MS = 1.0

PENDING_JS = """function() {
  const animations = document.getAnimations().filter(
    (a) => a.pending && a.playState === "running" && a.currentTime !== null);
  return animations.length ? {animations, times: animations.map((a) => a.currentTime)} : null;
}"""
STILL_PENDING_JS = "function() { return this.animations.some((a) => a.pending); }"
REPAIR_JS = """function(elapsed, tolerance) {
  let repaired = 0;
  this.animations.forEach((a, index) => {
    if (a.pending || a.currentTime === null) return;
    const shown = this.times[index];
    if (Math.abs(a.currentTime - shown) > Math.abs(a.playbackRate) * elapsed + tolerance) {
      a.currentTime = shown;
      repaired += 1;
    }
  });
  return repaired;
}"""


class AnimationFreeze:
    """Stops the document timeline and repairs what stopping it does to pending animations."""

    def __init__(self, cdp: Any) -> None:
        self._cdp = cdp
        self._pending: str | None = None
        self._noted = False
        # Equivalent under mutation: pause() sets it before anything can read it.
        self._noted_at = 0.0  # pragma: no mutate

    async def pause(self) -> None:
        """Note the pending animations, stop the timeline, then repair the noted ones once resolved."""
        await bounded(self._cdp.send("Animation.enable"), operation=_OPERATION)
        self._noted_at = time.monotonic()
        self._noted = True
        try:
            self._pending = await bounded(self._note_pending(), operation=_OPERATION)
        except Exception as exc:
            log.warning("octowright.macro.redacted_screenshot.animations_not_noted", error=type(exc).__name__)
        await bounded(self._cdp.send("Animation.setPlaybackRate", {"playbackRate": 0}), operation=_OPERATION)
        await self._settle()

    async def resume(self) -> None:
        """Repair any noted animation that resolved since, restart the timeline and release; each step is attempted."""
        await self._settle()
        for method, params in (("Animation.setPlaybackRate", {"playbackRate": 1}), ("Animation.disable", None)):
            with contextlib.suppress(Exception):
                await bounded(
                    self._cdp.send(method, params) if params else self._cdp.send(method), operation=_OPERATION
                )
        self._pending = None
        if self._noted:
            self._noted = False
            with contextlib.suppress(Exception):
                await bounded(
                    self._cdp.send("Runtime.releaseObjectGroup", {"objectGroup": OBJECT_GROUP}), operation=_OPERATION
                )

    async def _note_pending(self) -> str | None:
        document = await self._cdp.send("Runtime.evaluate", {"expression": "document", "objectGroup": OBJECT_GROUP})
        reply = await self._call(document["result"]["objectId"], PENDING_JS, by_value=False)
        result = reply.get("result", {})
        return result.get("objectId") if result.get("type") == "object" else None

    async def _call(
        self, object_id: str, function: str, arguments: list[Any] | None = None, *, by_value: bool = True
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "objectId": object_id,
            "functionDeclaration": function,
            "returnByValue": by_value,
            "objectGroup": OBJECT_GROUP,
        }
        if arguments is not None:
            params["arguments"] = [{"value": argument} for argument in arguments]
        reply: dict[str, Any] = await self._cdp.send("Runtime.callFunctionOn", params)
        return reply

    async def _settle(self) -> None:
        """Wait, bounded, for the noted animations to resolve, then set back any that jumped."""
        pending = self._pending
        if pending is None:
            return
        try:
            for _ in range(_SETTLE_POLLS):
                reply = await bounded(self._call(pending, STILL_PENDING_JS), operation=_OPERATION)
                if reply.get("result", {}).get("value") is not True:
                    break
                await asyncio.sleep(_SETTLE_POLL_SECONDS)
            elapsed_ms = (time.monotonic() - self._noted_at) * 1000
            reply = await bounded(self._call(pending, REPAIR_JS, [elapsed_ms, _TOLERANCE_MS]), operation=_OPERATION)
        except Exception as exc:
            log.warning("octowright.macro.redacted_screenshot.animations_not_repaired", error=type(exc).__name__)
            return
        repaired = reply.get("result", {}).get("value")
        if repaired:
            log.debug("octowright.macro.redacted_screenshot.animations_repaired", count=repaired)
