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

from octowright.plugins.errors import ControlBudgetExceededError, SessionIdInUseError
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from octowright.recorder import CONTROL_BUDGET_BYTES, Recorder


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

    def _probe(instance_id: str, *, exclude_kind: str | None = None) -> bool:
        return instance_id in taken

    return PluginContext(kind="refkind", recordings_dir=tmp_path, id_in_use=_probe)


class _StubDescriptor:
    """Just enough descriptor for ``PluginRegistry.register``."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.display_name = kind
        self.plugin_api_version = 1
        self.tool_names = frozenset({f"{kind}_launch"})
        self.tool_module = None
        self.profile_name = None
        self.frontend = None


class _EagerPool:
    """A pool that holds its session record — populated before ``commit``."""

    def __init__(self) -> None:
        self.sessions: dict[str, Any] = {}

    def maybe_get(self, instance_id: str) -> Any:
        return self.sessions.get(instance_id)

    def iter_sessions(self):
        return iter(list(self.sessions.values()))


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


@pytest.mark.asyncio
async def test_traversing_instance_id_is_refused_before_anything_is_created(tmp_path):
    # ``new_log_path`` sanitizes only the label, so a plugin-supplied id lands
    # in the filename raw. Without containment, Recorder's mkdir(parents=True)
    # materializes the traversal.
    root = tmp_path / "recordings"
    root.mkdir()
    ctx = PluginContext(
        kind="refkind",
        recordings_dir=root,
        id_in_use=lambda instance_id, *, exclude_kind=None: False,
    )
    escaped = tmp_path / "escaped"

    # The id's SYNTAX gate rejects this before the path-containment guard is
    # reached: a traversing id necessarily contains a separator, and
    # INSTANCE_ID_RE admits only ``[a-z0-9_]``. Containment remains behind it as
    # defense in depth for the composed path (``label`` still reaches
    # ``new_log_path``'s own sanitizer), but it is no longer reachable through a
    # plugin-supplied id, which is the stronger arrangement.
    with pytest.raises(ValueError, match="must match"):
        async with ctx.begin_session(instance_id="ssh:host/../../escaped", label=None, profile=None):
            pass  # pragma: no cover - the guard raises before the body runs

    assert not escaped.exists()
    assert list(root.iterdir()) == []


@pytest.mark.asyncio
async def test_a_hyphenated_instance_id_is_refused(tmp_path):
    # A hyphen is not a path hazard -- it is a PARSING hazard. Recording names
    # are ``{stamp}-{kind}-{instance_id}[-{label}]`` and readers recover the id
    # as ``stem.split("-")[2]``, so a hyphenated id parses back as a truncated
    # token and a request naming a different session could resolve to this one's
    # recording. Refused at the point core composes the name.
    root = tmp_path / "recordings"
    root.mkdir()
    ctx = PluginContext(
        kind="refkind",
        recordings_dir=root,
        id_in_use=lambda instance_id, *, exclude_kind=None: False,
    )

    with pytest.raises(ValueError, match="must match"):
        async with ctx.begin_session(instance_id="foo-bar", label=None, profile=None):
            pass  # pragma: no cover - the guard raises before the body runs

    assert list(root.iterdir()) == [], "nothing may be created for a refused id"


@pytest.mark.asyncio
async def test_an_ordinary_hex_instance_id_still_launches(tmp_path):
    # uuid4().hex[:12] can start with a digit, so the id must NOT be run
    # through the plugin-name pattern — only through containment.
    ctx = _ctx(tmp_path)
    async with ctx.begin_session(instance_id="0f3ab19c22d4", label=None, profile=None) as launch:
        record = _Record("0f3ab19c22d4", "refkind", None, None, None, launch.recorder, launch.log_path)
        result = launch.commit(record)
    assert result["instance_id"] == "0f3ab19c22d4"
    assert launch.log_path.exists()


@pytest.mark.asyncio
async def test_a_failed_opening_row_leaves_no_orphan_recording(tmp_path):
    # The opening-row write is inside the guard: an oversized ``extra`` blows
    # the control budget, and the empty file it created must not survive.
    ctx = _ctx(tmp_path)
    with pytest.raises(ControlBudgetExceededError):
        async with ctx.begin_session(
            instance_id="abc123",
            label=None,
            profile=None,
            extra={"blob": "x" * (CONTROL_BUDGET_BYTES + 1)},
        ):
            pass  # pragma: no cover - the opening row raises before the body runs

    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_commit_ignores_the_launching_plugins_own_pool(tmp_path):
    # Registering the session before committing is the natural order, and the
    # spec says core probes every *other* pool. Without the exclusion this is
    # a spurious SessionIdInUseError that discards a real recording.
    registry = PluginRegistry()
    pool = _EagerPool()
    registry.register(_StubDescriptor("refkind"), pool=pool, adapter=None, discovered=None)
    ctx = PluginContext(kind="refkind", recordings_dir=tmp_path, id_in_use=registry.id_in_use)

    async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
        record = _Record("abc123", "refkind", None, None, None, launch.recorder, launch.log_path)
        pool.sessions["abc123"] = record
        result = launch.commit(record)

    assert result["instance_id"] == "abc123"


@pytest.mark.asyncio
async def test_commit_still_refuses_an_id_held_by_another_kinds_pool(tmp_path):
    registry = PluginRegistry()
    other = _EagerPool()
    other.sessions["abc123"] = object()
    registry.register(_StubDescriptor("otherkind"), pool=other, adapter=None, discovered=None)
    ctx = PluginContext(kind="refkind", recordings_dir=tmp_path, id_in_use=registry.id_in_use)

    with pytest.raises(SessionIdInUseError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            record = _Record("abc123", "refkind", None, None, None, launch.recorder, launch.log_path)
            launch.commit(record)


@pytest.mark.asyncio
async def test_context_artifact_reserves_and_commits_through_a_real_session(tmp_path):
    # PluginContext.artifact() is the wired integration point a plugin
    # actually calls; exercising reserve_artifact() alone (as
    # tests/plugins/test_artifacts.py does) proves the module works but not
    # that PluginContext threads session.recorder / session.instance_id into
    # it correctly -- an attribute-name typo or argument-order mistake there
    # would pass every other test in this file and still be broken.
    ctx = _ctx(tmp_path)
    async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
        record = _Record("abc123", "refkind", None, None, None, launch.recorder, launch.log_path)
        handle = ctx.artifact(record, "transcript", ".txt")
        handle.path.write_text("hello")
        handle.commit(mime_type="text/plain")
        launch.commit(record)

    rows = [json.loads(line) for line in launch.log_path.read_text().splitlines() if line.strip()]
    row = [r for r in rows if r["action"] == "artifact_registered"][-1]
    assert row["artifact_id"] == "transcript"
    assert row["mime_type"] == "text/plain"
    assert (tmp_path / row["path"]).resolve() == handle.path.resolve()
