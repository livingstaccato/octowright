# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTTP debugger sidecar — write-endpoint tests.

Covers POST /api/sessions, DELETE /api/sessions/{id},
POST /api/sessions/{id}/navigate, POST /api/scenarios/{name}/start,
DELETE /api/scenarios/{id}, POST /api/scenarios/{id}/run_macro.

These tests stub ``_state.pool`` and ``_state.scenario_pool`` with simple
in-memory fakes — no real Playwright is started — so that endpoint behaviour
can be exercised hermetically. The sibling subagent owns ``test_pool*.py``;
this file deliberately stays out of pool internals.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, call

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http import state as _http_state
from octowright.http.routes import scenarios as scenario_routes
from octowright.http.routes import sessions as session_routes
from octowright.server import _state

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every RECORDINGS_DIR consumer in ``http_server`` at a fresh tmp dir."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    return rec


class _FakePool:
    """Minimal stand-in for ``BrowserPool`` for write-endpoint tests.

    Records every call so tests can assert kwargs were forwarded correctly.
    Sessions are SimpleNamespaces stored under ``_sessions`` while public
    accessors mirror the real pool API.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SimpleNamespace] = {}
        self.launch_calls: list[dict[str, Any]] = []
        self.close_calls: list[str] = []
        self.launch_result: dict[str, Any] | None = None
        self.close_result: dict[str, Any] | None = None
        self.launch_raises: BaseException | None = None
        self.next_instance_id: str = "fakeinst0001"

    async def launch(self, **kwargs: Any) -> dict[str, Any]:
        self.launch_calls.append(kwargs)
        if self.launch_raises is not None:
            raise self.launch_raises
        if self.launch_result is not None:
            iid = self.launch_result["instance_id"]
            self._sessions[iid] = SimpleNamespace(instance_id=iid)
            return self.launch_result
        iid = self.next_instance_id
        result = {
            "instance_id": iid,
            "kind": kwargs["kind"],
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": f"/tmp/{iid}.jsonl",
            "record_video": kwargs.get("record_video", False),
            "trace": kwargs.get("trace", False),
        }
        self._sessions[iid] = SimpleNamespace(instance_id=iid)
        return result

    async def close(self, instance_id: str) -> dict[str, Any]:
        self.close_calls.append(instance_id)
        self._sessions.pop(instance_id, None)
        if self.close_result is not None:
            return self.close_result
        return {
            "closed": True,
            "log_path": f"/tmp/{instance_id}.jsonl",
            "video_path": None,
            "trace_path": None,
        }

    def get(self, instance_id: str) -> SimpleNamespace:
        return self._sessions[instance_id]

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> tuple[SimpleNamespace, ...]:
        return tuple(self._sessions.values())


class _FakeSession:
    """Minimal stand-in for ``BrowserSession`` exposing just ``navigate``."""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self.navigate_calls: list[str] = []
        self.navigate_raises: BaseException | None = None

    async def navigate(self, url: str) -> dict[str, Any]:
        self.navigate_calls.append(url)
        if self.navigate_raises is not None:
            raise self.navigate_raises
        return {"url": url, "title": "fake"}


class _FakeScenarioPool:
    """Minimal stand-in for ``ScenarioPool`` for write-endpoint tests."""

    def __init__(self) -> None:
        self._live: dict[str, SimpleNamespace] = {}
        self.start_calls: list[str] = []
        self.stop_calls: list[str] = []
        self.run_macro_calls: list[dict[str, Any]] = []
        self.start_result: SimpleNamespace | None = None
        self.start_raises: BaseException | None = None
        self.stop_result: dict[str, Any] | None = None
        self.run_macro_result: dict[str, Any] | None = None

    def list_live(self) -> list[dict[str, Any]]:
        return []

    def has_live(self, scenario_id: str) -> bool:
        return scenario_id in self._live

    async def start(self, *, name: str, browser_pool: Any) -> SimpleNamespace:
        self.start_calls.append(name)
        if self.start_raises is not None:
            raise self.start_raises
        live = self.start_result or SimpleNamespace(
            scenario_id="scen00000001",
            name=name,
            participants=[
                {"role": "p", "persona": "x", "kind": "chromium", "instance_id": "iid000"},
            ],
        )
        self._live[live.scenario_id] = live
        return live

    async def stop(self, *, scenario_id: str, browser_pool: Any) -> dict[str, Any]:
        self.stop_calls.append(scenario_id)
        self._live.pop(scenario_id, None)
        return self.stop_result or {
            "scenario_id": scenario_id,
            "teardown_errors": [],
            "closed": [],
        }

    async def run_macro(
        self,
        *,
        scenario_id: str,
        macro: str,
        browser_pool: Any,
        role: str | None = None,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.run_macro_calls.append(
            {"scenario_id": scenario_id, "macro": macro, "role": role, "args": args},
        )
        return self.run_macro_result or {
            "scenario_id": scenario_id,
            "macro": macro,
            "role": role,
            "targeted": 0,
            "results": [],
        }


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace pool + scenario_pool singletons with fakes for the test run."""
    pool = _FakePool()
    spool = _FakeScenarioPool()
    monkeypatch.setattr(_state, "pool", pool)
    monkeypatch.setattr(_state, "scenario_pool", spool)
    return {"pool": pool, "scenario_pool": spool}


