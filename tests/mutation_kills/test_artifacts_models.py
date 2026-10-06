# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Whole-record shape of ``new_manifest`` and ``new_run_result``.

``tests/test_artifacts_models.py`` pins aliasing and defaults; nothing pinned the
timestamp keys or the run result's ``evidence_path`` placeholder, so they could
be renamed (or the stamp dropped to ``None``) with the suite green. Readers index
``created_at``/``updated_at``/``started_at``/``ended_at`` directly, and
``write_run_bundle`` overwrites ``evidence_path`` -- a renamed key would leave a
stray ``None`` field beside it.
"""

from __future__ import annotations

import re
from typing import Any

from octowright.artifacts.models import ARTIFACT_VERSION, new_manifest, new_run_result

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def _without_stamps(record: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Check the timestamps are one well-formed instant, then drop them."""
    values = {record[k] for k in keys}
    assert len(values) == 1, values
    assert _ISO_Z.match(str(next(iter(values)))), values
    return {k: v for k, v in record.items() if k not in keys}


def test_a_new_manifest_is_exactly_the_documented_record() -> None:
    manifest = new_manifest(artifact_type="macro", name="login", source={"macro": "login"})

    assert _without_stamps(manifest, "created_at", "updated_at") == {
        "artifact_version": ARTIFACT_VERSION,
        "artifact_type": "macro",
        "name": "login",
        "source": {"macro": "login"},
        "parameters": {},
        "latest_run": None,
        "exports": [],
        "critical_points": [],
        "metadata": {},
    }


def test_a_new_run_result_is_exactly_the_documented_record() -> None:
    result = new_run_result(
        run_id="run-1",
        status="ok",
        instance_id="b1",
        macro="login",
        args_used={"password": "zebrin4-not-real", "sku": "42"},  # pragma: allowlist secret
        executed=3,
        skipped=1,
        error=None,
        recording_path="/tmp/rec.jsonl",
    )

    assert _without_stamps(result, "started_at", "ended_at") == {
        "run_id": "run-1",
        "status": "ok",
        "instance_id": "b1",
        "macro": "login",
        "args_used": {"password": "<redacted>", "sku": "42"},
        "executed": 3,
        "skipped": 1,
        "error": None,
        "recording_path": "/tmp/rec.jsonl",
        "evidence_path": None,
    }
