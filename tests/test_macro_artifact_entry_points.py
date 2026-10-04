# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every entry point that reaches an artifact writer, listed by hand, each with a contract (#248).

Open question 2 of #248: which entry points reach ``write_artifact_manifest``,
``refresh_run_summary`` and ``macro_artifact_verify``. The design chose a list
kept by hand, each entry with a contract test, over a call-graph pass. The list
below is checked against the frozen sink inventory
(``tests/fixtures/privacy_sinks.json``), so a new caller of one of these
writers fails here until it is listed and given a contract.

The contract for every entry point is the same: a classified argument value
never reaches the artifact store in any serialized spelling -- not through a
manifest's ``parameters``, a run bundle, a verification or a re-rendered
summary -- including when it starts from a record an older Octowright wrote
unredacted.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.macros import execution
from tests._macro_artifact_fixtures import _FakeSession, _reload, restore_reloaded_defaults

SECRET = "Entry-Point-Pw-6Ym"  # pragma: allowlist secret

#: writer -> the functions in octowright.macros.artifacts that call it. MCP
#: tools of the same name (server/macros.py) call each public one.
ENTRY_POINTS: dict[str, set[str]] = {
    "write_artifact_manifest": {
        "plan_macro_artifact",  # macro_artifact_plan
        "export_macro_cli",  # macro_export_cli
        "run_macro_artifact",  # macro_artifact_run
        "macro_artifact_critical_points_set",  # macro_artifact_critical_points_set
        "macro_artifact_verify",  # macro_artifact_verify, and a run with critical points
    },
    "refresh_run_summary": {"macro_artifact_verify"},
    "macro_artifact_verify": {"_verify_run"},  # run_macro_artifact's verify step
}


def test_the_hand_list_matches_the_frozen_sink_inventory() -> None:
    from tests._script_module import load_script_module

    derive = load_script_module("scripts/derive_privacy_sinks.py")
    rows = json.loads(derive.FIXTURE.read_text(encoding="utf-8"))
    for writer, listed in ENTRY_POINTS.items():
        callers = {
            row["function"]
            for row in rows
            if row["callee"] == writer and row["module"] != "octowright.artifacts.reports"
        }
        assert callers == listed, writer


@pytest.fixture(autouse=True)
def _restore() -> Any:
    yield
    restore_reloaded_defaults()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Any, Any]:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    storage.write_macro(
        name="m",
        macro={
            "name": "m",
            "parameters": ["password", "note"],
            "actions": [{"action": "fill", "selector": "#pw", "value": "{{password}}"}],
        },
    )

    async def dispatch(*_a: Any, **_kw: Any) -> tuple[int, int]:
        return 1, 0

    monkeypatch.setattr(execution, "load_macro", storage.load_macro)
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "dispatch_plain_action", dispatch)
    monkeypatch.setattr(execution, "credential_fill_guard", lambda *_a: contextlib.nullcontext())
    return storage, macro_artifacts


def _spellings(value: str) -> set[str]:
    return {value, value.lower(), value.upper(), json.dumps(value)[1:-1]}


def _store_is_clean(tmp_path: Path) -> None:
    root = tmp_path / "recordings"
    for path in root.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert not any(spelling in text for spelling in _spellings(SECRET)), path


def test_plan(store: tuple[Any, Any], tmp_path: Path) -> None:
    _storage, macro_artifacts = store
    result = macro_artifacts.plan_macro_artifact("m", {"password": SECRET, "note": "n"})
    assert result["args_used"]["password"] == "<redacted>"
    _store_is_clean(tmp_path)


def test_export(store: tuple[Any, Any], tmp_path: Path) -> None:
    _storage, macro_artifacts = store
    result = macro_artifacts.export_macro_cli(name="m", args={"password": SECRET, "note": "n"})
    assert SECRET not in Path(result["path"]).read_text(encoding="utf-8")
    _store_is_clean(tmp_path)


def _critical_point() -> list[dict[str, Any]]:
    return [{"id": "cp1", "checks": [{"type": "result_status", "status": "ok"}]}]


@pytest.mark.asyncio
async def test_run_with_verification(store: tuple[Any, Any], tmp_path: Path) -> None:
    """A run with critical points reaches every writer: manifest, bundle, verify, summary refresh."""
    _storage, macro_artifacts = store
    macro_artifacts.macro_artifact_critical_points_set("m", _critical_point())

    result = await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "m", {"password": SECRET, "note": "n"}, capture=False, verify=True
    )

    assert result["verification_status"] == "passed"
    assert {"verification", "summary"} <= set(result["paths"])
    _store_is_clean(tmp_path)


def _legacy_manifest(macro_artifacts: Any) -> Path:
    """A manifest an older Octowright wrote with its parameters in the clear."""
    from octowright.artifacts.paths import ArtifactStore

    path = ArtifactStore().macro_manifest_path("m")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": "m", "parameters": {"password": SECRET}, "critical_points": []}))
    return path


def test_critical_points_set_rewrites_a_legacy_manifest_redacted(store: tuple[Any, Any], tmp_path: Path) -> None:
    _storage, macro_artifacts = store
    _legacy_manifest(macro_artifacts)

    macro_artifacts.macro_artifact_critical_points_set("m", _critical_point())

    _store_is_clean(tmp_path)


@pytest.mark.asyncio
async def test_verify_copies_no_argument_value_from_a_legacy_bundle(store: tuple[Any, Any], tmp_path: Path) -> None:
    """Verification re-renders the summary from result.json; an old one may hold args_used raw."""
    _storage, macro_artifacts = store
    macro_artifacts.macro_artifact_critical_points_set("m", _critical_point())
    result = await macro_artifacts.run_macro_artifact(
        _FakeSession(tmp_path), "m", {"note": "n"}, capture=False, verify=False
    )
    run_dir = Path(result["paths"]["run_dir"])
    legacy = json.loads((run_dir / "result.json").read_text())
    legacy["args_used"] = {"password": SECRET}
    (run_dir / "result.json").write_text(json.dumps(legacy))
    manifest = Path(result["paths"]["manifest"])
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "parameters": {"password": SECRET}}))

    verified = macro_artifacts.macro_artifact_verify("m", result["run_id"])

    assert verified["ok"] is True
    for name in ("verification.json", "summary.md"):
        text = (run_dir / name).read_text(encoding="utf-8")
        assert not any(spelling in text for spelling in _spellings(SECRET)), name
    assert SECRET not in manifest.read_text(encoding="utf-8")
