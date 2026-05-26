# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from octowright.artifacts.redaction import redact_mapping

ARTIFACT_VERSION = 1


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_manifest(
    *,
    artifact_type: str,
    name: str,
    source: dict[str, Any],
    parameters: dict[str, Any] | None = None,
    exports: list[dict[str, Any]] | None = None,
    critical_points: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stamp = now_iso()
    return {
        "artifact_version": ARTIFACT_VERSION,
        "artifact_type": artifact_type,
        "name": name,
        "source": copy.deepcopy(source),
        "parameters": redact_mapping(parameters),
        "created_at": stamp,
        "updated_at": stamp,
        "latest_run": None,
        "exports": copy.deepcopy(exports) if exports is not None else [],
        "critical_points": copy.deepcopy(critical_points) if critical_points is not None else [],
        "metadata": copy.deepcopy(metadata) if metadata is not None else {},
    }


def new_run_result(
    *,
    run_id: str,
    status: str,
    instance_id: str,
    macro: str,
    args_used: dict[str, Any] | None,
    executed: int,
    skipped: int,
    error: str | None,
    recording_path: str | None,
) -> dict[str, Any]:
    stamp = now_iso()
    return {
        "run_id": run_id,
        "status": status,
        "started_at": stamp,
        "ended_at": stamp,
        "instance_id": instance_id,
        "macro": macro,
        "args_used": redact_mapping(args_used),
        "executed": executed,
        "skipped": skipped,
        "error": error,
        "recording_path": recording_path,
        "evidence_path": None,
    }
