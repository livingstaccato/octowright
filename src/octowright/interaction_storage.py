# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Private append-only sidecars with bounded, byte-cursor disk reads."""

from __future__ import annotations

import contextlib
import json
import os
import stat
import time
from pathlib import Path
from typing import Any, BinaryIO

from octowright._paths import _DIR_FD_SUPPORTED
from octowright.interaction_schema import integer, number, valid_row

MAX_BYTES = 64 * 1024 * 1024
MAX_LINE = 4096
READ_WINDOW = 1024 * 1024
TRUNCATED = {"type": "truncated", "v": 1, "reason": "capacity"}


def interaction_path(log_path: Path) -> Path:
    return log_path.with_suffix(".interactions.jsonl")


def _open(log_path: Path, *, write: bool) -> BinaryIO:
    path = interaction_path(log_path)
    root = path.parent.resolve(strict=True)
    if path.is_symlink():
        raise ValueError("interaction sidecar must not be a symlink")
    if write:
        with contextlib.suppress(OSError):
            root.chmod(0o700)
    flags = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= os.O_WRONLY | os.O_APPEND | os.O_CREAT if write else os.O_RDONLY
    if _DIR_FD_SUPPORTED:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fd = os.open(path.name, flags, 0o600, dir_fd=parent)
        finally:
            os.close(parent)
    else:
        # Match _paths' Windows name-based fallback. Reject existing links
        # before opening; POSIX additionally pins the parent and uses NOFOLLOW.
        fd = os.open(root / path.name, flags | getattr(os, "O_BINARY", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("interaction sidecar must be a regular file")
        if write:
            with contextlib.suppress(OSError):
                os.chmod(fd if os.chmod in os.supports_fd else path, 0o600)
        stream = os.fdopen(fd, "ab" if write else "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise
    return stream


def _encode(row: dict[str, Any]) -> bytes:
    if not valid_row(row):
        raise ValueError("invalid interaction row")
    data = json.dumps(row, separators=(",", ":"), allow_nan=False).encode() + b"\n"
    if len(data) > MAX_LINE:
        raise ValueError("interaction row too large")
    return data


class InteractionSink:
    """One event-loop-owned writer; reserve a terminal reset marker before filling."""

    def __init__(self, log_path: Path, *, max_bytes: int = MAX_BYTES) -> None:
        self._stream = _open(log_path, write=True)
        self._size = os.fstat(self._stream.fileno()).st_size
        self._cap = min(max_bytes, MAX_BYTES)
        self._stopped = False

    def append(self, row: dict[str, Any]) -> bool:
        if self._stopped:
            return False
        data = _encode(row)
        at = row.get("at", time.time() * 1000)
        terminal = _encode({**TRUNCATED, "at": at if number(at, 1e12, 4e12) else time.time() * 1000})
        if self._size + len(data) + 128 > self._cap:
            if self._size + len(terminal) <= self._cap:
                self._stream.write(terminal)
            self._stopped = True
            return False
        self._stream.write(data)
        self._size += len(data)
        return True

    def close(self) -> None:
        self._stopped = True
        self._stream.close()


def _decode(line: bytes) -> dict[str, Any] | None:
    if len(line) > MAX_LINE:
        return None
    try:
        value = json.loads(line)
    except (ValueError, UnicodeError, RecursionError):
        return None
    return value if isinstance(value, dict) and valid_row(value) else None


def _scan(stream: BinaryIO, size: int, limit: int, result: dict[str, Any]) -> None:
    position = stream.tell()
    end = min(size, position + READ_WINDOW)
    while stream.tell() < end and len(result["events"]) < limit:
        line = stream.readline(min(MAX_LINE + 1, end - stream.tell()))
        if not line.endswith(b"\n"):
            if len(line) > MAX_LINE:
                result["truncated"] = True
                position = stream.tell()
                continue
            break
        position = stream.tell()
        value = _decode(line)
        if value is None:
            result["truncated"] = True
            continue
        result["events"].append(value)
        result["truncated"] |= value["type"] == "truncated" or value.get("reason") == "loss"
    result["next_cursor"] = position
    result["complete"] = position >= size


def read_interactions(log_path: Path, *, cursor: int = 0, limit: int = 1000) -> dict[str, Any]:
    """Read at most one MiB and ``limit`` whole validated rows; retain partial tails."""
    if not integer(cursor, 0, 2**63 - 1) or not integer(limit, 1, 5000):
        raise ValueError("invalid interaction pagination")
    result: dict[str, Any] = {"available": False, "events": [], "next_cursor": 0, "complete": True, "truncated": False}
    try:
        stream = _open(log_path, write=False)
    except FileNotFoundError:
        return result
    with stream:
        physical_size = os.fstat(stream.fileno()).st_size
        size = min(physical_size, MAX_BYTES)
        result["available"] = True
        stream.seek(max(0, size - MAX_LINE))
        tail = stream.read(MAX_LINE)
        result["truncated"] = physical_size > MAX_BYTES or b'"type":"truncated"' in tail or b'"reason":"loss"' in tail
        stream.seek(min(cursor, size))
        _scan(stream, size, limit, result)
    return result
