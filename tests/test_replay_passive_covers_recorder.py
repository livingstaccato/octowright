# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Passive recorder events must be stripped, not replayed as failing steps.

A macro built from a raw recording carries whatever the page emitted on its own.
Those are observations, not user actions: replaying one is meaningless, and
counting it is worse -- ``dispatch_simple`` tallies an unknown action as an
error, so a socket-backed page turned every frame it received into a "failed"
step.

This drifted for real. Sockets are recorded as ``websocket_{direction}`` for
Playwright's own ``framesent`` / ``framereceived``, but the strip-lists still
named ``websocket_inbound`` / ``websocket_outbound`` -- an older vocabulary with
no emitter left. So the live names fell straight through. One captured macro
library carried 608 of them, every replay reporting 608 bogus errors, which is
the kind of noise that makes a suite unreadable and then ignored.

There were also two hand-maintained copies of the list, in runtime and in
recording_import, which is how they disagreed with the recorder AND with each
other. One is now derived from the other.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import macros
from octowright.macros.recording_import import RECORDER_NOISE
from octowright.macros.runtime import _ACTION_MAP, _REPLAY_PASSIVE, _REPLAY_SKIP

SRC = Path(__file__).parents[1] / "src" / "octowright"

#: Recorder calls whose event name is built at runtime, with the values the
#: surrounding code can pass. A static scan cannot resolve an f-string, and
#: leaving these unchecked is exactly how the frame events were missed.
DYNAMIC_RECORDER_EVENTS = {"websocket_framesent", "websocket_framereceived"}


def _statically_recorded_events() -> set[str]:
    """Every literal event name passed to ``recorder.record``."""
    found: set[str] = set()
    for path in (SRC / "session").rglob("*.py"):
        found.update(re.findall(r'recorder\.record\(\s*"([a-z_]+)"', path.read_text()))
    return found


#: Nothing. switch_frame and get_text_by used to sit here as a known gap; they
#: are replayable now, which is what they always should have been.
KNOWN_UNCLASSIFIED: set[str] = set()


def test_every_recorded_event_is_either_replayable_or_stripped() -> None:
    """The invariant that was missing: nothing the recorder emits may be unknown.

    dispatch_simple errors on a kind that is in none of _ACTION_MAP,
    _REPLAY_SKIP or _REPLAY_PASSIVE, so an unclassified event is a counted
    failure the moment it appears in a recording.
    """
    recorded = _statically_recorded_events() | DYNAMIC_RECORDER_EVENTS
    assert recorded, "found no recorder.record calls — the scan broke, not the code"

    unclassified = {
        event
        for event in recorded
        if event not in _ACTION_MAP and event not in _REPLAY_PASSIVE and event not in _REPLAY_SKIP
    }
    assert unclassified <= KNOWN_UNCLASSIFIED, (
        "these recorder events are neither replayable nor stripped, so a recording "
        f"containing one tallies it as a failed step: {sorted(unclassified - KNOWN_UNCLASSIFIED)}"
    )


def test_the_websocket_frame_events_are_stripped() -> None:
    """The specific regression: 608 frames, 608 bogus errors."""
    assert DYNAMIC_RECORDER_EVENTS <= _REPLAY_PASSIVE
    assert DYNAMIC_RECORDER_EVENTS <= RECORDER_NOISE, "frames would be SAVED into the macro too"


def test_recorder_noise_is_derived_from_the_replay_list() -> None:
    """One definition. Two hand-kept copies is how the vocabularies diverged."""
    assert {"user_navigation"} == RECORDER_NOISE - _REPLAY_PASSIVE
    assert _REPLAY_PASSIVE < RECORDER_NOISE


@pytest.mark.anyio
async def test_switch_frame_replays_without_its_observed_landing() -> None:
    """The selector chose the frame; index/frame_url/frame_name describe where it landed.

    Passing those back would be a TypeError on a method that only takes
    selector/name/url_pattern -- and replay must re-resolve the frame from the
    live page anyway, since an index recorded yesterday means nothing today.
    """
    session = MagicMock()
    session.switch_frame = AsyncMock()
    session.diagnostic_bundle = AsyncMock(return_value={})

    executed, errors = await macros._dispatch_simple(
        session,
        {
            "action": "switch_frame",
            "selector": "#checkout-iframe",
            "name": None,
            "url_pattern": None,
            "index": 2,
            "frame_url": "https://provider.test/pay",
            "frame_name": "pay",
        },
    )

    assert (executed, errors) == (1, 0)
    session.switch_frame.assert_awaited_once_with(selector="#checkout-iframe", name=None, url_pattern=None)


@pytest.mark.anyio
async def test_get_text_by_replays_without_the_text_it_read() -> None:
    """`result` is the observation, and dropping it matters more here than elsewhere.

    get_text_by takes **finders, so a stray `result` would not raise as an
    unexpected kwarg -- it would be handed to the locator builder as though it
    were a finder, and match nothing.
    """
    session = MagicMock()
    session.get_text_by = AsyncMock()
    session.diagnostic_bundle = AsyncMock(return_value={})

    executed, errors = await macros._dispatch_simple(
        session,
        {"action": "get_text_by", "test_id": "order-total", "result": "$49.00"},
    )

    assert (executed, errors) == (1, 0)
    session.get_text_by.assert_awaited_once_with(test_id="order-total")
