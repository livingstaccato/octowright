# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest


def _write_recording(tmp_path: Path, lines: list[dict[str, Any]]) -> Path:
    p = tmp_path / "recording.jsonl"
    p.write_text(
        "\n".join(json.dumps(line) for line in lines),
        encoding="utf-8",
    )
    return p


def _import_macros(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    # MACROS_DIR is owned by defaults; reload it first.
    from octowright import defaults

    importlib.reload(defaults)
    import octowright.macros.storage as _storage

    importlib.reload(_storage)
    import octowright.macros as _m

    importlib.reload(_m)
    return _m


def test_save_macro_parameterizes_semantic_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    recording = [
        {
            "ts": "2026-04-24T10:00:00.000Z",
            "action": "click",
            "selector": "button#login",
            "role": "button",
            "role_name": "Login",
            "label": "Login Button",
        },
        {
            "ts": "2026-04-24T10:00:01.000Z",
            "action": "fill",
            "selector": "input#user",
            "role": "textbox",
            "role_name": "Username",
            "value": "alice",
        },
    ]
    rec_path = _write_recording(tmp_path, recording)

    # We want to parameterize 'Login' and 'Username' and 'alice'
    path = m.save_macro(
        recording_path=rec_path,
        name="semantic-test",
        parameters={
            "login_text": "Login",
            "user_label": "Username",
            "user_val": "alice",
        },
    )

    assert path.exists()
    data = json.loads(path.read_text())
    actions = data["actions"]

    # Check first action (click)
    click = actions[0]
    assert click["action"] == "click"
    assert click["selector"] == "button#login"
    assert click["role_name"] == "{{login_text}}", f"Expected role_name to be parameterized, got {click['role_name']}"
    assert click["label"] == "Login Button"  # Not parameterized
    assert click["role"] == "button"

    # Check second action (fill)
    fill = actions[1]
    assert fill["selector"] == "input#user"
    assert fill["role_name"] == "{{user_label}}"
    assert fill["value"] == "{{user_val}}"


def test_save_macro_parameterizes_label_if_matched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    recording = [
        {
            "ts": "2026-04-24T10:00:00.000Z",
            "action": "click",
            "selector": "button#login",
            "role": "button",
            "role_name": "Login",
            "label": "Login Button",
        }
    ]
    rec_path = _write_recording(tmp_path, recording)

    path = m.save_macro(
        recording_path=rec_path,
        name="label-test",
        parameters={"btn_label": "Login Button"},
    )

    data = json.loads(path.read_text())
    actions = data["actions"]
    assert actions[0]["label"] == "{{btn_label}}"


def test_save_macro_keeps_semantic_and_selector_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    m = _import_macros(monkeypatch, tmp_path)

    recording = [
        {
            "ts": "2026-04-24T10:00:00.000Z",
            "action": "click",
            "selector": "#login",
            "role": "button",
            "role_name": "Log in",
            "label": "Primary Login Button",
        },
        {
            "ts": "2026-04-24T10:00:01.000Z",
            "action": "fill",
            "selector": "input[name=user]",
            "role": "textbox",
            "role_name": "Username",
            "value": "alice",
            "label": "Username field",
        },
    ]
    rec_path = _write_recording(tmp_path, recording)

    path = m.save_macro(recording_path=rec_path, name="clean-structure")

    data = json.loads(path.read_text())

    assert data["name"] == "clean-structure"
    assert data["parameters"] == []
    assert len(data["actions"]) == 2

    click = data["actions"][0]
    fill = data["actions"][1]

    # Both legacy selector and semantic metadata are preserved so playback can choose
    # semantic-first by default and still fallback to selector if needed.
    assert click["action"] == "click"
    for key in ("selector", "role", "role_name", "label"):
        assert key in click
    assert click["selector"] == "#login"
    assert click["role"] == "button"

    assert fill["action"] == "fill"
    for key in ("selector", "role", "role_name", "value"):
        assert key in fill
    assert fill["selector"] == "input[name=user]"
    assert fill["role"] == "textbox"
