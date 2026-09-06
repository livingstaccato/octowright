# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.errors import ControlBudgetExceededError

log = get_logger(__name__)

# Restrict label characters to alphanumeric + dot/underscore/hyphen so a
# caller-supplied label can never inject path separators, NUL bytes, or
# other characters that would escape base_dir on disk.
_LABEL_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Falsey tokens that opt OUT of owner-only recording permissions.
_PRIVATE_OFF = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})

#: Rows core writes about a session rather than about a page action. They
#: bypass ``OCTOWRIGHT_RECORDING_MAX_BYTES`` because the ceiling exists to
#: bound a firehose *page*, and dropping a metadata row instead loses the
#: recording's identity: no ``session_start`` means discovery cannot report
#: the kind, and a dropped ``artifact_registered`` means ``commit()`` returned
#: success for a registration that does not exist. ``_write_truncation_marker``
#: already bypasses the ceiling for exactly this reason; this generalizes it.
#: Core-only — ``record()`` stays a plugin's sole surface, ceiling and all.
CONTROL_ACTIONS: frozenset[str] = frozenset(
    {
        "session_start",
        "artifact_registered",
        "recording_truncated",
    }
)

#: Separate bounded budget for control rows. Bounded so a plugin cannot evade
#: the disk-fill guard by committing artifacts in a loop; a commit that would
#: exceed it fails visibly instead of vanishing.
CONTROL_BUDGET_BYTES = 64 * 1024


def _recording_max_bytes() -> int:
    """Per-recording byte ceiling, or 0 (unbounded) when disabled.

    A long-lived session — or a hostile page spewing console output — can grow
    its JSONL recording without bound and fill the disk. ``OCTOWRIGHT_RECORDING_MAX_BYTES``
    caps it: once the file would exceed the ceiling the recorder writes a single
    ``recording_truncated`` marker and stops appending. **OFF by default**
    (unbounded), mirroring ``OCTOWRIGHT_MIN_FREE_MEMORY_MB`` /
    ``OCTOWRIGHT_IDLE_GRACE``: silently dropping recorded actions is a behavior
    change an operator must opt into. A non-positive / falsey / unparsable
    value keeps it off.
    """
    raw = os.environ.get("OCTOWRIGHT_RECORDING_MAX_BYTES", "").strip().lower()
    if not raw or raw in _PRIVATE_OFF:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return 0
    return value if value > 0 else 0


def _recordings_private() -> bool:
    """Whether to lock recordings to the owner (0600 file / 0700 parent).

    Default ON. The JSONL can hold typed input, navigated URLs, console output,
    and — where ``OCTOWRIGHT_REDACT_INPUTS=off`` is set — cleartext
    credentials. A world-readable (0644) file would let any *local* user read all
    of that, bypassing the loopback HTTP boundary the dashboard enforces. Opt out
    with ``OCTOWRIGHT_RECORDINGS_PRIVATE`` set to a falsey token for setups that
    intentionally share recordings with other local users.
    """
    return os.environ.get("OCTOWRIGHT_RECORDINGS_PRIVATE", "on").strip().lower() not in _PRIVATE_OFF


# Mirrors octowright.http.artifacts.EVENT_ONLY_ACTIONS — kept here to avoid
# importing the http layer from the core recorder.
_EVENT_ONLY_ACTIONS = frozenset({"console", "download_saved", "popup_opened"})


