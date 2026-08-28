# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from octowright._paths import atomic_write_text
from octowright.artifacts.evidence import redact_preview
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
    verification: dict[str, Any] | None = None,
) -> dict[str, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / "result.json"
    evidence_path = run_dir / "evidence.json"
    summary_path = run_dir / "summary.md"

    result_payload = dict(result)
    result_payload["args_used"] = redact_mapping(result_payload.get("args_used"))
    result_payload["evidence_path"] = str(evidence_path)
    # Kept so the summary can be regenerated once verification exists. This
    # bundle is necessarily written BEFORE verification runs -- verification
    # reads result.json and evidence.json -- so the verdict cannot be in the
    # first render, and re-rendering needs this prose back.
    result_payload["summary"] = summary
    evidence_payload = _redact_evidence(evidence)

    _json_write(result_path, result_payload)
    _json_write(evidence_path, {"records": evidence_payload})

    paths = {"result": result_path, "evidence": evidence_path}
    if verification is not None:
        verification_path = run_dir / "verification.json"
        _json_write(verification_path, verification)
        paths["verification"] = verification_path

    atomic_write_text(
        summary_path, _render_summary(result_payload, evidence_payload, summary, verification), encoding="utf-8"
    )
    paths["summary"] = summary_path
    return paths


def refresh_run_summary(
    *,
    run_dir: Path,
    result: dict[str, Any],
    evidence: list[dict[str, Any]],
    verification: dict[str, Any],
) -> Path:
    """Re-render ``summary.md`` for a run whose verification has now completed.

    ``write_run_bundle`` cannot include the verdict: verification consumes the
    result and evidence files that call writes, so it necessarily runs after.
    Without this, the summary's "Verification and Critical Points" section was
    unreachable in production -- every artifact run with critical points
    produced a human-readable report that said nothing about whether the
    claims held, while ``verification.json`` sat beside it with the answer.
    """
    summary_path = run_dir / "summary.md"
    atomic_write_text(
        summary_path,
        _render_summary(result, _redact_evidence(evidence), _summary_line(result), verification),
        encoding="utf-8",
    )
    return summary_path


def _summary_line(result: dict[str, Any]) -> str:
    """The run's prose line, rebuilt when the bundle predates it being stored.

    Bundles written before `write_run_bundle` began persisting `summary` carry
    no such key, and every artifact on disk today is one of those. Reading it
    as `""` would make the first re-verify of an existing run silently blank
    the one sentence saying what the run did -- replacing information with
    nothing, on a path whose whole purpose is to add information.

    The reconstruction matches what `run_macro_artifact` composes when no
    operator note was given. A run that *did* carry a note cannot have it
    recovered, so this is a floor, not a round trip.
    """
    stored = result.get("summary")
    if isinstance(stored, str) and stored:
        return stored
    return (
        f"Ran macro {result.get('macro', 'unknown')}: "
        f"status={result.get('status', 'unknown')}, "
        f"executed={result.get('executed', 0)}, "
        f"skipped={result.get('skipped', 0)}."
    )


def _redact_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for record in evidence:
        sanitized = dict(record)
        if sanitized.get("type") == "log_excerpt" and isinstance(sanitized.get("preview"), str):
            sanitized["preview"] = redact_preview(sanitized["preview"])
        records.append(sanitized)
    return records


def _render_summary(
    result: dict[str, Any], evidence: list[dict[str, Any]], summary: str, verification: dict[str, Any] | None = None
) -> str:
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

    if verification is not None:
        lines.append("## Verification and Critical Points")
        lines.append("")
        v_status = verification.get("status", "unknown")
        lines.append(f"**Verification Status**: `{v_status}`")
        lines.append("")
        cps = verification.get("critical_points", [])
        if not cps:
            lines.append("No critical points evaluated.")
        else:
            for cp in cps:
                lines.append(f"### {cp.get('id', 'CP')}: {cp.get('description', 'Unknown')}")
                lines.append(f"- Status: `{cp.get('status', 'unknown')}`")
                checks = cp.get("checks", [])
                if checks:
                    lines.append("- Checks:")
                    for c in checks:
                        lines.append(
                            f"  - `{c.get('type', 'unknown')}`: `{c.get('status', 'unknown')}` - {c.get('message', '')}"
                        )
                lines.append("")
        lines.append("")

    return "\n".join(lines)
