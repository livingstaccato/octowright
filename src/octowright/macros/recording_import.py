# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from octowright.macros.runtime import _REPLAY_PASSIVE

ALWAYS_STRIP = {"close", "snapshot"}
LIFECYCLE = {"launch"}

# Passive recorder events: observations and cache/lifecycle notices that are not
# replayable actions. Saving them into a macro only bloats it and inflates replay
# (e.g. a `user_navigation` to the internal new-tab landing page, or `markdown_cached`
# entries, are pure noise). This is macros.runtime._REPLAY_PASSIVE plus the
# user-driven navigation artifact, which has no entry in the replay action map.
# Derived, not mirrored. Both lists were maintained by hand and drifted apart
# from the recorder: sockets are recorded as websocket_framesent /
# websocket_framereceived, and neither copy had them, so every frame was saved
# into the macro AND tallied as a bogus error on replay.
RECORDER_NOISE = _REPLAY_PASSIVE | {"user_navigation"}


def iter_macro_actions(
    path: Path, *, include_launch: bool = False, strict_json: bool = False
) -> Iterator[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            if strict_json:
                raise
            continue
        action_type = entry.get("action", "")
        if action_type in ALWAYS_STRIP or action_type in RECORDER_NOISE:
            continue
        if action_type in LIFECYCLE and not include_launch:
            continue
        yield entry


def load_macro_from_recording(path: Path, include_launch: bool = False) -> list[dict[str, Any]]:
    return list(iter_macro_actions(path, include_launch=include_launch))
