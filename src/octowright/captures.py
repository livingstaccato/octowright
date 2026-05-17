# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Persistent capture store for large analysis payloads.

Capture files keep full-fidelity text/JSON payloads on disk and return small
previews plus stable IDs to MCP clients. Follow-up tools can search or slice the
cached content without dumping the entire payload into the model context.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from octowright.defaults import CAPTURE_MAX_TOTAL_BYTES, CAPTURE_TTL_SECONDS, CAPTURES_DIR

DEFAULT_PREVIEW_CHARS = 2000
DEFAULT_SLICE_CHARS = 4000


@dataclass(frozen=True)
class CaptureRecord:
    capture_id: str
    path: Path
    kind: str
    host: str
    instance_id: str | None
    url: str | None
    title: str | None
    size_chars: int
    size_bytes: int
    created_at: float
    source: dict[str, Any]


def _safe_part(value: str | None, fallback: str) -> str:
    raw = (value or "").strip().lower() or fallback
    safe = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-._")
    return safe[:80] or fallback


def host_for_url(url: str | None) -> str:
    if not url:
        return "unknown-host"
    try:
        parsed = urlparse(url)
    except Exception:
        return "unknown-host"
    return _safe_part(parsed.netloc or parsed.path, "unknown-host")


def _capture_path(root: Path, host: str, instance_id: str | None, capture_id: str) -> Path:
    session_part = _safe_part(instance_id, "no-session")
    return root / host / session_part / f"{capture_id}.json"


def _read_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _record_from_payload(path: Path, payload: dict[str, Any]) -> CaptureRecord:
    content = str(payload.get("content", ""))
    stat = path.stat()
    raw_meta = payload.get("meta")
    meta: dict[str, Any] = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    return CaptureRecord(
        capture_id=str(payload.get("capture_id", path.stem)),
        path=path,
        kind=str(payload.get("kind", "unknown")),
        host=str(payload.get("host", "unknown-host")),
        instance_id=payload.get("instance_id") if isinstance(payload.get("instance_id"), str) else None,
        url=payload.get("url") if isinstance(payload.get("url"), str) else None,
        title=payload.get("title") if isinstance(payload.get("title"), str) else None,
        size_chars=len(content),
        size_bytes=stat.st_size,
        created_at=float(payload.get("created_at", stat.st_mtime)),
        source=meta,
    )


def _iter_capture_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.json") if p.is_file()]


