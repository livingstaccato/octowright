# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ALWAYS_STRIP = {"close", "snapshot"}
LIFECYCLE = {"launch"}

# Passive recorder events: observations and cache/lifecycle notices that are not
# replayable actions. Saving them into a macro only bloats it and inflates replay
# (e.g. a `user_navigation` to the internal new-tab landing page, or `markdown_cached`
# entries, are pure noise). Mirrors macros.runtime._REPLAY_PASSIVE plus the
# user-driven navigation artifact, which has no entry in the replay action map.
RECORDER_NOISE = {
    "user_navigation",
    "console",
    "popup_opened",
    "websocket_opened",
    "websocket_closed",
    "websocket_inbound",
    "websocket_outbound",
    "markdown_cached",
    "markdown_cache_error",
    "websocket_cache_error",
    "trace_stop_error",
    "dialog_handler_error",
}


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
