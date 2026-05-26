# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from octowright.artifacts.models import now_iso

_REDACTED = "<redacted>"
_SENSITIVE_KEYS = r"(?:password|token|api_key|authorization|cookie|set-cookie)"
_AUTHORIZATION_KEYS = r"(?:authorization|proxyauthorization|proxy[-_]authorization)"
_KEY_VALUE_COOKIE_SECRET = re.compile(r"\b(cookie|set-cookie)\s*=\s*([^\r\n]+)", re.IGNORECASE)
_KEY_VALUE_AUTHORIZATION_SECRET = re.compile(rf"\b({_AUTHORIZATION_KEYS})\s*=\s*([^\r\n]+)", re.IGNORECASE)
_KEY_VALUE_SECRET = re.compile(rf"\b({_SENSITIVE_KEYS})\s*=\s*([^\s,;]+)", re.IGNORECASE)
_COOKIE_HEADER_SECRET = re.compile(r"\b(cookie|set-cookie)\s*:\s*([^\r\n]+)", re.IGNORECASE)
_COLON_SECRET = re.compile(rf"\b({_SENSITIVE_KEYS})\s*:\s*([^\r\n,;]+)", re.IGNORECASE)
_JSON_SECRET = re.compile(
    rf'("{_SENSITIVE_KEYS}"\s*:\s*")([^"]*)(")',
    re.IGNORECASE,
)


def redact_preview(preview: str) -> str:
    redacted = _KEY_VALUE_COOKIE_SECRET.sub(rf"\1={_REDACTED}", preview)
    redacted = _KEY_VALUE_AUTHORIZATION_SECRET.sub(rf"\1={_REDACTED}", redacted)
    redacted = _KEY_VALUE_SECRET.sub(rf"\1={_REDACTED}", redacted)
    redacted = _JSON_SECRET.sub(rf"\1{_REDACTED}\3", redacted)
    redacted = _COOKIE_HEADER_SECRET.sub(rf"\1: {_REDACTED}", redacted)
    return _COLON_SECRET.sub(rf"\1: {_REDACTED}", redacted)


class EvidenceBuilder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def _next_id(self) -> str:
        return f"ev_{len(self.records) + 1:03d}"

    def add(self, record: dict[str, Any]) -> dict[str, Any]:
        self.records.append(record)
        return record

    def screenshot(self, *, path: Path, label: str) -> dict[str, Any]:
        return self.add(
            {
                "id": self._next_id(),
                "type": "screenshot",
                "path": str(path),
                "label": label,
                "ts": now_iso(),
            }
        )

    def artifact(self, *, path: Path, kind: str, description: str) -> dict[str, Any]:
        return self.add(
            {
                "id": self._next_id(),
                "type": "artifact",
                "path": str(path),
                "kind": kind,
                "description": description,
                "ts": now_iso(),
            }
        )

    def log_excerpt(self, *, path: Path, offset: int, preview: str) -> dict[str, Any]:
        return self.add(
            {
                "id": self._next_id(),
                "type": "log_excerpt",
                "path": str(path),
                "offset": offset,
                "length": len(preview),
                "preview": redact_preview(preview),
                "ts": now_iso(),
            }
        )

    def digest(self, *, summary: str, truncated: bool, source_size: int, cap: int) -> dict[str, Any]:
        return self.add(
            {
                "id": self._next_id(),
                "type": "digest",
                "summary": summary,
                "truncated": truncated,
                "source_size": source_size,
                "cap": cap,
                "ts": now_iso(),
            }
        )
