# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octowright._paths import atomic_write_text
from octowright.artifacts.models import now_iso
from octowright.artifacts.redaction import redact_mapping


def _json_write(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_artifact_manifest(path: Path, manifest: dict[str, Any]) -> Path:
    to_write = dict(manifest)
    to_write["parameters"] = redact_mapping(to_write.get("parameters"))
    to_write["updated_at"] = now_iso()
    return _json_write(path, to_write)


def write_run_bundle(
    *,
    run_dir: Path,
    result: dict[str, Any],
    evidence: list[dict[str, Any]],
    summary: str,
) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "result.json"
    evidence_path = run_dir / "evidence.json"
    summary_path = run_dir / "summary.md"

    result_payload = dict(result)
    result_payload["args_used"] = redact_mapping(result_payload.get("args_used"))
    result_payload["evidence_path"] = str(evidence_path)

    _json_write(result_path, result_payload)
    _json_write(evidence_path, {"records": evidence})
    atomic_write_text(summary_path, _render_summary(result_payload, evidence, summary), encoding="utf-8")
    return {"result": result_path, "evidence": evidence_path, "summary": summary_path}


def _render_summary(result: dict[str, Any], evidence: list[dict[str, Any]], summary: str) -> str:
    lines = [
        f"# Macro Artifact Run: {result.get('macro', '')}",
        "",
        summary,
        "",
        "## Result",
        "",
        f"- Status: `{result.get('status')}`",
        f"- Run ID: `{result.get('run_id')}`",
        f"- Executed: `{result.get('executed')}`",
        f"- Skipped: `{result.get('skipped')}`",
        "",
        "## Evidence",
        "",
    ]
    if not evidence:
        lines.append("No evidence records captured.")
    for record in evidence:
        label = record.get("label") or record.get("description") or record.get("type")
        lines.append(f"- `{record.get('id')}` `{record.get('type')}`: {label}")
    lines.append("")
    return "\n".join(lines)
