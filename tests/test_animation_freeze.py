# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The order in which the animation freeze notes, stops, repairs and resumes, over a fake DevTools session.

What the repair does to a real animation is measured in
``test_macro_redacted_screenshot_leaks_live.py``; this pins the protocol around it.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros import animation_freeze
from octowright.macros.animation_freeze import AnimationFreeze

_NOTED = "pending-animations"


class FakeCDP:
    def __init__(self, *, pending: bool, still_pending: int = 0, fail: str | None = None) -> None:
        self.pending = pending
        self.still_pending = still_pending
        self.fail = fail
        self.calls: list[str] = []
        self.repair_arguments: list[Any] = []

    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if method == "Animation.setPlaybackRate":
            assert params is not None
            self.calls.append(f"rate:{params['playbackRate']}")
            return {}
        if method != "Runtime.callFunctionOn":
            self.calls.append(method)
            if method == "Runtime.evaluate":
                assert params is not None and params["objectGroup"] == animation_freeze.OBJECT_GROUP
                return {"result": {"objectId": "document"}}
            return {}
        assert params is not None and params["objectGroup"] == animation_freeze.OBJECT_GROUP
        function = params["functionDeclaration"]
        name = {
            animation_freeze.PENDING_JS: "note",
            animation_freeze.STILL_PENDING_JS: "poll",
            animation_freeze.REPAIR_JS: "repair",
        }[function]
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError("target closed")
        if name == "note":
            assert params["objectId"] == "document" and params["returnByValue"] is False
            if not self.pending:
                return {"result": {"type": "object", "subtype": "null", "value": None}}
            return {"result": {"type": "object", "objectId": _NOTED}}
        assert params["objectId"] == _NOTED and params["returnByValue"] is True
        if name == "poll":
            self.still_pending -= 1
            return {"result": {"value": self.still_pending >= 0}}
        self.repair_arguments.append([argument["value"] for argument in params["arguments"]])
        return {"result": {"value": 1}}


async def test_without_a_pending_animation_the_timeline_is_only_stopped_and_restarted() -> None:
    cdp = FakeCDP(pending=False)
    freeze = AnimationFreeze(cdp)

    await freeze.pause()
    await freeze.resume()

    assert cdp.calls == [
        "Animation.enable",
        "Runtime.evaluate",
        "note",
        "rate:0",
        "rate:1",
        "Animation.disable",
        "Runtime.releaseObjectGroup",
    ]


async def test_pending_animations_are_noted_before_the_stop_and_repaired_before_the_scans_and_the_restart() -> None:
    cdp = FakeCDP(pending=True, still_pending=1)
    freeze = AnimationFreeze(cdp)

    await freeze.pause()
    assert cdp.calls == ["Animation.enable", "Runtime.evaluate", "note", "rate:0", "poll", "poll", "repair"]
    await freeze.resume()

    assert cdp.calls[7:] == ["poll", "repair", "rate:1", "Animation.disable", "Runtime.releaseObjectGroup"]
    # The time that passed since the note bounds how far an animation may legitimately have moved.
    elapsed, tolerance = cdp.repair_arguments[0]
    assert elapsed >= 0 and tolerance == animation_freeze._TOLERANCE_MS


async def test_an_animation_that_never_resolves_is_waited_for_a_bounded_time() -> None:
    cdp = FakeCDP(pending=True, still_pending=10_000)
    freeze = AnimationFreeze(cdp)

    await freeze.pause()

    assert cdp.calls.count("poll") == animation_freeze._SETTLE_POLLS
    assert cdp.calls[-1] == "repair"


@pytest.mark.parametrize("step", ["note", "poll", "repair"])
async def test_a_failed_note_or_repair_still_stops_and_restarts_the_timeline(step: str) -> None:
    cdp = FakeCDP(pending=True, still_pending=0, fail=step)
    freeze = AnimationFreeze(cdp)

    await freeze.pause()
    await freeze.resume()

    assert cdp.calls.index("rate:0") < cdp.calls.index("rate:1")
    assert cdp.calls[-3:] == ["rate:1", "Animation.disable", "Runtime.releaseObjectGroup"]
