# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every DevTools message the animation freeze sends, with its parameters, over a fake session.

`test_animation_freeze.py` pins the order of the protocol; this pins its exact
content, what a malformed reply does, which failures are survived and logged,
and that the elapsed time handed to the repair is in milliseconds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from octowright.macros import animation_freeze
from octowright.macros.animation_freeze import OBJECT_GROUP, PENDING_JS, REPAIR_JS, STILL_PENDING_JS, AnimationFreeze
from octowright.session.timeouts import SessionCallTimeoutError

_NOTED = "pending-animations"
_NO_PARAMS = "<no params>"


class FakeCDP:
    """Records ``(method, params)``; replies are scripted per step, and a step may fail or hang."""

    def __init__(
        self,
        *,
        note: dict[str, Any] | None = None,
        polls: list[dict[str, Any]] | None = None,
        fail: frozenset[str] = frozenset(),
        hang: str | None = None,
    ) -> None:
        self.note = {"result": {"type": "object", "objectId": _NOTED}} if note is None else note
        self.polls = list(polls or [])
        self.fail = fail
        self.hang = hang
        self.sent: list[tuple[str, Any]] = []

    def _step(self, method: str, params: Any) -> str:
        if method != "Runtime.callFunctionOn":
            return method if method != "Animation.setPlaybackRate" else f"rate:{params['playbackRate']}"
        return {PENDING_JS: "note", STILL_PENDING_JS: "poll", REPAIR_JS: "repair"}[params["functionDeclaration"]]

    async def send(self, method: str, *rest: Any) -> dict[str, Any]:
        params = rest[0] if rest else _NO_PARAMS
        self.sent.append((method, params))
        step = self._step(method, params)
        if self.hang is not None and step == self.hang:
            await asyncio.Event().wait()
        if step in self.fail:
            raise RuntimeError(f"{step} failed")
        if method == "Runtime.evaluate":
            return {"result": {"type": "object", "objectId": "document"}}
        if step == "note":
            return self.note
        if step == "poll":
            return self.polls.pop(0) if self.polls else {"result": {"type": "boolean", "value": False}}
        if step == "repair":
            return {"result": {"type": "number", "value": 2}}
        return {}

    def steps(self) -> list[str]:
        return [self._step(method, params) for method, params in self.sent]


def _call(function: str, object_id: str, *, by_value: bool = True, arguments: list[Any] | None = None) -> tuple:
    params: dict[str, Any] = {
        "objectId": object_id,
        "functionDeclaration": function,
        "returnByValue": by_value,
        "objectGroup": OBJECT_GROUP,
    }
    if arguments is not None:
        params["arguments"] = [{"value": argument} for argument in arguments]
    return ("Runtime.callFunctionOn", params)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[float]]:
    """A monotonic clock the freeze reads from, advanced by the test; asyncio keeps the real one."""
    now = [100.0]
    monkeypatch.setattr(animation_freeze, "time", SimpleNamespace(monotonic=lambda: now[0]))
    yield now


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(animation_freeze, "_SETTLE_POLL_SECONDS", 0)


async def test_pause_and_resume_send_exactly_these_messages(clock: list[float]) -> None:
    cdp = FakeCDP(polls=[{"result": {"value": True}}, {"result": {"value": False}}])
    freeze = AnimationFreeze(cdp)

    await freeze.pause()
    clock[0] = 100.25
    await freeze.resume()

    assert cdp.sent == [
        ("Animation.enable", _NO_PARAMS),
        ("Runtime.evaluate", {"expression": "document", "objectGroup": OBJECT_GROUP}),
        _call(PENDING_JS, "document", by_value=False),
        ("Animation.setPlaybackRate", {"playbackRate": 0}),
        _call(STILL_PENDING_JS, _NOTED),
        _call(STILL_PENDING_JS, _NOTED),
        # No time passed on the fake clock between the note and the first repair.
        _call(REPAIR_JS, _NOTED, arguments=[0.0, 1.0]),
        _call(STILL_PENDING_JS, _NOTED),
        # 0.25 s after the note, in milliseconds.
        _call(REPAIR_JS, _NOTED, arguments=[250.0, 1.0]),
        ("Animation.setPlaybackRate", {"playbackRate": 1}),
        ("Animation.disable", _NO_PARAMS),
        ("Runtime.releaseObjectGroup", {"objectGroup": OBJECT_GROUP}),
    ]


