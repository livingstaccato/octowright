# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Shared helpers for the macro-artifact record tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Fields stamped with the wall clock; dropped so whole records compare exactly.
_CLOCK_KEYS = frozenset({"ts", "created_at", "updated_at", "started_at", "ended_at"})


def stable(value: Any, tmp_path: Path) -> Any:
    """*value* without clock stamps, with *tmp_path* spelled ``<tmp>``."""
    if isinstance(value, dict):
        return {key: stable(item, tmp_path) for key, item in value.items() if key not in _CLOCK_KEYS}
    if isinstance(value, list):
        return [stable(item, tmp_path) for item in value]
    if isinstance(value, str):
        return value.replace(str(tmp_path), "<tmp>")
    return value


def native(*parts: str) -> str:
    """*parts* joined and spelled the way this OS spells a path.

    The product returns ``str(Path)``, so ``\\`` separates on Windows; an
    expected value built here stays an exact comparison on every OS.
    """
    return str(Path(*parts))


def read_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def artifact_dir(tmp_path: Path, name: str) -> Path:
    return tmp_path / "recordings" / "artifacts" / "macros" / name


def exercise_non_ascii(storage: Any, macro_artifacts: Any, root: Path) -> dict[str, Any]:
    """Every artifact-store read and write, over documents holding non-ASCII text.

    Returns what each step reported, with *root* spelled ``<tmp>``, so a run
    under one locale can be compared with a run under another.
    """
    storage.write_macro(
        name="cafe",
        macro={
            "name": "cafe",
            "description": "Crème brûlée",
            "parameters": [],
            "actions": [{"action": "navigate", "url": "https://example.test/crème"}],
        },
    )
    macro_artifacts.plan_macro_artifact("cafe")
    checks = [{"type": "result_status", "status": "ok"}]
    macro_artifacts.macro_artifact_critical_points_set(
        "cafe", [{"id": "cp1", "description": "Crème", "checks": checks}]
    )
    replanned = macro_artifacts.plan_macro_artifact("cafe")
    run = artifact_dir(root, "cafe") / "runs" / "run_0001"
    run.mkdir(parents=True)
    (run / "result.json").write_text(json.dumps({"status": "ok", "summary": "brûlée"}, ensure_ascii=False), "utf-8")
    evidence = {"records": [{"id": "ev_001", "type": "note", "preview": "crème"}]}
    (run / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False), "utf-8")
    verified = macro_artifacts.macro_artifact_verify("cafe", "run_0001")
    recording = root / "recordings" / "r.jsonl"
    recording.write_text(json.dumps({"action": "fill", "value": "Zoë"}, ensure_ascii=False) + "\n", "utf-8")
    digest = macro_artifacts.macro_digest(recording_path=str(recording))
    listed = macro_artifacts.list_macro_artifacts()
    report = {
        "replanned": replanned["ok"],
        "verified": verified,
        "verification": json.loads((run / "verification.json").read_text("utf-8"))["status"],
        "digest": digest["summary"],
        "listed": [[item["name"], item["metadata"]["description"]] for item in listed["artifacts"]],
    }
    result: dict[str, Any] = stable(report, root)
    return result
