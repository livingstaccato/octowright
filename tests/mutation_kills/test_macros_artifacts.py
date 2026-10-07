# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whole records the macro-artifact store writes and returns, outside a run.

Plan, export, list, digest, critical points, verify, status and delete each
write a manifest or return a payload an agent reads; these pin them field for
field, so a renamed key or a dropped path is a failure rather than a silently
different document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octowright.drawn_text import REDACTED_ASSERTION_TEXT, REDACTED_TEXT_REFUSAL
from tests._macro_artifact_fixtures import _reload, restore_reloaded_defaults
from tests.mutation_kills._artifact_helpers import artifact_dir, native, read_json, stable


@pytest.fixture(autouse=True)
def _restore_defaults() -> Any:
    yield
    restore_reloaded_defaults()


def _paths_metadata(name: str) -> dict[str, str]:
    root = native(f"<tmp>/recordings/artifacts/macros/{name}")
    return {"artifact_dir": root, "runs_dir": native(root, "runs"), "exports_dir": native(root, "exports")}


def _expected_manifest(name: str, *, parameters: dict[str, Any], missing: list[str], **extra: Any) -> dict[str, Any]:
    manifest = {
        "artifact_version": 1,
        "artifact_type": "macro",
        "name": name,
        "source": {"type": "macro", "path": native(f"<tmp>/macros/{name}.json")},
        "parameters": parameters,
        "latest_run": None,
        "exports": [],
        "critical_points": [],
        "metadata": {
            "description": "Search flow",
            "action_count": 1,
            "missing_args": missing,
            "paths": _paths_metadata(name),
            "ready": not missing,
        },
    }
    manifest.update(extra)
    return manifest


def _search_macro(
    storage: Any, *, name: str = "search", actions: list[dict[str, Any]] | None = None, **extra: Any
) -> None:
    storage.write_macro(
        name=name,
        macro={
            "name": name,
            "description": "Search flow",
            "parameters": ["q", "region"],
            "actions": actions or [{"action": "navigate", "url": "https://example.test/search?q={{q}}"}],
            **extra,
        },
    )


# --- plan ---------------------------------------------------------------------


def test_plan_writes_the_whole_manifest_and_returns_its_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)

    result = macro_artifacts.plan_macro_artifact("search", {"q": "otters"})

    assert stable(result, tmp_path) == {
        "ok": False,
        "macro": "search",
        "missing_args": ["region"],
        "args_used": {"q": "otters"},
        "paths": {
            "macro_path": native("<tmp>/macros/search.json"),
            "artifact_dir": native("<tmp>/recordings/artifacts/macros/search"),
            "manifest": native("<tmp>/recordings/artifacts/macros/search/artifact.json"),
            "runs_dir": native("<tmp>/recordings/artifacts/macros/search/runs"),
            "exports_dir": native("<tmp>/recordings/artifacts/macros/search/exports"),
        },
    }
    assert stable(read_json(result["paths"]["manifest"]), tmp_path) == _expected_manifest(
        "search", parameters={"q": "otters"}, missing=["region"]
    )


def test_plan_withholds_every_value_when_the_macro_calls_another(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage, actions=[{"action": "macro_call", "name": "other", "args": {"q": "{{q}}"}}])

    result = macro_artifacts.plan_macro_artifact("search", {"q": "otters", "region": "eu"})

    assert result["args_used"] == {"q": "<redacted>", "region": "<redacted>"}
    assert read_json(result["paths"]["manifest"])["parameters"] == {"q": "<redacted>", "region": "<redacted>"}


# --- list ---------------------------------------------------------------------


def _write_manifest(tmp_path: Path, name: str, **fields: Any) -> Path:
    path = artifact_dir(tmp_path, name) / "artifact.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, **fields}), encoding="utf-8")
    return path


