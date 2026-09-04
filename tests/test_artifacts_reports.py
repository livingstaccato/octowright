# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octowright.artifacts.evidence import EvidenceBuilder
from octowright.artifacts.models import new_manifest, new_run_result
from octowright.artifacts.reports import refresh_run_summary, write_artifact_manifest, write_run_bundle


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


def test_new_manifest_deep_copies_mutable_inputs() -> None:
    source: dict[str, Any] = {"macro_path": "/tmp/login.json", "nested": {"version": 1}}
    metadata: dict[str, Any] = {"tags": ["auth"]}

    manifest = new_manifest(artifact_type="macro", name="login", source=source, metadata=metadata)
    source["nested"]["version"] = 2
    metadata["tags"].append("mutated")

    assert manifest["source"] == {"macro_path": "/tmp/login.json", "nested": {"version": 1}}
    assert manifest["metadata"] == {"tags": ["auth"]}


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


def test_write_run_bundle_redacts_log_excerpt_preview(tmp_path: Path) -> None:
    preview = (
        "password=hunter2 token=abc123 cookie=sessionid=raw "
        '"api_key":"key-123" "authorization": "Bearer raw-token" '  # pragma: allowlist secret
        "authorization: Bearer colon-token Cookie: colon-session token: colon-abc password: colon-password"  # pragma: allowlist secret
        "\nCookie: sessionid=abc123; csrftoken=raw-token; theme=dark"  # pragma: allowlist secret
    )  # pragma: allowlist secret
    evidence = EvidenceBuilder()
    record = evidence.log_excerpt(
        path=tmp_path / "recording.jsonl",
        offset=12,
        preview=preview,  # pragma: allowlist secret
    )
    assert "hunter2" not in record["preview"]
    assert "colon-token" not in record["preview"]
    assert "colon-session" not in record["preview"]
    assert "colon-abc" not in record["preview"]
    assert "colon-password" not in record["preview"]
    assert "raw-token" not in record["preview"]
    assert "theme=dark" not in record["preview"]
    raw_evidence = [
        {
            "id": "ev_001",
            "type": "log_excerpt",
            "path": str(tmp_path / "recording.jsonl"),
            "offset": 12,
            "length": len(preview),
            "preview": preview,
            "ts": "2026-05-26T00:00:00Z",
        }
    ]
    result = new_run_result(
        run_id="run_0002",
        status="ok",
        instance_id="inst-1",
        macro="login",
        args_used={},
        executed=1,
        skipped=0,
        error=None,
        recording_path="/tmp/recording.jsonl",
    )

    paths = write_run_bundle(
        run_dir=tmp_path,
        result=result,
        evidence=raw_evidence,
        summary="Captured logs.",
    )

    evidence_text = paths["evidence"].read_text()
    evidence_data = json.loads(evidence_text)
    preview = evidence_data["records"][0]["preview"]

    assert evidence_data["records"][0]["type"] == "log_excerpt"
    assert "<redacted>" in preview
    assert "hunter2" not in evidence_text
    assert "abc123" not in evidence_text
    assert "sessionid=raw" not in evidence_text
    assert "key-123" not in evidence_text
    assert "Bearer raw-token" not in evidence_text
    assert "Bearer colon-token" not in evidence_text
    assert "colon-session" not in evidence_text
    assert "colon-abc" not in evidence_text
    assert "colon-password" not in evidence_text
    assert "raw-token" not in evidence_text
    assert "theme=dark" not in evidence_text


def test_write_run_bundle_redacts_key_value_cookie_preview_through_line_end(tmp_path: Path) -> None:
    preview = "cookie=sessionid=abc; csrftoken=raw-token; theme=dark"  # pragma: allowlist secret
    raw_evidence = [
        {
            "id": "ev_001",
            "type": "log_excerpt",
            "path": str(tmp_path / "recording.jsonl"),
            "offset": 12,
            "length": len(preview),
            "preview": preview,
            "ts": "2026-05-26T00:00:00Z",
        }
    ]
    result = new_run_result(
        run_id="run_0004",
        status="ok",
        instance_id="inst-1",
        macro="login",
        args_used={},
        executed=1,
        skipped=0,
        error=None,
        recording_path="/tmp/recording.jsonl",
    )

    paths = write_run_bundle(
        run_dir=tmp_path,
        result=result,
        evidence=raw_evidence,
        summary="Captured logs.",
    )

    evidence_text = paths["evidence"].read_text()
    evidence_data = json.loads(evidence_text)
    redacted_preview = evidence_data["records"][0]["preview"]

    assert redacted_preview == "cookie=<redacted>"
    assert "abc" not in evidence_text
    assert "raw-token" not in evidence_text
    assert "theme=dark" not in evidence_text


def test_write_run_bundle_redacts_key_value_authorization_preview_through_line_end(tmp_path: Path) -> None:
    preview = "authorization=Bearer raw-token"  # pragma: allowlist secret
    raw_evidence = [
        {
            "id": "ev_001",
            "type": "log_excerpt",
            "path": str(tmp_path / "recording.jsonl"),
            "offset": 12,
            "length": len(preview),
            "preview": preview,
            "ts": "2026-05-26T00:00:00Z",
        }
    ]
    result = new_run_result(
        run_id="run_0005",
        status="ok",
        instance_id="inst-1",
        macro="login",
        args_used={},
        executed=1,
        skipped=0,
        error=None,
        recording_path="/tmp/recording.jsonl",
    )

    paths = write_run_bundle(
        run_dir=tmp_path,
        result=result,
        evidence=raw_evidence,
        summary="Captured logs.",
    )

    evidence_text = paths["evidence"].read_text()
    evidence_data = json.loads(evidence_text)
    redacted_preview = evidence_data["records"][0]["preview"]

    assert redacted_preview == "authorization=<redacted>"
    assert "Bearer" not in evidence_text
    assert "raw-token" not in evidence_text


