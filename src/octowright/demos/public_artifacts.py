# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from octowright.demos.models import DemoBundle

_DROP_EVENT_KEYS = {
    "har_path",
    "markdown_path",
    "trace_path",
    "user_data_dir",
    "video_dir",
    "video_path",
    "websocket_path",
}

_DROP_ROSTER_KEYS = {"log_path", "video_dir"}


def sanitize_public_artifacts(bundle: DemoBundle) -> None:
    for rel_path in bundle.replay_artifacts:
        artifact_path = bundle.root / rel_path
        if not artifact_path.exists():
            continue
        if artifact_path.suffix == ".jsonl":
            _sanitize_replay_log(bundle, artifact_path)
            continue
        if artifact_path.name == "participant-roster.json":
            _sanitize_participant_roster(bundle, artifact_path)


def _sanitize_replay_log(bundle: DemoBundle, replay_path: Path) -> None:
    sanitized_lines: list[str] = []
    for raw_line in replay_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        event = json.loads(raw_line)
        sanitized_lines.append(json.dumps(_sanitize_event(bundle, event), ensure_ascii=False))
    replay_path.write_text("\n".join(sanitized_lines) + "\n", encoding="utf-8")


def _sanitize_participant_roster(bundle: DemoBundle, roster_path: Path) -> None:
    payload = json.loads(roster_path.read_text(encoding="utf-8"))
    participants = payload.get("participants")
    if not isinstance(participants, list):
        return
    sanitized = []
    for participant in participants:
        if not isinstance(participant, dict):
            continue
        row = {key: value for key, value in participant.items() if key not in _DROP_ROSTER_KEYS}
        url = row.get("url")
        if isinstance(url, str):
            row["url"] = _sanitize_url(bundle, url)
        sanitized.append(row)
    payload["participants"] = sanitized
    roster_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sanitize_event(bundle: DemoBundle, event: dict[str, object]) -> dict[str, object]:
    sanitized = {key: value for key, value in event.items() if key not in _DROP_EVENT_KEYS}
    action = sanitized.get("action")
    url = sanitized.get("url")
    if isinstance(url, str):
        sanitized["url"] = _sanitize_url(bundle, url)
    raw_path = sanitized.get("path")
    if isinstance(raw_path, str):
        replacement = _sanitize_path(bundle, raw_path)
        if replacement is None or action == "markdown_cached":
            sanitized.pop("path", None)
        else:
            sanitized["path"] = replacement
    return sanitized


def _sanitize_url(bundle: DemoBundle, raw_url: str) -> str:
    if not raw_url.startswith("file://"):
        return raw_url
    relative = _bundle_relative_from_file_url(bundle, raw_url)
    if relative is None:
        return raw_url
    return f"bundle://{relative}"


def _sanitize_path(bundle: DemoBundle, raw_path: str) -> str | None:
    path = Path(raw_path)
    if not path.is_absolute():
        return raw_path
    try:
        relative = path.relative_to(bundle.root).as_posix()
    except ValueError:
        relative = _bundle_relative_from_path_parts(bundle, path.parts)
        if relative is None:
            return None
    return f"bundle://{relative}"


def _bundle_relative_from_file_url(bundle: DemoBundle, raw_url: str) -> str | None:
    parsed = urlparse(raw_url)
    if parsed.scheme != "file":
        return None
    file_path = Path(unquote(parsed.path))
    try:
        relative = file_path.relative_to(bundle.root).as_posix()
    except ValueError:
        relative = _bundle_relative_from_path_parts(bundle, file_path.parts)
        if relative is None:
            return None
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{relative}{query}{fragment}"


def _bundle_relative_from_path_parts(bundle: DemoBundle, parts: tuple[str, ...]) -> str | None:
    needle = ("demo", "bundles", bundle.id)
    if len(parts) < len(needle):
        return None
    for index in range(len(parts) - len(needle) + 1):
        if parts[index : index + len(needle)] == needle:
            remainder = parts[index + len(needle) :]
            if not remainder:
                return None
            return Path(*remainder).as_posix()
    return None
