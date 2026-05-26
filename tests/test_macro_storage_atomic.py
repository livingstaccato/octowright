# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


def _import_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    import octowright.macros.storage as storage

    monkeypatch.setattr(storage, "MACROS_DIR", tmp_path / "macros")
    return storage


def _write_recording(tmp_path: Path, lines: list[dict[str, Any]] | None = None) -> Path:
    path = tmp_path / "recording.jsonl"
    rows = lines or [{"ts": "2026-04-24T10:00:00Z", "action": "navigate", "url": "https://octowright.com"}]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    return path


def test_save_macro_uses_atomic_write_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = _import_storage(monkeypatch, tmp_path)
    rec = _write_recording(tmp_path)
    calls: list[tuple[Path, str]] = []

    def fake_atomic_write_text(path: Path, body: str, *, encoding: str = "utf-8") -> None:
        calls.append((path, encoding))
        path.write_text(body, encoding=encoding)

    monkeypatch.setattr(storage, "atomic_write_text", fake_atomic_write_text)

    out = storage.save_macro(recording_path=rec, name="atomic-save")

    assert calls == [(out, "utf-8")]
    assert json.loads(out.read_text(encoding="utf-8"))["name"] == "atomic-save"


def test_write_macro_uses_atomic_write_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage = _import_storage(monkeypatch, tmp_path)
    calls: list[tuple[Path, str]] = []

    def fake_atomic_write_text(path: Path, body: str, *, encoding: str = "utf-8") -> None:
        calls.append((path, encoding))
        path.write_text(body, encoding=encoding)

    monkeypatch.setattr(storage, "atomic_write_text", fake_atomic_write_text)

    out = storage.write_macro(name="atomic-write", macro={"actions": []})

    assert calls == [(out, "utf-8")]
    assert json.loads(out.read_text(encoding="utf-8"))["name"] == "atomic-write"
