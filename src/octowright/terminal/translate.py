# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Translate uterm connector worker-protocol messages into Octowright Recorder
actions (`{ts, action, ...fields}`).

The PTY/SSH connectors emit `snapshot` messages whose `screen` is the *cumulative*
decoded output buffer (capped ~32KB), not per-chunk bytes. `MessageTranslator`
holds the previous screen and emits only the delta as `terminal_output.data`, so
the recording is a clean append-only stream that reconstructs the full screen by
concatenation (and feeds xterm.js incrementally in a later phase).
"""

from __future__ import annotations

from typing import Any


class MessageTranslator:
    """Stateful per-session translator. Not thread-safe; drive from one loop."""

    def __init__(self) -> None:
        self._last_screen = ""

    def feed(self, msg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        mtype = msg.get("type")
        if mtype == "snapshot":
            screen = str(msg.get("screen", ""))
            delta = self._delta(self._last_screen, screen)
            self._last_screen = screen
            if not delta:
                return []
            fields: dict[str, Any] = {"data": delta}
            if msg.get("cursor") is not None:
                fields["cursor"] = msg["cursor"]
            if msg.get("screen_hash") is not None:
                fields["screen_hash"] = msg["screen_hash"]
            return [("terminal_output", fields)]
        if mtype == "error":
            return [("terminal_error", {"message": str(msg.get("message", ""))})]
        # worker_hello / hello / any unmapped type: pass through, never drop.
        payload = {k: v for k, v in msg.items() if k != "type"}
        return [("terminal_event", {"uterm_type": mtype, **payload})]

    @staticmethod
    def _delta(prev: str, cur: str) -> str:
        if not prev:
            return cur
        if cur.startswith(prev):
            return cur[len(prev) :]
        # Cap slid (output exceeded ~32KB) or buffer was cleared: the delta is
        # the new buffer past the longest suffix-of-prev that prefixes cur.
        max_overlap = min(len(prev), len(cur))
        for k in range(max_overlap, 0, -1):
            if prev[-k:] == cur[:k]:
                return cur[k:]
        return cur
