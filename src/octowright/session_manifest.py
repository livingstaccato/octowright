# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Lightweight launch manifest for crash/orphan diagnostics.

This is intentionally not a reattach registry. It records enough state to
explain stale sessions after a daemon crash and is cleared on graceful close.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from octowright.defaults import SESSION_MANIFEST_PATH
from octowright.types import SessionManifest, SessionManifestEntry

SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _empty() -> SessionManifest:
    return {"schema_version": SCHEMA_VERSION, "sessions": {}}


def _resolve_path(path: Path | None) -> Path:
    return path or SESSION_MANIFEST_PATH


def read_manifest(path: Path | None = None) -> SessionManifest:
    """Return a parsed manifest, or an empty manifest if missing/corrupt."""
    path = _resolve_path(path)
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    sessions = data.get("sessions")
    if not isinstance(sessions, dict):
        return _empty()
    return {"schema_version": data.get("schema_version", SCHEMA_VERSION), "sessions": sessions}


def write_manifest(data: SessionManifest, path: Path | None = None) -> None:
    """Atomically replace the manifest."""
    path = _resolve_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def record_launch(
    *,
    session_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    user_data_dir: str | Path | None,
    log_path: str | Path,
    path: Path | None = None,
) -> SessionManifestEntry:
    """Add/update an open session entry and return the stored entry."""
    manifest = read_manifest(path)
    launched_at = _now_iso()
    entry: SessionManifestEntry = {
        "session_id": session_id,
        "kind": kind,
        "label": label,
        "profile": profile,
        "user_data_dir": str(user_data_dir) if user_data_dir is not None else None,
        "log_path": str(log_path),
        "launched_at": launched_at,
        "updated_at": launched_at,
        "state": "open",
        "daemon_pid": os.getpid(),
    }
    manifest["sessions"][session_id] = entry
    write_manifest(manifest, path)
    return entry


def remove_session(session_id: str, path: Path | None = None) -> bool:
    """Remove a gracefully closed session entry. Returns True when removed."""
    manifest = read_manifest(path)
    removed = manifest["sessions"].pop(session_id, None) is not None
    if removed:
        write_manifest(manifest, path)
    return removed


def stale_entries(
    *,
    live_session_ids: set[str],
    path: Path | None = None,
) -> list[SessionManifestEntry]:
    """Return manifest entries that are not present in the current live pool."""
    manifest = read_manifest(path)
    stale: list[SessionManifestEntry] = []
    for session_id, raw in sorted(manifest["sessions"].items()):
        if session_id in live_session_ids or not isinstance(raw, dict):
            continue
        entry = cast(SessionManifestEntry, {**raw})
        entry.setdefault("session_id", session_id)
        entry["reason"] = "manifest entry is not present in the live browser pool"
        stale.append(entry)
    return stale