def test_list_by_name_returns_only_that_artifact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_manifest(tmp_path, "alpha", updated_at="2026-01-01")
    _write_manifest(tmp_path, "beta", updated_at="2026-01-02")

    listed = macro_artifacts.list_macro_artifacts("alpha")

    assert [artifact["name"] for artifact in listed["artifacts"]] == ["alpha"]


def test_list_puts_a_manifest_without_updated_at_last(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _write_manifest(tmp_path, "alpha")
    _write_manifest(tmp_path, "beta", updated_at="2026-01-02")

    listed = macro_artifacts.list_macro_artifacts()

    assert [artifact["name"] for artifact in listed["artifacts"]] == ["beta", "alpha"]


# --- digest -------------------------------------------------------------------


def test_digest_by_name_uses_the_default_cap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)

    assert macro_artifacts.macro_digest(name="search")["cap"] == 4000


def test_digest_without_a_source_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    with pytest.raises(ValueError) as excinfo:
        macro_artifacts.macro_digest()

    assert str(excinfo.value) == "provide either name or recording_path"


def test_digest_of_a_recording_outside_the_root_names_what_was_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    outside = tmp_path / "elsewhere.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        macro_artifacts.macro_digest(recording_path=str(outside))

    assert str(excinfo.value).startswith(f"macro digest recording path {str(outside)!r} resolves outside ")


# --- export -------------------------------------------------------------------


def test_export_writes_the_manifest_and_returns_the_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)

    result = macro_artifacts.export_macro_cli(name="search", args={"q": "otters-in-the-river"})

    script = native("<tmp>/recordings/artifacts/macros/search/exports/search.py")
    assert stable(result, tmp_path) == {"ok": True, "macro": "search", "path": script, "import_safe": True}
    assert "otters-in-the-river" in Path(result["path"]).read_text(encoding="utf-8")
    manifest = stable(read_json(artifact_dir(tmp_path, "search") / "artifact.json"), tmp_path)
    assert manifest == _expected_manifest(
        "search",
        parameters={"q": "otters-in-the-river"},
        missing=["region"],
        exports=[{"path": script, "kind": "python-cli"}],
    )


def test_export_without_evidence_leaves_the_evidence_writer_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)

    with_evidence = Path(macro_artifacts.export_macro_cli(name="search", out_path="a.py")["path"])
    without = Path(macro_artifacts.export_macro_cli(name="search", out_path="b.py", include_evidence=False)["path"])

    assert "class _Evidence" in with_evidence.read_text(encoding="utf-8")
    assert "class _Evidence" not in without.read_text(encoding="utf-8")


def test_export_refuses_an_unbound_assertion_naming_the_macro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage, actions=[{"action": "expect_no_text", "text": REDACTED_ASSERTION_TEXT}])

    with pytest.raises(ValueError) as excinfo:
        macro_artifacts.export_macro_cli(name="search")

    assert str(excinfo.value) == f"macro 'search' cannot be exported at step 0: {REDACTED_TEXT_REFUSAL}"


def test_export_refuses_an_unrunnable_step_naming_the_macro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage, actions=[{"action": "teleport"}])

    with pytest.raises(ValueError) as excinfo:
        macro_artifacts.export_macro_cli(name="search")

    assert str(excinfo.value) == "macro 'search' cannot be exported: the script cannot run step 0 (teleport)"


def test_export_of_a_macro_without_an_action_list_is_not_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    (tmp_path / "macros").mkdir(parents=True, exist_ok=True)
    (tmp_path / "macros" / "bare.json").write_text(json.dumps({"name": "bare", "actions": None}), encoding="utf-8")

    assert macro_artifacts.export_macro_cli(name="bare")["ok"] is True


def test_export_classifies_args_with_the_macros_own_specs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Under the `reject` policy a non-credential classified argument refuses the
    # export. `username` is identity-tier by name; this macro unmarks it.
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    monkeypatch.setenv("OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY", "reject")
    _search_macro(storage, parameter_specs={"username": {"sensitive": False}})

    assert macro_artifacts.export_macro_cli(name="search", args={"username": "ada"})["ok"] is True


