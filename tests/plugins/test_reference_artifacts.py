# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from octowright.plugins.artifacts import read_registered_artifacts
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.plugin import plugin


@pytest.fixture
def pool(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    return plugin.create_pool(ctx), tmp_path


async def test_reference_pool_writes_and_commits_a_transcript(pool):
    ref_pool, root = pool
    launched = await ref_pool.launch(label="demo")
    artifact_id = ref_pool.write_transcript(launched["instance_id"], "line one\nline two\n")
    assert artifact_id == "transcript"

    found = read_registered_artifacts(Path(launched["log_path"]), root)
    assert [a.artifact_id for a in found] == ["transcript"]
    assert found[0].path.read_text() == "line one\nline two\n"
    assert found[0].mime_type == "text/plain"


async def test_the_transcript_lives_inside_the_recordings_root(pool):
    ref_pool, root = pool
    launched = await ref_pool.launch()
    ref_pool.write_transcript(launched["instance_id"], "x")
    found = read_registered_artifacts(Path(launched["log_path"]), root)
    assert found[0].path.resolve().is_relative_to(root.resolve())


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX file-mode bits: secure_artifact_tree's 0700 chmod is best-effort and "
    "a no-op on Windows (NTFS ACLs, not mode bits), so this assertion does not apply.",
)
async def test_the_artifact_directory_is_owner_only(pool, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS_PRIVATE", "1")
    ref_pool, root = pool
    launched = await ref_pool.launch()
    ref_pool.write_transcript(launched["instance_id"], "x")
    found = read_registered_artifacts(Path(launched["log_path"]), root)
    assert stat.S_IMODE(found[0].path.parent.stat().st_mode) == 0o700


async def test_writing_a_transcript_for_an_unknown_session_raises(pool):
    ref_pool, _ = pool
    with pytest.raises(KeyError):
        ref_pool.write_transcript("nosuchid", "x")
