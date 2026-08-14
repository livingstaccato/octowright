# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

import octowright.macros as macro_mod
from octowright._tracing import counter, set_attrs, span
from octowright.artifacts.digest import digest_macro, digest_recording_text
from octowright.artifacts.evidence import EvidenceBuilder
from octowright.artifacts.models import new_manifest, new_run_result
from octowright.artifacts.paths import ArtifactStore
from octowright.artifacts.paths import slug as artifact_slug
from octowright.artifacts.redaction import redact_mapping
from octowright.artifacts.reports import write_artifact_manifest, write_run_bundle
from octowright.artifacts.script_export import write_macro_cli
from octowright.macros.storage import load_macro, macro_path

log = get_logger("octowright.artifacts.verification")


def _cap_macro(name: str) -> str:
    if len(name) > 100:
        return name[:97] + "..."
    return name


def plan_macro_artifact(name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    macro = load_macro(name)
    args_used = dict(args or {})
    missing_args = _missing_args(macro, args_used)
    store = ArtifactStore()
    manifest_path = store.macro_manifest_path(name)
    artifact_dir = manifest_path.parent
    runs_dir = artifact_dir / "runs"
    exports_dir = artifact_dir / "exports"

    manifest = _manifest_for_plan(
        name=name,
        macro=macro,
        args_used=args_used,
        missing_args=missing_args,
        artifact_dir=artifact_dir,
        runs_dir=runs_dir,
        exports_dir=exports_dir,
    )
    existing_manifest_path = _safe_existing_manifest_path(store, manifest_path)
    if existing_manifest_path is not None:
        manifest = _merge_existing_manifest(existing_manifest_path, manifest)
    write_artifact_manifest(manifest_path, manifest)

    return {
        "ok": not missing_args,
        "macro": name,
        "missing_args": missing_args,
        "args_used": redact_mapping(args_used),
        "paths": {
            "macro_path": str(macro_path(name)),
            "artifact_dir": str(artifact_dir),
            "manifest": str(manifest_path),
            "runs_dir": str(runs_dir),
            "exports_dir": str(exports_dir),
        },
    }


def list_macro_artifacts(name: str | None = None, limit: int = 20) -> dict[str, Any]:
    store = ArtifactStore()
    manifests = [store.root / "macros" / artifact_slug(name) / "artifact.json"] if name else _all_macro_manifests(store)
    artifacts: list[dict[str, Any]] = []
    for manifest in manifests:
        artifact = _compact_manifest(store, manifest)
        if artifact is not None:
            artifacts.append(artifact)
    artifacts.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    capped = artifacts[: max(0, int(limit))]
    return {"artifacts": capped, "count": len(capped), "limit": max(0, int(limit))}


def macro_digest(
    name: str | None = None,
    recording_path: str | None = None,
    max_chars: int = 4000,
) -> dict[str, Any]:
    if name is None and recording_path is None:
        raise ValueError("provide either name or recording_path")
    if name is not None:
        result = digest_macro(load_macro(name), max_chars=max_chars)
        result["source"] = {"type": "macro", "name": name, "path": str(macro_path(name))}
        return result

    store = ArtifactStore()
    path = store._contained(Path(str(recording_path)).expanduser(), label="macro digest recording path")
    result = digest_recording_text(path.read_text(encoding="utf-8"), max_chars=max_chars)
    result["source"] = {"type": "recording", "path": str(path)}
    return result


def export_macro_cli(
    *,
    name: str,
    out_path: str | None = None,
    args: dict[str, Any] | None = None,
    include_evidence: bool = True,
) -> dict[str, Any]:
    macro = load_macro(name)
    args_used = dict(args or {})
    store = ArtifactStore()
    target = store.resolve_macro_export_path(name, out_path)
    write_macro_cli(path=target, name=name, macro=macro, args=args_used, include_evidence=include_evidence)

    manifest_path = store.macro_manifest_path(name)
    artifact_dir = manifest_path.parent
    manifest = _manifest_for_plan(
        name=name,
        macro=macro,
        args_used=args_used,
        missing_args=_missing_args(macro, args_used),
        artifact_dir=artifact_dir,
        runs_dir=artifact_dir / "runs",
        exports_dir=artifact_dir / "exports",
    )
    existing_manifest_path = _safe_existing_manifest_path(store, manifest_path)
    if existing_manifest_path is not None:
        manifest = _merge_existing_manifest(existing_manifest_path, manifest)
    manifest["exports"] = [{"path": str(target), "kind": "python-cli"}]
    write_artifact_manifest(manifest_path, manifest)
    return {"ok": True, "macro": name, "path": str(target), "import_safe": True}


async def run_macro_artifact(
    session: Any,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    capture: bool = True,
    notes: str | None = None,
    slowmo_ms: int | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    # One root lease for the entire replay: screenshots, replay, failure
    # evidence, run-bundle writes, verification, metrics, and the response
    # all stay under it. The nested run_macro/session.screenshot calls
    # re-enter it (same task); the root observable status stays
    # "macro_artifact_run" throughout.
    async with session.operation("macro_artifact_run"):
        macro = load_macro(name)
        args_used = dict(args or {})
        store = ArtifactStore()
        artifact_dir = store.macro_dir(name)
        runs_dir = artifact_dir / "runs"
        exports_dir = artifact_dir / "exports"
        manifest_path = artifact_dir / "artifact.json"
        manifest = _manifest_for_plan(
            name=name,
            macro=macro,
            args_used=args_used,
            missing_args=_missing_args(macro, args_used),
            artifact_dir=artifact_dir,
            runs_dir=runs_dir,
            exports_dir=exports_dir,
        )
        existing_manifest_path = _safe_existing_manifest_path(store, manifest_path)
        if existing_manifest_path is not None:
            manifest = _merge_existing_manifest(existing_manifest_path, manifest)
        write_artifact_manifest(manifest_path, manifest)

        run_dir = store.next_run_dir(artifact_dir)
        evidence = EvidenceBuilder()
        await _capture_screenshot(session=session, run_dir=run_dir, evidence=evidence, label="before", enabled=capture)

        status = "ok"
        error: str | None = None
        executed = 0
        skipped = 0
        try:
            replay = await macro_mod.run_macro(session=session, name=name, args=args_used, slowmo_ms=slowmo_ms)
            if isinstance(replay, dict):
                executed = int(replay.get("executed", 0))
                skipped = int(replay.get("skipped", 0))
        except Exception as exc:  # Return artifact paths to MCP callers even when replay fails.
            status = "failed"
            error = f"{exc.__class__.__name__}: {exc}"
            evidence.log_excerpt(
                path=Path(getattr(session, "log_path", run_dir / "replay.jsonl")),
                offset=0,
                preview=traceback.format_exc(limit=8),
            )

        await _capture_screenshot(session=session, run_dir=run_dir, evidence=evidence, label="after", enabled=capture)

        recording_path = str(getattr(session, "log_path", "")) or None
        run_result = new_run_result(
            run_id=run_dir.name,
            status=status,
            instance_id=str(getattr(session, "instance_id", "")),
            macro=name,
            args_used=args_used,
            executed=executed,
            skipped=skipped,
            error=error,
            recording_path=recording_path,
        )
        summary = notes or f"Ran macro {name}: status={status}, executed={executed}, skipped={skipped}."
        paths = write_run_bundle(run_dir=run_dir, result=run_result, evidence=evidence.records, summary=summary)

        manifest["latest_run"] = {"run_id": run_dir.name, "path": str(run_dir)}
        write_artifact_manifest(manifest_path, manifest)

        verification_status = "not_configured"
        verification_paths = {}
        critical_points = manifest.get("critical_points", [])
        if verify and critical_points:
            v_res = macro_artifact_verify(name, run_dir.name)
            if v_res.get("ok"):
                verification_status = v_res.get("status", "unknown")
                verification_paths = v_res.get("paths", {})

        with span("octowright.macro.artifact.run") as s:
            set_attrs(s, macro=name, run_id=run_dir.name, verify=verify)
            counter("octowright_macro_artifact_run_total").add(
                1, attributes={"macro": _cap_macro(name), "status": status, "verified": str(bool(critical_points))}
            )
            log.info(
                "octowright.macro.artifact.run.complete",
                artifact_type="macro",
                name=name,
                run_id=run_dir.name,
                status=status,
            )

        return {
            "ok": status == "ok",
            "macro": name,
            "run_id": run_dir.name,
            "summary": summary,
            "verification_status": verification_status,
            "paths": {
                "run_dir": str(run_dir),
                "manifest": str(manifest_path),
                "summary": str(paths["summary"]),
                "evidence": str(paths["evidence"]),
                "result": str(paths["result"]),
                **verification_paths,
            },
        }


async def _capture_screenshot(
    *,
    session: Any,
    run_dir: Path,
    evidence: EvidenceBuilder,
    label: str,
    enabled: bool,
) -> None:
    if not enabled or getattr(session, "page", None) is None:
        return
    screenshot = getattr(session, "screenshot", None)
    if screenshot is None:
        return
    path = run_dir / "screenshots" / f"{label}.png"
    try:
        await screenshot(path)
    except Exception as exc:  # Best-effort evidence must not hide macro results.
        evidence.log_excerpt(path=path, offset=0, preview=f"{exc.__class__.__name__}: {exc}")
        return
    evidence.screenshot(path=path, label=label)


def _safe_existing_manifest_path(store: ArtifactStore, manifest_path: Path) -> Path | None:
    if not manifest_path.exists():
        return None
    try:
        contained_path = store._contained(manifest_path, label="macro artifact manifest")
        contained_path.relative_to(store.root.resolve())
    except (OSError, ValueError):
        return None
    return contained_path


def _manifest_for_plan(
    *,
    name: str,
    macro: dict[str, Any],
    args_used: dict[str, Any],
    missing_args: list[str],
    artifact_dir: Path,
    runs_dir: Path,
    exports_dir: Path,
) -> dict[str, Any]:
    return new_manifest(
        artifact_type="macro",
        name=name,
        source={"type": "macro", "path": str(macro_path(name))},
        parameters=args_used,
        metadata={
            "description": macro.get("description"),
            "action_count": len(macro.get("actions", [])) if isinstance(macro.get("actions"), list) else 0,
            "missing_args": missing_args,
            "paths": {
                "artifact_dir": str(artifact_dir),
                "runs_dir": str(runs_dir),
                "exports_dir": str(exports_dir),
            },
            "ready": not missing_args,
        },
    )


def _merge_existing_manifest(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return manifest
    if not isinstance(existing, dict):
        return manifest
    merged = dict(manifest)
    for key in ("created_at", "latest_run", "exports", "critical_points"):
        if key in existing:
            merged[key] = existing[key]
    raw_existing_metadata = existing.get("metadata")
    existing_metadata = raw_existing_metadata if isinstance(raw_existing_metadata, dict) else {}
    raw_manifest_metadata = manifest.get("metadata")
    manifest_metadata = raw_manifest_metadata if isinstance(raw_manifest_metadata, dict) else {}
    merged["metadata"] = {**existing_metadata, **manifest_metadata}
    return merged


def _compact_manifest(store: ArtifactStore, path: Path) -> dict[str, Any] | None:
    try:
        contained_path = store._contained(path, label="macro artifact manifest")
        contained_path.relative_to(store.root.resolve())
    except (OSError, ValueError):
        return None
    try:
        manifest = json.loads(contained_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(manifest, dict):
        return None
    return {
        "artifact_type": manifest.get("artifact_type"),
        "name": manifest.get("name"),
        "source": manifest.get("source"),
        "parameters": redact_mapping(manifest.get("parameters")),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "latest_run": manifest.get("latest_run"),
        "exports": manifest.get("exports", []),
        "critical_points": manifest.get("critical_points", []),
        "metadata": manifest.get("metadata", {}),
        "path": str(contained_path),
    }


def _all_macro_manifests(store: ArtifactStore) -> list[Path]:
    root = store.root / "macros"
    if not root.exists():
        return []
    return sorted(root.glob("*/artifact.json"))


def _missing_args(macro: dict[str, Any], args: dict[str, Any]) -> list[str]:
    parameters = macro.get("parameters")
    if isinstance(parameters, dict):
        required = [str(key) for key in parameters]
    elif isinstance(parameters, list):
        required = [str(item) for item in parameters]
    else:
        required = []
    return [parameter for parameter in required if parameter not in args]


def macro_artifact_critical_points_get(name: str) -> dict[str, Any]:
    store = ArtifactStore()
    manifest_path = store.macro_manifest_path(name)
    manifest = _compact_manifest(store, manifest_path)
    if not manifest:
        return {"ok": False, "error": "Manifest not found."}
    return {
        "ok": True,
        "critical_points": manifest.get("critical_points", []),
        "paths": {"manifest": str(manifest_path)},
    }


def macro_artifact_critical_points_set(name: str, critical_points: list[dict[str, Any]]) -> dict[str, Any]:
    store = ArtifactStore()
    manifest_path = store.macro_manifest_path(name)
    manifest = _compact_manifest(store, manifest_path)
    if not manifest:
        macro = load_macro(name)
        manifest = _manifest_for_plan(
            name=name,
            macro=macro,
            args_used={},
            missing_args=_missing_args(macro, {}),
            artifact_dir=manifest_path.parent,
            runs_dir=manifest_path.parent / "runs",
            exports_dir=manifest_path.parent / "exports",
        )

    normalized_cps = []
    for i, cp in enumerate(critical_points):
        normalized = dict(cp)
        if "id" not in normalized or not normalized["id"]:
            normalized["id"] = f"CP{i + 1}"
        if "status" not in normalized:
            normalized["status"] = "unknown"
        if "checks" not in normalized:
            normalized["checks"] = []
        normalized_cps.append(normalized)

    manifest["critical_points"] = normalized_cps
    write_artifact_manifest(manifest_path, manifest)
    log.info("octowright.macro.artifact.critical_points.updated", artifact_type="macro", name=name)
    return {"ok": True, "critical_points": normalized_cps}


def macro_artifact_verify(name: str, run_id: str | None = None) -> dict[str, Any]:
    from octowright.artifacts.verification import evaluate_checks

    store = ArtifactStore()
    artifact_dir = store.macro_dir(name)
    manifest_path = artifact_dir / "artifact.json"
    manifest = _compact_manifest(store, manifest_path)
    if not manifest:
        return {"ok": False, "error": "Manifest not found."}

    critical_points = manifest.get("critical_points", [])
    if not critical_points:
        return {"ok": False, "error": "No critical points configured."}

    target_run_id = run_id or (manifest.get("latest_run") or {}).get("run_id")
    if not target_run_id:
        return {"ok": False, "error": "No runs exist to verify."}

    run_dir = artifact_dir / "runs" / target_run_id
    if not (run_dir / "result.json").exists() or not (run_dir / "evidence.json").exists():
        return {"ok": False, "error": "Run bundle incomplete."}

    try:
        import json

        result = json.loads((run_dir / "result.json").read_text("utf-8"))
        evidence_records = json.loads((run_dir / "evidence.json").read_text("utf-8")).get("records", [])
    except Exception as e:
        return {"ok": False, "error": f"Failed reading bundle: {e}"}

    v_res = evaluate_checks("macro", critical_points, result, evidence_records)

    verification_path = run_dir / "verification.json"
    import json

    from octowright._paths import atomic_write_text

    atomic_write_text(verification_path, json.dumps(v_res, indent=2, ensure_ascii=False), encoding="utf-8")

    manifest["critical_points"] = v_res["critical_points"]
    write_artifact_manifest(manifest_path, manifest)

    return {"ok": True, "status": v_res["status"], "paths": {"verification": str(verification_path)}}


def macro_artifact_status(name: str) -> dict[str, Any]:
    store = ArtifactStore()
    manifest_path = store.macro_manifest_path(name)
    manifest = _compact_manifest(store, manifest_path)
    if not manifest:
        return {"ok": False, "error": "Manifest not found."}

    critical_points = manifest.get("critical_points", [])
    passed = sum(1 for cp in critical_points if cp.get("status") == "passed")
    failed = sum(1 for cp in critical_points if cp.get("status") == "failed")

    return {
        "ok": True,
        "latest_run": manifest.get("latest_run"),
        "verification_status": "passed"
        if (critical_points and passed == len(critical_points))
        else "failed"
        if failed > 0
        else "unknown",
        "counts": {"total": len(critical_points), "passed": passed, "failed": failed},
        "paths": {"manifest": str(manifest_path)},
    }