def test_write_run_bundle_uses_atomic_write_text(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[tuple[Path, str, str]] = []

    def fake_atomic_write_text(path: Path, body: str, *, encoding: str = "utf-8") -> None:
        calls.append((path, body, encoding))
        path.write_text(body, encoding=encoding)

    monkeypatch.setattr("octowright.artifacts.reports.atomic_write_text", fake_atomic_write_text)
    result = new_run_result(
        run_id="run_0003",
        status="ok",
        instance_id="inst-1",
        macro="login",
        args_used={},
        executed=1,
        skipped=0,
        error=None,
        recording_path=None,
    )

    write_run_bundle(run_dir=tmp_path, result=result, evidence=[], summary="No evidence.")

    assert [path.name for path, _, _ in calls] == ["result.json", "evidence.json", "summary.md"]
    assert {encoding for _, _, encoding in calls} == {"utf-8"}


# ─── summary rendering: the fallbacks nothing exercised ──────────────────────
#
# Every branch below is a fallback -- the path taken when a field is absent,
# empty, or the wrong type. The suite only ever rendered fully-populated
# records, so each fallback was free to invert without a test noticing.


def _run(**overrides: Any) -> dict[str, Any]:
    base = new_run_result(
        run_id="run_0001",
        status="ok",
        instance_id="inst-1",
        macro="login",
        args_used={},
        executed=3,
        skipped=1,
        error=None,
        recording_path=None,
    )
    base.update(overrides)
    return base


def test_a_blank_stored_summary_is_rebuilt_rather_than_rendered_empty(tmp_path: Path) -> None:
    """``isinstance(stored, str) and stored`` -- the emptiness half is load-bearing.

    Every artifact written before ``summary`` was persisted lacks the key, and
    a bundle can also carry it as ``""``. Relaxing the ``and`` to an ``or``
    returns that empty string instead of rebuilding, so re-verifying an
    existing run replaces the one sentence saying what it did with a blank --
    on the code path whose entire purpose is to add information.
    """
    refresh_run_summary(run_dir=tmp_path, result=_run(summary=""), evidence=[], verification={})

    body = (tmp_path / "summary.md").read_text()

    assert "Ran macro login: status=ok, executed=3, skipped=1." in body


def test_a_stored_summary_is_preferred_over_the_rebuilt_one(tmp_path: Path) -> None:
    """The other side: a real stored summary must survive the refresh."""
    refresh_run_summary(
        run_dir=tmp_path, result=_run(summary="Checked out as Tanuki Tim."), evidence=[], verification={}
    )

    assert "Checked out as Tanuki Tim." in (tmp_path / "summary.md").read_text()


def test_a_log_excerpt_without_a_string_preview_does_not_break_the_bundle(tmp_path: Path) -> None:
    """The ``isinstance`` half of the redaction guard is what keeps this from raising.

    ``redact_preview`` runs a regex over its argument. Relax the ``and`` to an
    ``or`` and a ``log_excerpt`` whose ``preview`` is absent hands ``None`` to
    it -- a ``TypeError`` raised while writing the report for a run that has
    already finished, losing the bundle rather than the field.
    """
    evidence = [{"id": "ev_001", "type": "log_excerpt", "path": "/tmp/x.log", "offset": 0}]

    refresh_run_summary(run_dir=tmp_path, result=_run(), evidence=evidence, verification={})

    assert "- `ev_001` `log_excerpt`" in (tmp_path / "summary.md").read_text()


def test_the_no_evidence_notice_appears_only_when_there_is_none(tmp_path: Path) -> None:
    """``if not evidence`` -- inverted, the report says the opposite of the truth.

    Both directions are asserted because either alone passes under the
    inversion for the case it does not cover: a report that claims nothing was
    captured while listing records, or one that lists nothing and does not say
    why.
    """
    refresh_run_summary(run_dir=tmp_path, result=_run(), evidence=[], verification={})
    assert "No evidence records captured." in (tmp_path / "summary.md").read_text()

    builder = EvidenceBuilder()
    builder.screenshot(path=tmp_path / "a.png", label="before")
    refresh_run_summary(run_dir=tmp_path, result=_run(), evidence=builder.records, verification={})
    assert "No evidence records captured." not in (tmp_path / "summary.md").read_text()


def test_an_evidence_label_falls_back_through_description_to_type(tmp_path: Path) -> None:
    """``label or description or type`` -- each rung has to be reachable.

    The chain exists because the three record kinds carry different naming
    fields: ``screenshot`` has ``label``, ``artifact`` has ``description``, and
    a record with neither still has to render as something. Re-associating the
    ``or``s into an ``and`` collapses a rung, and the line renders with an
    empty label -- a bullet in the report pointing at nothing.
    """
    evidence = [
        {"id": "ev_001", "type": "screenshot", "label": "before"},
        {"id": "ev_002", "type": "artifact", "description": "the exported script"},
        {"id": "ev_003", "type": "log_excerpt"},
    ]

    refresh_run_summary(run_dir=tmp_path, result=_run(), evidence=evidence, verification={})
    body = (tmp_path / "summary.md").read_text()

    assert "- `ev_001` `screenshot`: before" in body
    assert "- `ev_002` `artifact`: the exported script" in body
    assert "- `ev_003` `log_excerpt`: log_excerpt" in body
