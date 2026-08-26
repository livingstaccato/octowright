# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Translate uterm connector worker-protocol messages into Octowright Recorder
actions (`{ts, action, ...fields}`).

The PTY/SSH connectors emit `snapshot` messages whose `screen` is the *cumulative*
decoded output buffer (capped ~32KB), not per-chunk bytes. `MessageTranslator`
holds the previous screen and emits only the delta as `terminal_output.data`, so
the recording is an append stream that reconstructs the full screen by
concatenation and feeds xterm.js incrementally.

Two non-append cases break pure concatenation, distinguished by buffer length:

* **Cap slide** — output exceeded ~32KB and the connector front-truncated the
  buffer (`buffer[-32768:]`), so `cur` stays at/above its prior length and shares
  a long suffix/prefix overlap with `prev`. The delta is just the genuinely-new
  tail; consumers keep appending (xterm keeps its scrollback).
* **Reset** — the connector's `clear()` emptied the buffer (`buffer = ""`), so
  `cur` is shorter and shares no real overlap. The delta carries `reset: True`
  and the *full* new buffer; consumers must CLEAR their display before writing
  it, otherwise the stale screen lingers above the fresh one (a program-emitted
  `\\x1b[2J` is NOT this case — it appends to the buffer and xterm executes it).
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
            reset, delta = self._delta(self._last_screen, screen)
            self._last_screen = screen
            # Skip a true no-op (unchanged buffer); but always emit a reset, even
            # one with empty data (a clear() to "") so the consumer clears.
            if not delta and not reset:
                return []
            fields: dict[str, Any] = {"data": delta}
            if reset:
                fields["reset"] = True
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
    def _delta(prev: str, cur: str) -> tuple[bool, str]:
        """Return ``(reset, data)`` for a new cumulative screen.

        ``reset`` is True when the buffer diverged from a pure append (the
        connector's ``clear()`` reset it), so ``data`` is the full new buffer
        and the consumer must clear its display first. Otherwise ``data`` is the
        delta to append. See the module docstring for the cap-slide vs reset
        distinction.
        """
        if not prev:
            return False, cur
        if cur.startswith(prev):
            return False, cur[len(prev) :]
        # Not a pure append. A cap slide keeps the buffer at/above its prior
        # length and shares a suffix/prefix overlap -> append only the new tail.
        # Anything shorter (or with no overlap) is a reset: clear, then write
        # the full buffer. The length test isn't fooled by a coincidental prompt
        # overlap, which a longest-overlap search alone would mistake for a slide.
        if len(cur) >= len(prev):
            overlap = MessageTranslator._suffix_prefix_overlap(prev, cur)
            if overlap:
                return False, cur[overlap:]
        return True, cur

    @staticmethod
    def _suffix_prefix_overlap(prev: str, cur: str) -> int:
        """Longest ``k`` with ``prev[-k:] == cur[:k]`` (0 if none).

        Finds the new tail after a cap slide. Scans occurrences of ``cur[0]`` in
        ``prev`` and verifies the suffix prefixes ``cur`` — the earliest match is
        the longest overlap. Uses ``str.find``/``str.startswith`` (C-level) so a
        no-overlap buffer doesn't trigger the quadratic char-by-char compare a
        descending-``k`` slice scan would.
        """
        if not cur:
            return 0
        idx = prev.find(cur[0])
        while idx != -1:
            if cur.startswith(prev[idx:]):
                return len(prev) - idx
            idx = prev.find(cur[0], idx + 1)
        return 0