@pytest.fixture
def client(isolated_recordings: Path, fakes: dict[str, Any]) -> TestClient:
    return TestClient(_http.build_app())


# ---------------------------------------------------------------------------
# POST /api/sessions
# ---------------------------------------------------------------------------


def test_post_sessions_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    pool.launch_result = {
        "instance_id": "deadbeef0001",
        "kind": "chromium",
        "label": "qa-1",
        "profile": None,
        "url": "https://example.com",
        "log_path": "/tmp/deadbeef0001.jsonl",
        "record_video": True,
        "trace": True,
    }
    r = client.post(
        "/api/sessions",
        json={"kind": "chromium", "url": "https://example.com", "label": "qa-1"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # Response shape mirrors GET /api/sessions live[] entries.
    assert body["id"] == "deadbeef0001"
    assert body["kind"] == "chromium"
    assert body["label"] == "qa-1"
    assert body["url"] == "https://example.com"
    assert body["live"] is True
    assert body["log_path"] == str(Path("/tmp/deadbeef0001.jsonl"))
    assert "started_at" in body
    # pool.launch was called once, with the expected kwargs.
    assert len(pool.launch_calls) == 1
    call = pool.launch_calls[0]
    assert call["kind"] == "chromium"
    assert call["url"] == "https://example.com"
    assert call["label"] == "qa-1"


def test_post_sessions_publishes_dashboard_invalidation(
    client: TestClient,
    fakes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = AsyncMock()
    monkeypatch.setattr(session_routes, "publish_dashboard_invalidation", publish)

    r = client.post("/api/sessions", json={"kind": "chromium"})

    assert r.status_code == 201, r.text
    publish.assert_awaited_once_with("sessions")


def test_post_sessions_missing_kind_400(client: TestClient) -> None:
    r = client.post("/api/sessions", json={"url": "https://x.test"})
    assert r.status_code == 400
    assert "kind" in r.json()["error"]


def test_post_sessions_unknown_kind_400(client: TestClient) -> None:
    r = client.post("/api/sessions", json={"kind": "banana"})
    assert r.status_code == 400
    assert "kind must be one of" in r.json()["error"]


def test_post_sessions_malformed_json_400(client: TestClient) -> None:
    r = client.post(
        "/api/sessions",
        content=b"{not valid json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    assert "invalid JSON" in r.json()["error"]


def test_post_sessions_forwards_all_optional_kwargs(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    payload = {
        "kind": "firefox",
        "url": "https://x.test",
        "label": "lab",
        "profile": "alice",
        "viewport_w": 1024,
        "viewport_h": 768,
        "headed": False,
        "stabilize": True,
        "record_video": True,
        "trace": True,
    }
    r = client.post("/api/sessions", json=payload)
    assert r.status_code == 201, r.text
    assert len(pool.launch_calls) == 1
    call = pool.launch_calls[0]
    for key in (
        "kind",
        "url",
        "label",
        "profile",
        "viewport_w",
        "viewport_h",
        "headed",
        "stabilize",
        "record_video",
        "trace",
    ):
        assert call[key] == payload[key], f"mismatch on {key}: {call[key]!r} vs {payload[key]!r}"


def test_post_sessions_default_url_when_omitted(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    """If the body omits ``url`` the endpoint falls back to ``DEFAULT_URL``."""
    pool: _FakePool = fakes["pool"]
    r = client.post("/api/sessions", json={"kind": "chromium"})
    assert r.status_code == 201, r.text
    assert pool.launch_calls[0]["url"] == _http.DEFAULT_URL
    assert pool.launch_calls[0]["headed"] is None


def test_post_sessions_preserves_explicit_headed_false(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    r = client.post("/api/sessions", json={"kind": "chromium", "headed": False})
    assert r.status_code == 201, r.text
    assert pool.launch_calls[0]["headed"] is False


def test_post_sessions_pool_value_error_400(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    pool.launch_raises = ValueError("kind must be one of (chromium, firefox, webkit), got 'banana'")
    # We bypass the pre-check by stubbing launch to raise; need a kind that
    # passes our front-door validation first.
    r = client.post("/api/sessions", json={"kind": "chromium"})
    assert r.status_code == 400
    assert "must be one of" in r.json()["error"]


def test_post_sessions_unexpected_exception_500(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    pool.launch_raises = RuntimeError("playwright driver crashed")
    r = client.post("/api/sessions", json={"kind": "chromium"})
    assert r.status_code == 500
    assert "playwright driver crashed" in r.json()["error"]


# ---------------------------------------------------------------------------
# DELETE /api/sessions/{id}
# ---------------------------------------------------------------------------


def test_delete_session_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    pool._sessions["liveiid00001"] = SimpleNamespace(instance_id="liveiid00001")
    r = client.delete("/api/sessions/liveiid00001")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["closed"] is True
    assert body["instance_id"] == "liveiid00001"
    assert "log_path" in body
    assert pool.close_calls == ["liveiid00001"]


def test_delete_session_publishes_dashboard_invalidation(
    client: TestClient,
    fakes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool: _FakePool = fakes["pool"]
    pool._sessions["liveiid00003"] = SimpleNamespace(instance_id="liveiid00003")
    publish = AsyncMock()
    monkeypatch.setattr(session_routes, "publish_dashboard_invalidation", publish)

    r = client.delete("/api/sessions/liveiid00003")

    assert r.status_code == 200, r.text
    publish.assert_awaited_once_with("sessions")


def test_delete_session_happy_path_with_cache_report(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    pool: _FakePool = fakes["pool"]
    sid = "liveiid00002"
    pool._sessions[sid] = SimpleNamespace(instance_id=sid)

    log_path = isolated_recordings / "20260101T000000Z-chromium-liveiid00002.jsonl"
    md_path = log_path.with_suffix(".markdown.md")
    ws_path = log_path.with_suffix(".websocket.jsonl")
    video_path = isolated_recordings / "liveiid00002.webm"

    log_path.write_text(
        "".join(
            [
                json.dumps({"action": "launch", "kind": "chromium"}),
                "\n",
                json.dumps(
                    {
                        "action": "close",
                        "video_path": str(video_path),
                        "trace_path": None,
                        "markdown_path": str(md_path),
                    }
                ),
                "\n",
            ]
        )
    )
    md_path.write_text("markdown cache", encoding="utf-8")
    ws_path.write_text("hello", encoding="utf-8")
    video_path.write_bytes(b"\x00\x01")
    pool.close_result = {
        "closed": True,
        "log_path": str(log_path),
        "video_path": str(video_path),
        "trace_path": None,
    }

    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["closed"] is True
    assert body["instance_id"] == sid
    assert body["cache"]["components"]["markdown"]["size_bytes"] == md_path.stat().st_size
    assert body["cache"]["components"]["websocket"]["size_bytes"] == ws_path.stat().st_size
    assert body["cache"]["components"]["video"]["size_bytes"] == video_path.stat().st_size
    assert body["cache"]["components"]["jsonl"]["size_bytes"] == log_path.stat().st_size
    assert body["cache"]["components"]["jsonl"]["path"] == str(log_path)


def test_delete_session_writes_console_and_download_indexes(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
    tmp_path: Path,
) -> None:
    pool: _FakePool = fakes["pool"]
    sid = "liveiid00004"
    pool._sessions[sid] = SimpleNamespace(instance_id=sid)

    file_path = tmp_path / "artifact.txt"
    file_path.write_text("ok", encoding="utf-8")
    log_path = isolated_recordings / "20260101T000000Z-chromium-liveiid00004.jsonl"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {"action": "console", "level": "warn", "text": "be careful", "page_index": 0},
        {
            "action": "download_saved",
            "url": "https://x.test/artifact.txt",
            "suggested_filename": "artifact.txt",
            "path": str(file_path),
            "timestamp": "20260101T000001Z",
        },
        {"action": "close"},
    ]
    log_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    pool.close_result = {
        "closed": True,
        "log_path": str(log_path),
        "video_path": None,
        "trace_path": None,
    }

    r = client.delete(f"/api/sessions/{sid}")
    assert r.status_code == 200, r.text
    console_index = json.loads(log_path.with_suffix(".console.index.json").read_text(encoding="utf-8"))
    download_index = json.loads(log_path.with_suffix(".downloads.index.json").read_text(encoding="utf-8"))
    assert console_index["rows"] == [{"level": "warn", "text": "be careful", "page_index": 0}]
    assert download_index["rows"][0]["suggested_filename"] == "artifact.txt"


def test_delete_session_unknown_id_404(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    r = client.delete("/api/sessions/nopenope0001")
    assert r.status_code == 404
    assert "no live session" in r.json()["error"]


def test_delete_session_only_in_recordings_404(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    """A session whose JSONL is on disk but is not live must NOT be re-closeable."""
    pool: _FakePool = fakes["pool"]
    name = "20260101T000000Z-chromium-onlyondiskxx.jsonl"
    (isolated_recordings / name).write_text(
        json.dumps({"action": "launch", "kind": "chromium"}) + "\n",
    )
    assert "onlyondiskxx" not in pool._sessions
    r = client.delete("/api/sessions/onlyondiskxx")
    assert r.status_code == 404
    assert "closed sessions cannot be re-closed" in r.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/sessions/{id}/navigate
# ---------------------------------------------------------------------------


def test_post_navigate_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    sess = _FakeSession("navsessionA1")
    pool._sessions["navsessionA1"] = sess  # type: ignore[assignment]
    r = client.post(
        "/api/sessions/navsessionA1/navigate",
        json={"url": "https://target.test/page"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"ok": True, "url": "https://target.test/page"}
    assert sess.navigate_calls == ["https://target.test/page"]


def test_post_navigate_missing_url_400(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    pool._sessions["navmiss00001"] = _FakeSession("navmiss00001")  # type: ignore[assignment]
    r = client.post("/api/sessions/navmiss00001/navigate", json={})
    assert r.status_code == 400
    assert "url" in r.json()["error"]


def test_post_navigate_empty_url_400(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    pool: _FakePool = fakes["pool"]
    pool._sessions["navempty0001"] = _FakeSession("navempty0001")  # type: ignore[assignment]
    r = client.post("/api/sessions/navempty0001/navigate", json={"url": "  "})
    assert r.status_code == 400


def test_post_navigate_unknown_session_404(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    r = client.post(
        "/api/sessions/notthere0001/navigate",
        json={"url": "https://x.test"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/scenarios/{name}/start
# ---------------------------------------------------------------------------


def test_post_scenario_start_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool.start_result = SimpleNamespace(
        scenario_id="scenABC00001",
        name="demo",
        participants=[
            {"role": "player", "persona": "alice", "kind": "chromium", "instance_id": "iid000abc"},
        ],
    )
    r = client.post("/api/scenarios/demo/start")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scenario_id"] == "scenABC00001"
    assert body["name"] == "demo"
    assert len(body["participants"]) == 1
    assert spool.start_calls == ["demo"]


def test_post_scenario_start_publishes_dashboard_invalidations(
    client: TestClient,
    fakes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = AsyncMock()
    monkeypatch.setattr(scenario_routes, "publish_dashboard_invalidation", publish)

    r = client.post("/api/scenarios/demo/start")

    assert r.status_code == 201, r.text
    assert publish.await_args_list == [call("scenarios"), call("sessions")]


def test_post_scenario_start_unknown_name_404(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool.start_raises = FileNotFoundError("no scenario named 'mystery'")
    r = client.post("/api/scenarios/mystery/start")
    assert r.status_code == 404
    assert "mystery" in r.json()["error"]


def test_post_scenario_start_validation_error_400(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool.start_raises = ValueError("scenario 'broken': duplicate (persona, kind) pair")
    r = client.post("/api/scenarios/broken/start")
    assert r.status_code == 400
    assert "duplicate" in r.json()["error"]


def test_post_scenario_start_partial_launch_500(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    """spawn_roster failure surfaces as 500 with the spawn_roster error list."""
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool.start_raises = RuntimeError(
        "scenario 'two-up': 1 participant(s) failed to launch: [{'spec': {...}, 'error': 'boom'}]",
    )
    r = client.post("/api/scenarios/two-up/start")
    assert r.status_code == 500
    body = r.json()
    assert "failed to launch" in body["error"]


# ---------------------------------------------------------------------------
# DELETE /api/scenarios/{id}
# ---------------------------------------------------------------------------


def test_delete_scenario_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool._live["livescen0001"] = SimpleNamespace(scenario_id="livescen0001")
    spool.stop_result = {
        "scenario_id": "livescen0001",
        "teardown_errors": [],
        "closed": ["iid000a", "iid000b"],
    }
    r = client.delete("/api/scenarios/livescen0001")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scenario_id"] == "livescen0001"
    assert body["closed"] == ["iid000a", "iid000b"]
    assert body["teardown_errors"] == []
    assert spool.stop_calls == ["livescen0001"]


def test_delete_scenario_unknown_id_404(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    r = client.delete("/api/scenarios/notthere0000")
    assert r.status_code == 404
    assert "no live scenario" in r.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/scenarios/{id}/run_macro
# ---------------------------------------------------------------------------


def test_post_scenario_run_macro_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool._live["scenmacro001"] = SimpleNamespace(scenario_id="scenmacro001")
    spool.run_macro_result = {
        "scenario_id": "scenmacro001",
        "macro": "click_login",
        "role": None,
        "targeted": 2,
        "results": [
            {"instance_id": "iid000a", "ok": True},
            {"instance_id": "iid000b", "ok": True},
        ],
    }
    r = client.post(
        "/api/scenarios/scenmacro001/run_macro",
        json={"macro": "click_login"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["macro"] == "click_login"
    assert body["targeted"] == 2
    assert len(body["results"]) == 2
    assert spool.run_macro_calls[0]["macro"] == "click_login"


def test_post_scenario_run_macro_with_role_and_args(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool._live["scenarg00001"] = SimpleNamespace(scenario_id="scenarg00001")
    r = client.post(
        "/api/scenarios/scenarg00001/run_macro",
        json={"macro": "do_thing", "role": "player", "args": {"foo": "bar"}},
    )
    assert r.status_code == 200
    call = spool.run_macro_calls[0]
    assert call["role"] == "player"
    assert call["args"] == {"foo": "bar"}


def test_post_scenario_run_macro_missing_macro_400(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool._live["scennomac001"] = SimpleNamespace(scenario_id="scennomac001")
    r = client.post("/api/scenarios/scennomac001/run_macro", json={})
    assert r.status_code == 400
    assert "macro" in r.json()["error"]


def test_post_scenario_run_macro_unknown_id_404(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    r = client.post(
        "/api/scenarios/notthere0000/run_macro",
        json={"macro": "anything"},
    )
    assert r.status_code == 404


def test_post_scenario_run_macro_args_must_be_object_400(
    client: TestClient,
    fakes: dict[str, Any],
) -> None:
    spool: _FakeScenarioPool = fakes["scenario_pool"]
    spool._live["scenargbad01"] = SimpleNamespace(scenario_id="scenargbad01")
    r = client.post(
        "/api/scenarios/scenargbad01/run_macro",
        json={"macro": "x", "args": ["not", "an", "object"]},
    )
    assert r.status_code == 400
    assert "args" in r.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/sessions/{id}/relaunch
# ---------------------------------------------------------------------------


def _write_recording(rec_dir: Path, instance_id: str, launch_record: dict[str, Any]) -> Path:
    """Create a minimal JSONL whose first line is the supplied launch record."""
    path = rec_dir / f"20260101T000000Z-{launch_record['kind']}-{instance_id}.jsonl"
    path.write_text(json.dumps({"action": "launch", **launch_record}) + "\n")
    return path


def test_post_session_relaunch_happy_path(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    pool: _FakePool = fakes["pool"]
    _write_recording(
        isolated_recordings,
        "abc123abc123",
        {
            "kind": "firefox",
            "url": "https://example.com",
            "profile": "microdosing",
            "label": None,
            "viewport": {"w": 1280, "h": 800},
            "stabilize": False,
            "trace": False,
            "video_dir": None,
        },
    )
    r = client.post("/api/sessions/abc123abc123/relaunch")
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "firefox"
    assert body["profile"] == "microdosing"
    assert pool.launch_calls[0]["kind"] == "firefox"
    assert pool.launch_calls[0]["profile"] == "microdosing"
    assert pool.launch_calls[0]["viewport_w"] == 1280
    assert pool.launch_calls[0]["viewport_h"] == 800


def test_post_session_relaunch_409_when_live(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    pool: _FakePool = fakes["pool"]
    pool._sessions["liveinst0001"] = SimpleNamespace(instance_id="liveinst0001")
    r = client.post("/api/sessions/liveinst0001/relaunch")
    assert r.status_code == 409
    assert "still live" in r.json()["error"]


def test_post_session_relaunch_404_when_no_recording(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    r = client.post("/api/sessions/missingsid01/relaunch")
    assert r.status_code == 404


def test_post_session_relaunch_422_when_no_launch_record(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    # JSONL with no `launch` action — only a stray click record.
    path = isolated_recordings / "20260101T000000Z-chromium-noLaunch01ab.jsonl"
    path.write_text(json.dumps({"action": "click", "selector": "a"}) + "\n")
    r = client.post("/api/sessions/noLaunch01ab/relaunch")
    assert r.status_code == 422


def test_post_session_relaunch_passes_video_flag(
    client: TestClient,
    fakes: dict[str, Any],
    isolated_recordings: Path,
) -> None:
    pool: _FakePool = fakes["pool"]
    _write_recording(
        isolated_recordings,
        "video12absdf",
        {
            "kind": "chromium",
            "url": "https://example.com",
            "profile": None,
            "label": "rec",
            "viewport": None,
            "stabilize": False,
            "trace": False,
            "video_dir": "/tmp/some/video/dir",
        },
    )
    r = client.post("/api/sessions/video12absdf/relaunch")
    assert r.status_code == 201
    assert pool.launch_calls[0]["record_video"] is True


# ---------------------------------------------------------------------------
# GET /api/scenarios — saved field
# ---------------------------------------------------------------------------


def test_get_scenarios_includes_saved_on_disk(
    client: TestClient,
    fakes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """saved[] is the on-disk YAML/Python catalogue, decoupled from live[]."""
    from octowright import scenarios as _scenarios

    sdir = tmp_path / "scenarios"
    sdir.mkdir()
    (sdir / "alpha.yaml").write_text("name: alpha\nparticipants: []\n")
    (sdir / "beta.yaml").write_text("name: beta\nparticipants: []\n")
    monkeypatch.setattr(_scenarios, "SCENARIOS_DIR", sdir)

    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert "live" in body
    assert "saved" in body
    saved_names = sorted(s["name"] for s in body["saved"])
    assert saved_names == ["alpha", "beta"]
