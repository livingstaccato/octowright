# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from octowright.plugins.errors import SessionIdInUseError
from octowright.plugins.session_launch import PluginContext
from octowright.recorder import Recorder


@dataclass
class _Record:
    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    url: str | None
    recorder: Recorder
    log_path: Path
    protected: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _ctx(tmp_path: Path, *, in_use: set[str] | None = None) -> PluginContext:
    taken = in_use or set()
    return PluginContext(
        kind="refkind",
        recordings_dir=tmp_path,
        id_in_use=lambda instance_id: instance_id in taken,
    )


def _actions(path: Path) -> list[str]:
    return [json.loads(line)["action"] for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_opening_row_is_written_with_kind_label_and_profile(tmp_path):
    ctx = _ctx(tmp_path)
    async with ctx.begin_session(instance_id="abc123", label="demo", profile="tanuki") as launch:
        record = _Record("abc123", "refkind", "demo", "tanuki", None, launch.recorder, launch.log_path)
        result = launch.commit(record)

    assert result["instance_id"] == "abc123"
    assert result["kind"] == "refkind"
    rows = [json.loads(line) for line in launch.log_path.read_text().splitlines() if line.strip()]
    assert rows[0]["action"] == "session_start"
    assert rows[0]["kind"] == "refkind"
    assert rows[0]["label"] == "demo"
    assert rows[0]["profile"] == "tanuki"


@pytest.mark.asyncio
async def test_failed_launch_discards_an_opening_row_only_recording(tmp_path):
    ctx = _ctx(tmp_path)
    log_path: Path | None = None
    with pytest.raises(RuntimeError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            log_path = launch.log_path
            raise RuntimeError("connector refused")

    assert log_path is not None
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_failed_launch_keeps_a_partial_recording(tmp_path):
    ctx = _ctx(tmp_path)
    log_path: Path | None = None
    with pytest.raises(RuntimeError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            log_path = launch.log_path
            launch.recorder.record("terminal_output", data="boot")
            raise RuntimeError("died mid-boot")

    assert log_path is not None
    assert _actions(log_path) == ["session_start", "terminal_output"]


@pytest.mark.asyncio
async def test_cancellation_behaves_as_a_failed_launch(tmp_path):
    import asyncio

    ctx = _ctx(tmp_path)
    log_path: Path | None = None
    with pytest.raises(asyncio.CancelledError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            log_path = launch.log_path
            raise asyncio.CancelledError

    assert log_path is not None
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_exiting_without_commit_is_a_failure(tmp_path):
    ctx = _ctx(tmp_path)
    async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
        log_path = launch.log_path
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_commit_refuses_a_mismatched_record(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="does not match the transaction"):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            other = Recorder(tmp_path / "other.jsonl")
            record = _Record("abc123", "refkind", None, None, None, other, launch.log_path)
            launch.commit(record)


@pytest.mark.asyncio
async def test_commit_enforces_cross_pool_id_uniqueness(tmp_path):
    ctx = _ctx(tmp_path, in_use={"abc123"})
    with pytest.raises(SessionIdInUseError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            record = _Record("abc123", "refkind", None, None, None, launch.recorder, launch.log_path)
            launch.commit(record)
