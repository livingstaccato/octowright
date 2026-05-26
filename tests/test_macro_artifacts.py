# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_reloaded_defaults() -> None:
    yield
    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)


def _reload_macro_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "recordings"))
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "macros"))

    import octowright.artifacts.paths as artifact_paths
    import octowright.defaults as defaults
    import octowright.macros.artifacts as macro_artifacts
    import octowright.macros.storage as storage

    importlib.reload(defaults)
    importlib.reload(storage)
    importlib.reload(artifact_paths)
    importlib.reload(macro_artifacts)
    return storage, macro_artifacts, defaults.RECORDINGS_DIR


def _write_macro(storage, *, name: str = "login", parameters: list[str] | None = None) -> Path:
    macro = {
        "name": name,
        "description": "Login flow",
        "parameters": parameters or ["email", "password"],
        "actions": [
            {"action": "navigate", "url": "https://example.test/login"},
            {"action": "fill", "selector": "#email", "value": "{{email}}"},
            {"action": "fill", "selector": "#password", "value": "{{password}}"},
            {"action": "click", "selector": "button[type=submit]"},
        ],
    }
    return storage.write_macro(name=name, macro=macro)


def test_plan_reports_missing_args_and_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)

    plan = macro_artifacts.plan_macro_artifact("login", args={"email": "me@example.com"})

    assert plan["ok"] is False
    assert plan["macro"] == "login"
    assert plan["missing_args"] == ["password"]
    assert plan["args_used"] == {"email": "<redacted>"}
    assert str(recordings_dir / "artifacts" / "macros" / "login") in plan["paths"]["artifact_dir"]
    assert str(recordings_dir / "artifacts" / "macros" / "login" / "runs") in plan["paths"]["runs_dir"]
    assert str(recordings_dir / "artifacts" / "macros" / "login" / "exports") in plan["paths"]["exports_dir"]
    assert str(recordings_dir / "artifacts" / "macros" / "login" / "artifact.json") in plan["paths"]["manifest"]


def test_plan_ok_when_all_args_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, _recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)

    plan = macro_artifacts.plan_macro_artifact(
        "login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
    )

    assert plan["ok"] is True
    assert plan["missing_args"] == []
    assert plan["args_used"] == {"email": "<redacted>", "password": "<redacted>"}


def test_list_macro_artifacts_reads_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, _recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact(
        "login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
    )

    listed = macro_artifacts.list_macro_artifacts(name="login")

    assert listed["artifacts"][0]["name"] == "login"
    assert listed["artifacts"][0]["artifact_type"] == "macro"


def test_list_macro_artifacts_skips_malformed_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact(
        "login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
    )
    broken = recordings_dir / "artifacts" / "macros" / "broken"
    broken.mkdir(parents=True)
    (broken / "artifact.json").write_text(json.dumps(["not", "a", "manifest"]), encoding="utf-8")

    listed = macro_artifacts.list_macro_artifacts()

    assert [artifact["name"] for artifact in listed["artifacts"]] == ["login"]


def test_list_macro_artifacts_skips_symlinked_manifest_outside_recordings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage, macro_artifacts, recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    macro_artifacts.plan_macro_artifact(
        "login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "artifact.json").write_text(
        json.dumps(
            {
                "artifact_type": "macro",
                "name": "outside",
                "updated_at": "2999-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    macros_dir = recordings_dir / "artifacts" / "macros"
    (macros_dir / "outside").symlink_to(outside, target_is_directory=True)

    listed = macro_artifacts.list_macro_artifacts()

    assert [artifact["name"] for artifact in listed["artifacts"]] == ["login"]


def test_macro_digest_from_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, _recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)

    result = macro_artifacts.macro_digest(name="login", max_chars=4000)

    assert result["truncated"] is False
    assert "Macro login" in result["summary"]
