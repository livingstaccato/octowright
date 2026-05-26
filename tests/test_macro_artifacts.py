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


def test_plan_ignores_symlinked_manifest_outside_recordings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage, macro_artifacts, recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    artifact_dir = recordings_dir / "artifacts" / "macros" / "login"
    artifact_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_manifest = outside / "artifact.json"
    outside_manifest.write_text(
        json.dumps(
            {
                "artifact_type": "macro",
                "name": "login",
                "created_at": "1999-01-01T00:00:00Z",
                "latest_run": {"run_id": "evil", "path": str(outside)},
                "metadata": {"evil": True},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = artifact_dir / "artifact.json"
    manifest_path.symlink_to(outside_manifest)

    plan = macro_artifacts.plan_macro_artifact(
        "login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
    )

    assert plan["ok"] is True
    assert not manifest_path.is_symlink()
    assert json.loads(outside_manifest.read_text(encoding="utf-8"))["metadata"] == {"evil": True}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["latest_run"] is None
    assert "evil" not in manifest["metadata"]


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


def test_macro_export_cli_writes_import_safe_argparse_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage, macro_artifacts, recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)

    result = macro_artifacts.export_macro_cli(
        name="login",
        out_path=None,
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
        include_evidence=True,
    )

    script_path = Path(result["path"])
    text = script_path.read_text(encoding="utf-8")

    assert script_path == recordings_dir / "artifacts" / "macros" / "login" / "exports" / "login.py"
    assert "def run_login(" in text
    assert "argparse.ArgumentParser" in text
    assert 'if __name__ == "__main__":' in text
    assert "asyncio.run" in text
    assert "me@example.com" not in text
    assert "secret" not in text
    assert result["import_safe"] is True


class FakeSession:
    def __init__(self, tmp_path: Path) -> None:
        self.instance_id = "inst-1"
        self.log_path = tmp_path / "recording.jsonl"
        self.log_path.write_text('{"action":"click"}\n', encoding="utf-8")
        self.page = None


@pytest.mark.asyncio
async def test_run_macro_artifact_writes_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, _recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    session = FakeSession(tmp_path)

    async def fake_run_macro(*, session, name, args, slowmo_ms=None):
        return {"macro": name, "executed": 4, "skipped": 0, "args_used": args or {}, "slowmo_ms": slowmo_ms or 0}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)

    result = await macro_artifacts.run_macro_artifact(
        session=session,
        name="login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
        capture=False,
    )

    assert result["ok"] is True
    assert result["run_id"] == "run_0001"
    assert Path(result["paths"]["result"]).exists()
    assert Path(result["paths"]["evidence"]).exists()
    assert Path(result["paths"]["summary"]).exists()

    result_data = json.loads(Path(result["paths"]["result"]).read_text(encoding="utf-8"))
    assert result_data["args_used"] == {"email": "<redacted>", "password": "<redacted>"}

    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["latest_run"] == {"run_id": "run_0001", "path": result["paths"]["run_dir"]}


@pytest.mark.asyncio
async def test_run_macro_artifact_uses_notes_for_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, _recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    session = FakeSession(tmp_path)

    async def fake_run_macro(*, session, name, args, slowmo_ms=None):
        return {"macro": name, "executed": 1, "skipped": 0, "args_used": args or {}, "slowmo_ms": slowmo_ms or 0}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)

    result = await macro_artifacts.run_macro_artifact(
        session=session,
        name="login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
        capture=False,
        notes="Operator verified the login confirmation banner.",
    )

    assert result["summary"] == "Operator verified the login confirmation banner."
    summary_text = Path(result["paths"]["summary"]).read_text(encoding="utf-8")
    assert "Operator verified the login confirmation banner." in summary_text
    assert "Ran macro login" not in summary_text


@pytest.mark.asyncio
async def test_run_macro_artifact_ignores_symlinked_manifest_outside_recordings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage, macro_artifacts, recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    session = FakeSession(tmp_path)

    artifact_dir = recordings_dir / "artifacts" / "macros" / "login"
    artifact_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    outside_manifest = outside / "artifact.json"
    outside_manifest.write_text(
        json.dumps(
            {
                "artifact_type": "macro",
                "name": "login",
                "created_at": "1999-01-01T00:00:00Z",
                "latest_run": {"run_id": "evil", "path": str(outside)},
                "metadata": {"evil": True},
            }
        ),
        encoding="utf-8",
    )
    manifest_path = artifact_dir / "artifact.json"
    manifest_path.symlink_to(outside_manifest)

    async def fake_run_macro(*, session, name, args, slowmo_ms=None):
        return {"macro": name, "executed": 1, "skipped": 0, "args_used": args or {}, "slowmo_ms": slowmo_ms or 0}

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)

    result = await macro_artifacts.run_macro_artifact(
        session=session,
        name="login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
        capture=False,
    )

    assert not manifest_path.is_symlink()
    assert json.loads(outside_manifest.read_text(encoding="utf-8"))["metadata"] == {"evil": True}
    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["latest_run"] == {"run_id": "run_0001", "path": result["paths"]["run_dir"]}
    assert "evil" not in manifest["metadata"]


@pytest.mark.asyncio
async def test_run_macro_artifact_records_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts, _recordings_dir = _reload_macro_artifacts(monkeypatch, tmp_path)
    _write_macro(storage)
    session = FakeSession(tmp_path)

    async def fake_run_macro(*, session, name, args, slowmo_ms=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(macro_artifacts.macro_mod, "run_macro", fake_run_macro)

    result = await macro_artifacts.run_macro_artifact(
        session=session,
        name="login",
        args={"email": "me@example.com", "password": "secret"},  # pragma: allowlist secret
        capture=False,
    )

    assert result["ok"] is False
    assert Path(result["paths"]["result"]).exists()
    assert Path(result["paths"]["evidence"]).exists()

    result_data = json.loads(Path(result["paths"]["result"]).read_text(encoding="utf-8"))
    assert result_data["status"] == "failed"
    assert result_data["args_used"] == {"email": "<redacted>", "password": "<redacted>"}
    assert "RuntimeError" in result_data["error"]

    evidence_data = json.loads(Path(result["paths"]["evidence"]).read_text(encoding="utf-8"))
    assert evidence_data["records"][0]["type"] == "log_excerpt"
    assert "RuntimeError" in evidence_data["records"][0]["preview"]

    manifest = json.loads(Path(result["paths"]["manifest"]).read_text(encoding="utf-8"))
    assert manifest["latest_run"] == {"run_id": "run_0001", "path": result["paths"]["run_dir"]}