class Recorder:
    """Append-only JSONL action log for one browser instance.

    Each `record()` call writes a single line:
        {"ts": "<iso8601>", "action": "<name>", ...fields}

    Concurrency contract: `record()` is synchronous and is intended to be
    invoked only from a single asyncio event loop (the one that owns this
    BrowserSession). It does not hold a lock — atomicity relies on the
    write+flush pair completing without a context switch, which is true for
    sync code on a single event loop. Calling `record()` from multiple
    threads concurrently (e.g. via `asyncio.to_thread`) is not supported
    and may interleave JSONL lines.
    """

    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        private = _recordings_private()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        # Lock the parent before opening so a fresh file inherits a private dir;
        # then force 0600 on the file itself (covers both a fresh create and an
        # older 0644 recording reopened in append mode). Best-effort: a chmod
        # that fails (exotic FS, read-only mount) must not break recording.
        if private:
            with contextlib.suppress(OSError):
                os.chmod(self.log_path.parent, 0o700)
        self._fh = self.log_path.open("a", encoding="utf-8")
        if private:
            with contextlib.suppress(OSError):
                os.chmod(self.log_path, 0o600)
        self._event_count = 0
        self._action_count = 0
        # Disk-fill guard. _bytes_written counts bytes already on disk (so a
        # reopened recording respects its current size); 0 ceiling = unbounded.
        self._max_bytes = _recording_max_bytes()
        self._bytes_written = self.log_path.stat().st_size if self._max_bytes else 0
        self._truncated = False
        # Control rows are budgeted separately from the action ceiling, so a
        # truncated recording still carries its metadata. Counted from zero on
        # every open: the budget bounds one process's writes, not the file.
        self._control_bytes = 0

    def record(self, action: str, **fields: Any) -> None:
        """Append one action row, subject to the byte ceiling.

        Raises ``ValueError`` for a :data:`CONTROL_ACTIONS` member. The control
        set is core-only and ``record`` is a plugin's sole recording surface,
        so accepting one here would let a plugin forge core's own metadata —
        spoofing the opening row the failed-launch rule reasons about, or
        claiming an artifact core never registered.
        """
        if action in CONTROL_ACTIONS:
            raise ValueError(f"{action!r} is a control action; core writes it through record_control()")
        if self._truncated:  # ceiling already hit — drop silently (marker already written)
            return
        entry = {"ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "action": action, **fields}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        if self._max_bytes:
            encoded = len(line.encode("utf-8"))
            if self._bytes_written + encoded > self._max_bytes:
                self._write_truncation_marker()
                self._truncated = True
                return
            self._bytes_written += encoded
        self._fh.write(line)
        self._fh.flush()
        self._event_count += 1
        if action not in _EVENT_ONLY_ACTIONS:
            self._action_count += 1

    def record_control(self, action: str, **fields: Any) -> None:
        """Append a core-owned metadata row, bypassing the action ceiling.

        Raises ``ValueError`` for an action outside :data:`CONTROL_ACTIONS` and
        ``ControlBudgetExceededError`` when the control budget is exhausted.
        """
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"{action!r} is not a control action")
        entry = {"ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "action": action, **fields}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        encoded = len(line.encode("utf-8"))
        if self._control_bytes + encoded > CONTROL_BUDGET_BYTES:
            raise ControlBudgetExceededError(
                f"control row {action!r} would exceed the {CONTROL_BUDGET_BYTES}-byte control budget"
            )
        self._control_bytes += encoded
        self._fh.write(line)
        self._fh.flush()
        self._event_count += 1

    def _write_truncation_marker(self) -> None:
        """Append a single bounded marker recording the cut. Bypasses the
        ceiling itself so the cut is always visible to replay/export/discovery."""
        marker = {
            "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "action": "recording_truncated",
            "limit_bytes": self._max_bytes,
            "bytes_written": self._bytes_written,
        }
        with contextlib.suppress(OSError, ValueError):
            self._fh.write(json.dumps(marker, ensure_ascii=False) + "\n")
            self._fh.flush()

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def action_count(self) -> int:
        return self._action_count

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


#: Bytes ``tail_log`` will read in ONE call. An unbounded ``fh.read()`` lets a
#: single ``?since=0`` on a recording that has grown for hours pull the whole
#: file into the leader — the process that owns every live browser — and then
#: multiply it by parsing every line into dicts.
#: Recordings have no ceiling by default (``OCTOWRIGHT_RECORDING_MAX_BYTES`` is
#: off), so nothing else bounded it. Every caller already loops on the returned
#: cursor, so a window costs an extra round trip, not correctness. 8 MiB is
#: ~40k typical events per call. (defaults.py is at its LOC ceiling.)
_TAIL_MAX_BYTES_DEFAULT = 8 * 1024 * 1024
_TAIL_DISABLE_TOKENS = frozenset({"", "0", "off", "false", "no", "never", "none", "disabled"})


def _tail_max_bytes() -> int | None:
    """Per-call read window; ``None`` = unbounded (the pre-bound behaviour).

    ``OCTOWRIGHT_TAIL_MAX_BYTES``. Unset → the default (ON, unlike the recording
    ceiling: this one costs a caller nothing, since they all page on the cursor
    already). A falsey token or a non-positive/unparsable value → unbounded.
    """
    raw = os.environ.get("OCTOWRIGHT_TAIL_MAX_BYTES")
    if raw is None:
        return _TAIL_MAX_BYTES_DEFAULT
    if raw.strip().lower() in _TAIL_DISABLE_TOKENS:
        return None
    try:
        value = int(raw)
    except ValueError:
        return _TAIL_MAX_BYTES_DEFAULT
    return value if value > 0 else None


def _offset_after_next_newline(fh: Any, start: int, chunk: int) -> int | None:
    """Scan forward from ``start`` for the end of the current line.

    Reads a chunk at a time and DISCARDS it — the point is to get past a line
    too big to hold, so holding it to find its end would defeat the exercise.
    ``None`` means the file ended without one, i.e. the writer is still
    producing that line and the caller must not skip it yet.
    """
    position = start
    while True:
        block = fh.read(chunk)
        if not block:
            return None
        index = block.find(b"\n")
        if index != -1:
            return position + index + 1
        position += len(block)


def _cursor_past_unterminated_window(fh: Any, path: Path, cursor: int, data: bytes, limit: int | None) -> int:
    """Where to resume when the window held no line boundary at all.

    Two different situations look identical here. Either the writer is mid-line
    and the newline is simply not on disk yet — the long-standing behaviour is
    to hold the cursor and re-read once it lands — or this single line is bigger
    than the entire window, which only became possible once the read was bounded.
    Holding still in THAT case makes every future poll re-read the same bytes and
    return nothing, forever, so the line is stepped over instead: it cannot be
    parsed at this window size, and buffering it to try is the exact allocation
    the bound exists to prevent.
    """
    if limit is None or len(data) < limit:
        return cursor
    skip_to = _offset_after_next_newline(fh, cursor + len(data), limit)
    if skip_to is None:
        return cursor  # still being written; not yet safe to skip
    log.warning(
        "octowright.recorder.tail_line_too_large",
        path=str(path),
        skipped_bytes=skip_to - cursor,
        limit_bytes=limit,
    )
    return skip_to


#: Bytes scanned per ``split()`` when walking a window line by line. Large
#: enough that the C-level split still does the work, small enough that a
#: caller stopping after one page has not paid for the whole window.
_LINE_SCAN_CHUNK = 262_144


def parse_log_line(raw_bytes: bytes) -> dict | None:
    """One JSONL line, or ``None`` if the recorder wrote it malformed.

    Exposed rather than kept private because a reader that pages on a byte
    cursor has to parse lines one at a time -- it needs the offset of the line
    it stopped at, which a whole-blob parse has already thrown away.
    """
    raw = raw_bytes.strip()
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _read_window(path: Path, cursor: int, max_bytes: int | None) -> tuple[bytes, int, int, int]:
    """One bounded read: ``(data, usable_end, new_cursor, total_bytes)``.

    ``data[:usable_end]`` is the whole lines in the window; a trailing partial
    line is left for the next call. Shared by ``tail_log`` and
    ``tail_log_lines`` so the parts that are easy to get wrong -- the byte
    bound, the line boundary, and stepping over a line bigger than the whole
    window -- exist once. Assumes a non-negative cursor; both callers clamp.
    """
    if not path.exists():
        return b"", 0, cursor, 0

    limit = _tail_max_bytes() if max_bytes == -1 else max_bytes
    total_bytes = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(cursor)
        data = fh.read() if limit is None else fh.read(limit)

        if not data:
            return b"", 0, cursor, total_bytes

        last_newline = data.rfind(b"\n")
        if last_newline == -1:
            return b"", 0, _cursor_past_unterminated_window(fh, path, cursor, data, limit), total_bytes

    return data, last_newline, cursor + last_newline + 1, total_bytes


def _iter_offset_lines(data: bytes, end: int, start: int) -> Iterator[tuple[int, bytes]]:
    """Each line with its ABSOLUTE offset in the file, a chunk at a time.

    Split on ``b"\n"`` rather than ``splitlines()``, whose extra separators
    (``\r``, ``\x0b``, ``\u2028`` and friends) would make the running offset
    disagree with the bytes actually on disk -- and a cursor that is off by a
    few bytes lands mid-line and loses a row.

    Chunked rather than one ``split()`` of the whole window, because a
    generator over a whole-blob split is only lazy in appearance: the split
    runs in full on the first ``next()``. Measured on an 8.4 MiB window for a
    caller that stops after 100 rows -- 5.51ms and a second copy of the window
    against 0.126ms chunked -- and within noise when the whole window IS
    consumed, so nothing pays for the laziness it does not use.
    """
    pos = 0
    offset = start
    carry = b""
    while pos < end:
        stop = min(pos + _LINE_SCAN_CHUNK, end)
        lines = (carry + data[pos:stop]).split(b"\n")
        pos = stop
        # The last element is a line this chunk cut in half -- unless this was
        # the final chunk, in which case it is the window's last whole line
        # and is yielded after the loop.
        carry = lines.pop()
        for raw in lines:
            yield offset, raw
            offset += len(raw) + 1
    if carry:
        yield offset, carry


def tail_log_lines(path: Path, cursor: int, max_bytes: int | None = -1) -> tuple[Iterator[tuple[int, bytes]], int, int]:
    """``tail_log``'s window, as raw lines paired with their byte offsets.

    Two things the parsed form cannot give a caller. A reader that stops early
    -- because it filled a row cap or a byte budget -- must resume at the line
    it stopped ON, and only an offset says where that is; resuming at the end
    of the window instead silently drops everything between. And parsing is
    lazy here, so a caller that wants 100 rows out of an 8 MiB window pays for
    100, not for every line in it.

    Returns ``(lines, new_cursor, total_bytes)``, where ``new_cursor`` is the
    end of the window -- the right answer for a caller that consumed all of it.
    """
    cursor = max(0, cursor)
    data, end, new_cursor, total_bytes = _read_window(path, cursor, max_bytes)
    return _iter_offset_lines(data, end, cursor), new_cursor, total_bytes


def tail_log(path: Path, cursor: int, max_bytes: int | None = -1) -> tuple[list[dict], int, int]:
    """
    Reads new JSONL events from a file since the given byte offset.

    At most ``max_bytes`` are read per call (``-1`` = use ``_tail_max_bytes()``,
    ``None`` = unbounded); the cursor always lands on a line boundary, so a
    caller that keeps passing the returned cursor back sees every event. A
    negative cursor is clamped to 0 rather than reaching ``seek()``, which
    answers it with a bare ``OSError``.

    Returns:
        tuple[events, new_cursor, total_bytes]
        - events: list of parsed JSON objects
        - new_cursor: updated byte offset for the next read
        - total_bytes: total size of the file on disk
    """
    # Deliberately NOT routed through ``tail_log_lines``: every caller here
    # discards the offsets, and pairing them costs a generator resume, a tuple
    # and an addition per line -- measured at +7% on an 8 MiB window and +16%
    # on a 1 MiB one. One C-level split is the right shape when the whole blob
    # is being parsed anyway. What is worth not duplicating is ``_read_window``.
    cursor = max(0, cursor)
    data, end, new_cursor, total_bytes = _read_window(path, cursor, max_bytes)
    events = [event for event in map(parse_log_line, data[:end].split(b"\n")) if event is not None]
    return events, new_cursor, total_bytes


def new_log_path(base_dir: Path, instance_id: str, label: str | None, kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = _LABEL_UNSAFE_RE.sub("-", label).strip("-.") if label else ""
    suffix = f"-{safe_label}" if safe_label else ""
    return base_dir / f"{stamp}-{kind}-{instance_id}{suffix}.jsonl"
