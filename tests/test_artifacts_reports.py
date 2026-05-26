# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

from octowright.artifacts.evidence import EvidenceBuilder
from octowright.artifacts.models import new_manifest, new_run_result
from octowright.artifacts.reports import write_artifact_manifest, write_run_bundle


def test_evidence_builder_creates_stable_ids(tmp_path: Path) -> None:
    builder = EvidenceBuilder()

    first = builder.screenshot(path=tmp_path / "a.png", label="before")
    second = builder.artifact(path=tmp_path / "result.json", kind="json", description="result")

    assert first["id"] == "ev_001"
    assert first["type"] == "screenshot"
    assert second["id"] == "ev_002"
    assert builder.records == [first, second]


def test_write_artifact_manifest_redacts_params(tmp_path: Path) -> None:
    manifest = new_manifest(
        artifact_type="macro",
        name="login",
        source={"macro_path": "/tmp/login.json"},
        parameters={"email": "me@example.com", "tenant": "prod"},
    )

    path = write_artifact_manifest(tmp_path / "artifact.json", manifest)
    data = json.loads(path.read_text())

    assert data["artifact_version"] == 1
    assert data["parameters"] == {"email": "<redacted>", "tenant": "prod"}


def test_write_run_bundle_writes_json_and_markdown(tmp_path: Path) -> None:
    evidence = EvidenceBuilder()
    evidence.screenshot(path=tmp_path / "screenshots" / "after.png", label="after")
    result = new_run_result(
        run_id="run_0001",
        status="ok",
        instance_id="inst-1",
        macro="login",
        args_used={"password": "secret", "tenant": "prod"},  # pragma: allowlist secret
        executed=3,
        skipped=1,
        error=None,
        recording_path="/tmp/recording.jsonl",
    )

    paths = write_run_bundle(
        run_dir=tmp_path,
        result=result,
        evidence=evidence.records,
        summary="Ran login macro successfully.",
    )

    assert paths["result"].name == "result.json"
    assert paths["evidence"].name == "evidence.json"
    assert paths["summary"].name == "summary.md"

    result_data = json.loads(paths["result"].read_text())
    evidence_data = json.loads(paths["evidence"].read_text())
    summary_text = paths["summary"].read_text()

    assert result_data["args_used"]["password"] == "<redacted>"
    assert result_data["args_used"]["tenant"] == "prod"
    assert evidence_data["records"][0]["type"] == "screenshot"
    assert "Ran login macro successfully." in summary_text