async def test_resume_without_a_pause_only_restarts_the_timeline() -> None:
    cdp = FakeCDP()

    await AnimationFreeze(cdp).resume()

    assert cdp.steps() == ["rate:1", "Animation.disable"]


async def test_a_second_resume_neither_repairs_nor_releases_again() -> None:
    cdp = FakeCDP()
    freeze = AnimationFreeze(cdp)
    await freeze.pause()
    await freeze.resume()
    sent = len(cdp.sent)

    await freeze.resume()

    assert cdp.steps()[sent:] == ["rate:1", "Animation.disable"]


@pytest.mark.parametrize(
    "note",
    [
        {},
        {"result": {"type": "object", "subtype": "null", "value": None}},
        # Only an object is a set of noted animations, whatever else the reply carries.
        {"result": {"type": "function", "objectId": "not-a-list"}},
    ],
    ids=["no-result", "null", "not-an-object"],
)
async def test_a_note_that_is_not_an_object_notes_nothing_and_warns_of_nothing(note: dict[str, Any]) -> None:
    cdp = FakeCDP(note=note)
    freeze = AnimationFreeze(cdp)

    with structlog.testing.capture_logs() as logs:
        await freeze.pause()
        await freeze.resume()

    assert cdp.steps() == [
        "Animation.enable",
        "Runtime.evaluate",
        "note",
        "rate:0",
        "rate:1",
        "Animation.disable",
        "Runtime.releaseObjectGroup",
    ]
    assert logs == []


async def test_a_poll_reply_without_a_result_ends_the_wait_and_still_repairs() -> None:
    cdp = FakeCDP(polls=[{}])
    freeze = AnimationFreeze(cdp)

    with structlog.testing.capture_logs() as logs:
        await freeze.pause()

    assert cdp.steps()[-2:] == ["poll", "repair"]
    assert logs == []


async def test_a_failed_note_is_logged_and_the_timeline_still_stops() -> None:
    cdp = FakeCDP(fail=frozenset({"note"}))

    with structlog.testing.capture_logs() as logs:
        await AnimationFreeze(cdp).pause()

    assert cdp.steps()[-1] == "rate:0"
    assert logs == [
        {
            "event": "octowright.macro.redacted_screenshot.animations_not_noted",
            "error": "RuntimeError",
            "log_level": "warning",
        }
    ]


async def test_a_failed_repair_is_logged() -> None:
    cdp = FakeCDP(fail=frozenset({"repair"}))

    with structlog.testing.capture_logs() as logs:
        await AnimationFreeze(cdp).pause()

    assert logs == [
        {
            "event": "octowright.macro.redacted_screenshot.animations_not_repaired",
            "error": "RuntimeError",
            "log_level": "warning",
        }
    ]


@pytest.mark.parametrize("failing", ["rate:1", "Animation.disable", "Runtime.releaseObjectGroup"])
async def test_each_resume_step_is_attempted_whichever_fails(failing: str) -> None:
    cdp = FakeCDP(fail=frozenset({failing}))
    freeze = AnimationFreeze(cdp)
    await freeze.pause()
    sent = len(cdp.sent)

    await freeze.resume()

    assert cdp.steps()[sent:] == ["poll", "repair", "rate:1", "Animation.disable", "Runtime.releaseObjectGroup"]


@pytest.mark.parametrize("hang", ["Animation.enable", "rate:0"])
async def test_a_pause_step_that_never_answers_is_named_by_the_operation(
    hang: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS", "0.05")
    cdp = FakeCDP(hang=hang)

    with pytest.raises(SessionCallTimeoutError) as raised:
        await AnimationFreeze(cdp).pause()

    assert str(raised.value).startswith("macro_redacted_screenshot did not answer within 0.05s")
