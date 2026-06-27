# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Restrict label characters to alphanumeric + dot/underscore/hyphen so a
# caller-supplied label can never inject path separators, NUL bytes, or
# other characters that would escape base_dir on disk.
_LABEL_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Falsey tokens that opt OUT of owner-only recording permissions.
_PRIVATE_OFF = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})


def _recording_max_bytes() -> int:
    """Per-recording byte ceiling, or 0 (unbounded) when disabled.

    A long-lived session — or a hostile page spewing console output — can grow
    its JSONL recording without bound and fill the disk. ``OCTOWRIGHT_RECORDING_MAX_BYTES``
    caps it: once the file would exceed the ceiling the recorder writes a single
    ``recording_truncated`` marker and stops appending. **OFF by default**
    (unbounded, back-compat), mirroring ``OCTOWRIGHT_MIN_FREE_MEMORY_MB`` /
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
    and — in legacy ``OCTOWRIGHT_REDACT_INPUTS=off`` deployments — cleartext
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

    def record(self, action: str, **fields: Any) -> None:
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


def tail_log(path: Path, cursor: int) -> tuple[list[dict], int, int]:
    """
    Reads new JSONL events from a file since the given byte offset.

    Returns:
        tuple[events, new_cursor, total_bytes]
        - events: list of parsed JSON objects
        - new_cursor: updated byte offset for the next read
        - total_bytes: total size of the file on disk
    """
    if not path.exists():
        return [], cursor, 0

    total_bytes = path.stat().st_size
    with path.open("rb") as fh:
        fh.seek(cursor)
        data = fh.read()

    if not data:
        return [], cursor, total_bytes

    last_newline = data.rfind(b"\n")
    if last_newline == -1:
        return [], cursor, total_bytes

    complete_data = data[:last_newline]
    new_cursor = cursor + last_newline + 1
    events = []
    for raw_bytes in complete_data.splitlines():
        raw = raw_bytes.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue

    return events, new_cursor, total_bytes


def new_log_path(base_dir: Path, instance_id: str, label: str | None, kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_label = _LABEL_UNSAFE_RE.sub("-", label).strip("-.") if label else ""
    suffix = f"-{safe_label}" if safe_label else ""
    return base_dir / f"{stamp}-{kind}-{instance_id}{suffix}.jsonl"