def _remove_empty_parents(path: Path, root: Path) -> None:
    cur = path.parent
    while cur != root and root in cur.parents:
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def save_capture(
    *,
    kind: str,
    content: str,
    url: str | None = None,
    title: str | None = None,
    instance_id: str | None = None,
    source: dict[str, Any] | None = None,
    root: Path = CAPTURES_DIR,
    max_total_bytes: int = CAPTURE_MAX_TOTAL_BYTES,
    ttl_seconds: float = CAPTURE_TTL_SECONDS,
    preview_chars: int = DEFAULT_PREVIEW_CHARS,
) -> dict[str, Any]:
    """Persist a capture and return a compact wire-facing summary."""
    host = host_for_url(url)
    capture_id = f"cap_{int(time.time() * 1000):x}_{uuid.uuid4().hex[:10]}"
    path = _capture_path(root, host, instance_id, capture_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = time.time()
    cleanup_captures(root=root, ttl_seconds=ttl_seconds, max_total_bytes=max_total_bytes, apply=True)
    payload = {
        "capture_id": capture_id,
        "created_at": created_at,
        "kind": kind,
        "host": host,
        "instance_id": instance_id,
        "url": url,
        "title": title,
        "meta": source or {},
        "content": content,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    stat = path.stat()
    return {
        "capture_id": capture_id,
        "kind": kind,
        "host": host,
        "instance_id": instance_id,
        "url": url,
        "title": title,
        "path": str(path),
        "size_chars": len(content),
        "size_bytes": stat.st_size,
        "preview": content[:preview_chars],
        "truncated": len(content) > preview_chars,
        "actions": ["capture_get", "capture_search", "capture_list", "capture_cleanup"],
    }


def find_capture(capture_id: str, *, root: Path = CAPTURES_DIR) -> Path:
    for path in _iter_capture_files(root):
        if path.stem == capture_id:
            return path
    raise FileNotFoundError(f"capture not found: {capture_id}")


def get_capture_slice(
    capture_id: str,
    *,
    offset: int = 0,
    limit: int = DEFAULT_SLICE_CHARS,
    root: Path = CAPTURES_DIR,
) -> dict[str, Any]:
    path = find_capture(capture_id, root=root)
    payload = _read_payload(path)
    content = str(payload.get("content", ""))
    start = max(0, offset)
    cap = max(0, limit)
    end = min(len(content), start + cap)
    return {
        "capture_id": capture_id,
        "offset": start,
        "limit": cap,
        "next_offset": end if end < len(content) else None,
        "size_chars": len(content),
        "content": content[start:end],
        "truncated": end < len(content),
        "path": str(path),
    }


def search_capture(
    capture_id: str,
    query: str,
    *,
    regex: bool = False,
    context_chars: int = 500,
    limit: int = 20,
    root: Path = CAPTURES_DIR,
) -> dict[str, Any]:
    path = find_capture(capture_id, root=root)
    payload = _read_payload(path)
    content = str(payload.get("content", ""))
    matches: list[dict[str, Any]] = []
    if regex:
        iterator = re.finditer(query, content, flags=re.IGNORECASE | re.MULTILINE)
    else:
        iterator = re.finditer(re.escape(query), content, flags=re.IGNORECASE)
    for match in iterator:
        start = max(0, match.start() - context_chars)
        end = min(len(content), match.end() + context_chars)
        matches.append(
            {
                "start": match.start(),
                "end": match.end(),
                "context_start": start,
                "context_end": end,
                "context": content[start:end],
            }
        )
        if len(matches) >= limit:
            break
    return {
        "capture_id": capture_id,
        "query": query,
        "regex": regex,
        "count": len(matches),
        "matches": matches,
        "path": str(path),
    }


def list_captures(
    *,
    root: Path = CAPTURES_DIR,
    instance_id: str | None = None,
    host: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    records: list[CaptureRecord] = []
    for path in _iter_capture_files(root):
        try:
            record = _record_from_payload(path, _read_payload(path))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if instance_id is not None and record.instance_id != instance_id:
            continue
        if host is not None and record.host != _safe_part(host, host):
            continue
        records.append(record)
    records.sort(key=lambda r: r.created_at, reverse=True)
    selected = records[: max(0, limit)]
    return {
        "root": str(root),
        "count": len(records),
        "returned": len(selected),
        "captures": [
            {
                "capture_id": r.capture_id,
                "kind": r.kind,
                "host": r.host,
                "instance_id": r.instance_id,
                "url": r.url,
                "title": r.title,
                "size_chars": r.size_chars,
                "size_bytes": r.size_bytes,
                "created_at": r.created_at,
                "path": str(r.path),
            }
            for r in selected
        ],
    }


def _capture_file_stats(root: Path) -> list[tuple[Path, float, int]]:
    files: list[tuple[Path, float, int]] = []
    for path in _iter_capture_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((path, stat.st_mtime, stat.st_size))
    return files


def _expired_capture_sizes(
    files: list[tuple[Path, float, int]],
    *,
    ttl_seconds: float,
    reference: float,
) -> dict[Path, int]:
    if ttl_seconds < 0:
        return {}
    return {p: s for p, m, s in files if reference - m > ttl_seconds}


def _size_pruned_capture_sizes(
    files: list[tuple[Path, float, int]],
    *,
    total_bytes: int,
    max_total_bytes: int,
    preselected: dict[Path, int],
) -> dict[Path, int]:
    selected = dict(preselected)
    projected = total_bytes - sum(selected.values())
    for path, _mtime, size in sorted(files, key=lambda item: item[1]):
        if projected <= max_total_bytes:
            break
        if path in selected:
            continue
        selected[path] = size
        projected -= size
    return selected


def _delete_capture_files(to_remove: dict[Path, int], *, root: Path) -> tuple[int, int, list[dict[str, str]]]:
    removed_count = 0
    removed_bytes = 0
    errors: list[dict[str, str]] = []
    for path, size in to_remove.items():
        try:
            path.unlink()
            _remove_empty_parents(path, root)
        except OSError as exc:
            errors.append({"path": str(path), "error": str(exc)})
            continue
        removed_count += 1
        removed_bytes += size
    return removed_count, removed_bytes, errors


def cleanup_captures(
    *,
    root: Path = CAPTURES_DIR,
    ttl_seconds: float = CAPTURE_TTL_SECONDS,
    max_total_bytes: int = CAPTURE_MAX_TOTAL_BYTES,
    apply: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    reference = time.time() if now is None else now
    files = _capture_file_stats(root)
    total_bytes = sum(size for _, _, size in files)
    to_remove = _size_pruned_capture_sizes(
        files,
        total_bytes=total_bytes,
        max_total_bytes=max_total_bytes,
        preselected=_expired_capture_sizes(files, ttl_seconds=ttl_seconds, reference=reference),
    )
    errors: list[dict[str, str]] = []
    removed_count = 0
    removed_bytes = 0
    if apply:
        removed_count, removed_bytes, errors = _delete_capture_files(to_remove, root=root)
    return {
        "root": str(root),
        "total_files": len(files),
        "total_bytes": total_bytes,
        "eligible_count": len(to_remove),
        "eligible_bytes": sum(to_remove.values()),
        "removed_count": removed_count,
        "removed_bytes": removed_bytes,
        "max_total_bytes": max_total_bytes,
        "ttl_seconds": ttl_seconds,
        "dry_run": not apply,
        "errors": errors,
    }


def storage_report(*, recordings_dir: Path, config_dir: Path, captures_dir: Path = CAPTURES_DIR) -> dict[str, Any]:
    def _entry(path: Path) -> dict[str, Any]:
        files = 0
        dirs = 0
        bytes_total = 0
        if path.exists():
            for item in path.rglob("*"):
                try:
                    stat = item.stat()
                except OSError:
                    continue
                if item.is_file():
                    files += 1
                    bytes_total += stat.st_size
                elif item.is_dir():
                    dirs += 1
        return {"path": str(path), "exists": path.exists(), "files": files, "dirs": dirs, "bytes": bytes_total}

    return {
        "recordings": _entry(recordings_dir),
        "captures": _entry(captures_dir),
        "config": _entry(config_dir),
        "profiles": _entry(config_dir / "profiles"),
        "macros": _entry(config_dir / "macros"),
        "scenarios": _entry(config_dir / "scenarios"),
        "goldens": _entry(config_dir / "goldens"),
    }
