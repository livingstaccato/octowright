# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro artifact's ``artifact.json``: building, merging, reading and listing it.

The manifest helpers behind ``macros.artifacts``' plan, run, export, listing
and critical-point entry points, including the canonical shape a stored
critical point is filled out to. None of them writes: every write of a
manifest stays in ``macros.artifacts`` (through ``write_artifact_manifest``),
where the durable-write sink inventory expects it. Reads are contained to the
artifact store, and parameters are redacted through the macro's own privacy
view -- ``parameter_specs`` included -- as ``macro_run`` resolves it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.artifacts.models import new_manifest
from octowright.artifacts.paths import ArtifactStore
from octowright.macros.parameter_specs import macro_privacy
from octowright.macros.privacy import MacroArgPrivacy
from octowright.macros.storage import load_macro, macro_path

# The logger ``macros.artifacts`` has always used, so the listing fallback
# logs under the same name it did before it moved here.
log = get_logger("octowright.artifacts.verification")


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
        # The macro's own view, as macro_run uses: not the name-only one.
        parameters=_privacy(macro).redact(args_used),
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


def _stored_manifest(store: ArtifactStore, path: Path) -> tuple[Path, dict[str, Any]] | None:
    """The manifest as it is on disk, and its contained path; what a read-modify-write writes back."""
    try:
        contained_path = store._contained(path, label="macro artifact manifest")
        contained_path.relative_to(store.root.resolve())
    except (OSError, ValueError):
        return None
    try:
        manifest = json.loads(contained_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return (contained_path, manifest) if isinstance(manifest, dict) else None


def _writable_manifest(store: ArtifactStore, path: Path) -> dict[str, Any] | None:
    """`_stored_manifest` with its parameters redacted as a listing shows them (one may predate that redaction)."""
    stored = _stored_manifest(store, path)
    if stored is None:
        return None
    manifest = stored[1]
    manifest["parameters"] = _listing_privacy(manifest.get("name")).redact(manifest.get("parameters") or {})
    return manifest


def _compact_manifest(store: ArtifactStore, path: Path) -> dict[str, Any] | None:
    """The manifest for a response: never written back, since it drops ``artifact_version`` and adds ``path``."""
    stored = _stored_manifest(store, path)
    if stored is None:
        return None
    contained_path, manifest = stored
    return {
        "artifact_type": manifest.get("artifact_type"),
        "name": manifest.get("name"),
        "source": manifest.get("source"),
        "parameters": _listing_privacy(manifest.get("name")).redact(manifest.get("parameters") or {}),
        "created_at": manifest.get("created_at"),
        "updated_at": manifest.get("updated_at"),
        "latest_run": manifest.get("latest_run"),
        "exports": manifest.get("exports", []),
        "critical_points": manifest.get("critical_points", []),
        "metadata": manifest.get("metadata", {}),
        "path": str(contained_path),
    }


def _privacy(macro: dict[str, Any]) -> MacroArgPrivacy:
    """The macro's own view, ``parameter_specs`` included, as macro_run resolves it."""
    return macro_privacy(macro)


def _listing_privacy(name: Any) -> MacroArgPrivacy:
    """The macro's view for a stored manifest, which may predate positional redaction.

    A macro that is gone or unreadable falls back to name-only: the manifest
    is still listed, and a value written by a current octowright is already
    redacted on disk.
    """
    try:
        return _privacy(load_macro(str(name)))
    except Exception:  # A listing must not fail over one unreadable macro.
        log.debug("octowright.artifacts.listing_privacy_fallback", macro=str(name)[:100])
        return MacroArgPrivacy()


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


def _normalize_critical_point(point: dict[str, Any], index: int) -> dict[str, Any]:
    """Fill a critical point out to the shape the design doc calls canonical.

    Filling only `id`, `status` and `checks` leaves the other four slots absent
    from stored critical points. The visible cost lands in the
    run report, which renders `cp.get("description", "Unknown")` -- a claim
    with no description came back as `### CP1: Unknown`, naming a critical
    point while saying nothing about what it asserts. `evidence`,
    `last_verified_run` and `notes` were likewise missing rather than empty,
    so a reader had to distinguish "not set" from "not yet verified" by their
    absence.

    Supplied values always win; this only fills what the caller left out.
    """
    normalized = dict(point)
    if not normalized.get("id"):
        normalized["id"] = f"CP{index + 1}"
    if not normalized.get("description"):
        normalized["description"] = "Unknown claim"
    normalized.setdefault("status", "unknown")
    normalized.setdefault("checks", [])
    normalized.setdefault("evidence", [])
    normalized.setdefault("last_verified_run", None)
    normalized.setdefault("notes", None)
    return normalized
