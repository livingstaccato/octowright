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
from starlette.requests import Request

from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state


@dataclass
class _Session:
    instance_id: str
    kind: str = "refkind"
    label: str | None = None
    profile: str | None = None
    url: str | None = None
    log_path: Path = Path("/tmp/x.jsonl")
    protected: bool = False
    started_at: str = "2026-08-23T00:00:00Z"
    recorder: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Pool:
    sessions: dict[str, _Session] = field(default_factory=dict)

    def maybe_get(self, instance_id: str) -> _Session | None:
        return self.sessions.get(instance_id)

    def iter_sessions(self):
        return iter(list(self.sessions.values()))


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names: frozenset[str] = frozenset()
    tool_module = None
    profile_name = None
    frontend = None

    def create_pool(self, ctx: Any) -> Any:
        raise AssertionError("not used")

    def create_scenario_adapter(self, pool: Any) -> None:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {"id": session.instance_id, "kind": session.kind, "refkind_specific": True}


@pytest.fixture
def registered():
    """Install a one-plugin registry and restore the real one afterwards."""
    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _Pool({"refsess01": _Session("refsess01")})
    reg.register(_Descriptor(), pool=pool, adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        yield reg, pool
    finally:
        plugin_state.set_registry(original)


def test_state_exposes_the_live_registry(registered):
    from octowright.http import state

    reg, _ = registered
    assert state.plugin_registry is reg


def test_iter_plugin_sessions_spans_every_registered_pool(registered):
    from octowright.http.routes._session_kinds import iter_plugin_sessions

    assert [s.instance_id for s in iter_plugin_sessions()] == ["refsess01"]


def test_find_plugin_session_returns_kind_and_session(registered):
    from octowright.http.routes._session_kinds import find_plugin_session

    found = find_plugin_session("refsess01")
    assert found is not None
    kind, session = found
    assert kind == "refkind"
    assert session.instance_id == "refsess01"
    assert find_plugin_session("nope") is None


def test_no_plugins_registered_is_an_empty_iteration():
    from octowright.http.routes._session_kinds import find_plugin_session, iter_plugin_sessions

    assert list(iter_plugin_sessions()) == []
    assert find_plugin_session("refsess01") is None


def test_plugin_session_detail_uses_the_descriptor(registered):
    from octowright.http.routes._session_kinds import plugin_session_detail

    _, pool = registered
    detail = plugin_session_detail("refkind", pool.sessions["refsess01"])
    assert detail["refkind_specific"] is True
    assert detail["kind"] == "refkind"
    assert detail["artifacts"] == []


def test_plugin_session_detail_includes_committed_artifacts(registered, tmp_path, monkeypatch):
    from octowright.http import state as http_state
    from octowright.http.routes._session_kinds import plugin_session_detail
    from octowright.plugins.artifacts import reserve_artifact
    from octowright.recorder import Recorder

    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("hello")
    handle.commit(mime_type="text/plain")
    recorder.close()

    _, pool = registered
    session = pool.sessions["refsess01"]
    session.log_path = log_path
    monkeypatch.setattr(http_state, "RECORDINGS_DIR", tmp_path)

    detail = plugin_session_detail("refkind", session)
    assert [a["artifact_id"] for a in detail["artifacts"]] == ["transcript"]
    assert detail["artifacts"][0]["mime_type"] == "text/plain"
    # The absolute path is deliberately NOT exposed to the dashboard.
    assert "path" not in detail["artifacts"][0]


def test_a_descriptor_that_raises_yields_a_degraded_detail(registered):
    from octowright.http.routes._session_kinds import plugin_session_detail

    class _Boom(_Descriptor):
        def session_detail(self, session: Any) -> dict[str, Any]:
            raise RuntimeError("plugin detail exploded")

    reg, pool = registered
    reg.register(_Boom(), pool=pool, adapter=None, discovered=None)
    detail = plugin_session_detail("refkind", pool.sessions["refsess01"])
    assert detail["id"] == "refsess01"
    assert detail["kind"] == "refkind"
    assert "detail_error" in detail


def test_a_vanishing_artifact_file_degrades_that_entry_not_the_whole_response(registered, tmp_path, monkeypatch):
    """A committed artifact can be deleted between read_registered_artifacts's
    own existence check and plugin_session_detail's stat() call (a concurrent
    recordings_cleanup, or a plugin rotating its own artifact). That race must
    drop the one entry, not 500 the whole dashboard detail response.
    """
    from octowright.http import state as http_state
    from octowright.http.routes._session_kinds import plugin_session_detail
    from octowright.plugins import artifacts as artifacts_module
    from octowright.plugins.artifacts import reserve_artifact
    from octowright.recorder import Recorder

    log_path = tmp_path / "20260823T000000Z-refkind-refsess01.jsonl"
    recorder = Recorder(log_path)
    recorder.record_control("session_start", kind="refkind", label=None, profile=None)
    handle = reserve_artifact(
        recorder=recorder, instance_id="refsess01", recordings_dir=tmp_path, artifact_id="transcript", suffix=".txt"
    )
    handle.path.write_text("hello")
    handle.commit(mime_type="text/plain")
    recorder.close()

    _, pool = registered
    session = pool.sessions["refsess01"]
    session.log_path = log_path
    monkeypatch.setattr(http_state, "RECORDINGS_DIR", tmp_path)

    real_read_registered_artifacts = artifacts_module.read_registered_artifacts

    def _read_then_vanish(log_path: Path, recordings_dir: Path):
        found = real_read_registered_artifacts(log_path, recordings_dir)
        for artifact in found:
            artifact.path.unlink()
        return found

    monkeypatch.setattr(artifacts_module, "read_registered_artifacts", _read_then_vanish)

    detail = plugin_session_detail("refkind", session)
    assert detail["artifacts"] == []


async def test_close_plugin_session_closes_and_reports(registered):
    from octowright.http.routes._session_kinds import close_plugin_session

    _, pool = registered
    closed: list[str] = []

    async def _close(instance_id: str, *, force: bool = False):
        closed.append(instance_id)
        pool.sessions.pop(instance_id)
        return {"instance_id": instance_id, "kind": "refkind", "closed": True}

    pool.close = _close  # type: ignore[attr-defined]
    result = await close_plugin_session("refsess01", force=False)
    assert result == {"instance_id": "refsess01", "kind": "refkind", "closed": True}
    assert closed == ["refsess01"]


async def test_close_plugin_session_returns_none_for_an_unknown_id(registered):
    from octowright.http.routes._session_kinds import close_plugin_session

    assert await close_plugin_session("nope", force=False) is None


async def test_protected_close_propagates_for_the_409_mapping(registered):
    from octowright.http.routes._session_kinds import close_plugin_session
    from octowright.plugins.errors import ProtectedSessionCloseError

    _, pool = registered

    async def _close(instance_id: str, *, force: bool = False):
        if not force:
            raise ProtectedSessionCloseError(f"refkind {instance_id!r} is protected; pass force=True to close it")
        return {"instance_id": instance_id, "closed": True}

    pool.close = _close  # type: ignore[attr-defined]
    with pytest.raises(ProtectedSessionCloseError):
        await close_plugin_session("refsess01", force=False)
    assert await close_plugin_session("refsess01", force=True) == {"instance_id": "refsess01", "closed": True}


# ---------------------------------------------------------------------------
# One bad plugin pool must not 500 GET /api/sessions (final-fixes finding 1).
# ---------------------------------------------------------------------------


class _BoomDescriptor(_Descriptor):
    kind = "boomkind"


class _RaisingPool:
    """A pool whose iteration explodes -- the reviewer's reproduction case."""

    def iter_sessions(self):
        raise RuntimeError("plugin pool exploded")

    def maybe_get(self, instance_id: str) -> Any:
        return None


def _get_sessions_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/sessions",
            "headers": [],
            "query_string": b"",
            "path_params": {},
        }
    )


