# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``run_id`` reaches the artifact store from an MCP tool argument.

Joined raw, ``../..`` escaped the recordings root and ``macro_artifact_verify``
then wrote ``verification.json`` at the traversal target.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.artifacts.paths import ArtifactStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ArtifactStore:
    from octowright import defaults

    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path / "recordings")
    return ArtifactStore()


def test_valid_run_id_resolves(store: ArtifactStore) -> None:
    art = store.macro_dir("login")
    assert store.existing_run_dir(art, "run_0001") == (art / "runs" / "run_0001")


@pytest.mark.parametrize(
    "run_id",
    [
        "../../../../etc",
        "run_0001/../../../..",
        "..",
        "/etc/passwd",
        "run_1",  # right prefix, wrong shape
        "run_00001",
        "",
        ".",
        "run_0001\x00",
    ],
)
def test_traversal_and_malformed_run_ids_are_refused(store: ArtifactStore, run_id: str) -> None:
    art = store.macro_dir("login")
    with pytest.raises(ValueError):
        store.existing_run_dir(art, run_id)


def test_verify_refuses_a_traversal_instead_of_writing_outside(store: ArtifactStore, tmp_path: Path) -> None:
    """End-to-end: the tool returns an error and plants no file outside the root."""
    from octowright.macros.artifacts import macro_artifact_verify

    art = store.macro_dir("login")
    (art / "artifact.json").write_text(
        json.dumps({"name": "login", "critical_points": [{"id": "c1", "type": "url_contains"}]})
    )

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "result.json").write_text("{}")
    (outside / "evidence.json").write_text(json.dumps({"records": []}))

    import os

    payload = os.path.relpath(outside, art / "runs")
    res = macro_artifact_verify("login", run_id=payload)

    assert res["ok"] is False
    assert not (outside / "verification.json").exists()