# --- critical points ----------------------------------------------------------


def test_setting_critical_points_without_a_manifest_builds_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)

    result = macro_artifacts.macro_artifact_critical_points_set("search", [{"description": "lands"}])

    point = {
        "description": "lands",
        "id": "CP1",
        "status": "unknown",
        "checks": [],
        "evidence": [],
        "last_verified_run": None,
        "notes": None,
    }
    assert result == {"ok": True, "critical_points": [point]}
    manifest = stable(read_json(artifact_dir(tmp_path, "search") / "artifact.json"), tmp_path)
    assert manifest == _expected_manifest("search", parameters={}, missing=["q", "region"], critical_points=[point])


def test_setting_critical_points_keeps_the_rest_of_an_existing_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)
    _write_manifest(tmp_path, "search", latest_run={"run_id": "run_0003"}, parameters={"q": "x"})

    macro_artifacts.macro_artifact_critical_points_set("search", [{"id": "a", "description": "d"}])

    manifest = read_json(artifact_dir(tmp_path, "search") / "artifact.json")
    assert (manifest["latest_run"], manifest["parameters"]) == ({"run_id": "run_0003"}, {"q": "x"})


def test_a_stored_manifest_is_redacted_with_the_macros_own_view_when_rewritten(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage, parameter_specs={"display": {"sensitive": True}})
    stored_password = "-".join(("pw", "123"))  # pragma: allowlist secret
    _write_manifest(tmp_path, "search", parameters={"display": "Ada L.", "q": "otters", "password": stored_password})

    macro_artifacts.macro_artifact_critical_points_set("search", [])

    stored = read_json(artifact_dir(tmp_path, "search") / "artifact.json")["parameters"]
    assert stored == {"display": "<redacted>", "q": "otters", "password": "<redacted>"}


def test_verify_run_without_a_manifest_is_not_configured(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _storage, macro_artifacts = _reload(monkeypatch, tmp_path)

    assert macro_artifacts._verify_run("absent", "run_0001", True) == ("not_configured", {})


# --- verify -------------------------------------------------------------------


def _bundle(tmp_path: Path, name: str, *, evidence: dict[str, Any]) -> Path:
    run = artifact_dir(tmp_path, name) / "runs" / "run_0001"
    run.mkdir(parents=True)
    (run / "result.json").write_text(json.dumps({"status": "ok", "summary": "s"}), encoding="utf-8")
    (run / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    return run


def test_verify_reads_a_bundle_whose_evidence_has_no_records(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)
    checks = [{"type": "result_status", "status": "ok"}]
    _write_manifest(tmp_path, "search", critical_points=[{"id": "cp1", "checks": checks}])
    run = _bundle(tmp_path, "search", evidence={})

    result = macro_artifacts.macro_artifact_verify("search", "run_0001")

    assert stable(result, tmp_path) == {
        "ok": True,
        "status": "passed",
        "paths": {
            "verification": native("<tmp>/recordings/artifacts/macros/search/runs/run_0001/verification.json"),
            "summary": native("<tmp>/recordings/artifacts/macros/search/runs/run_0001/summary.md"),
        },
    }
    text = (run / "verification.json").read_text(encoding="utf-8")
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False)


# --- status and delete --------------------------------------------------------


def test_delete_reports_what_it_removed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    storage, macro_artifacts = _reload(monkeypatch, tmp_path)
    _search_macro(storage)
    macro_artifacts.plan_macro_artifact("search")
    (artifact_dir(tmp_path, "search") / "runs" / "run_0001").mkdir(parents=True)

    result = macro_artifacts.delete_macro_artifact("search")

    assert stable(result, tmp_path) == {
        "deleted": True,
        "name": "search",
        "path": native("<tmp>/recordings/artifacts/macros/search"),
        "runs_removed": 1,
    }