def test_iter_plugin_sessions_isolates_a_raising_pool_from_later_pools():
    """A pool that raises must not stop LATER pools from being listed.

    Registered in raise-then-good order specifically so a naive
    ``for pool in pools: yield from pool.iter_sessions()`` (no per-pool guard)
    would abort the whole generator on the first pool and never reach the
    second -- the isolation this pins.
    """
    from octowright.http.routes._session_kinds import iter_plugin_sessions

    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_BoomDescriptor(), pool=_RaisingPool(), adapter=None, discovered=None)
    reg.register(_Descriptor(), pool=_Pool({"refsess01": _Session("refsess01")}), adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        assert [s.instance_id for s in iter_plugin_sessions()] == ["refsess01"]
    finally:
        plugin_state.set_registry(original)


async def test_list_sessions_route_survives_a_raising_plugin_pool():
    """The reviewer's reproduction: GET /api/sessions must not 500."""
    from octowright.http.routes.sessions import list_sessions

    original = plugin_state.registry()
    reg = PluginRegistry()
    reg.register(_BoomDescriptor(), pool=_RaisingPool(), adapter=None, discovered=None)
    reg.register(_Descriptor(), pool=_Pool({"refsess01": _Session("refsess01")}), adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        resp = await list_sessions(_get_sessions_request())
    finally:
        plugin_state.set_registry(original)

    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert any(s["id"] == "refsess01" for s in body["live"])


class _AttributeLackingSession:
    """Has an instance_id but nothing else -- ``_live_summary`` reads
    ``.log_path``/``.kind``/``.label``/``.profile``/``.url`` without
    ``getattr``, so this raises ``AttributeError`` inside the summariser."""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id


async def test_list_sessions_route_survives_a_session_missing_attributes():
    """A nonconforming session object must not take the rest of the live list down."""
    from octowright.http.routes.sessions import list_sessions

    original = plugin_state.registry()
    reg = PluginRegistry()
    pool = _Pool(
        {
            "good01": _Session("good01"),
            "bad01": _AttributeLackingSession("bad01"),  # type: ignore[dict-item]
        }
    )
    reg.register(_Descriptor(), pool=pool, adapter=None, discovered=None)
    plugin_state.set_registry(reg)
    try:
        resp = await list_sessions(_get_sessions_request())
    finally:
        plugin_state.set_registry(original)

    assert resp.status_code == 200
    body = json.loads(resp.body)
    live_ids = {s["id"] for s in body["live"]}
    assert "good01" in live_ids
    assert "bad01" not in live_ids
