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


#: Recorded user actions with no entry in the replay map. Unlike the passive
#: events above, these should probably become REPLAYABLE rather than stripped --
#: octowright exposes browser_switch_frame and browser_get_text_by as tools, so a
#: recording that used them describes real intent. Deciding that is a feature
#: change, not a strip-list edit, so they are pinned here as a known gap: the
#: test still fails on any NEW unclassified event.
KNOWN_UNCLASSIFIED = {"switch_frame", "get_text_by"}


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
