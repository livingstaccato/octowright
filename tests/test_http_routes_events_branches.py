# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.http.routes.events.

Existing `tests/test_http_server.py` already covers the easy paths
(session_events 404/full/partial/invalid-since, console live + level +
cursor + closed-empty, downloads live + path_exists + sidecar +
since-clamping, tail 1008/1003 + since + live streaming). This file
fills the remaining gaps:

  - The two private helpers `_read_console_from_jsonl` /
    `_read_downloads_from_jsonl` (currently only reachable via the
    routes themselves).
  - The SSE `/api/dashboard/events` endpoint (no existing tests).
  - SSE-frame / SSE-comment format pins.
  - `session_events` since=0 default branch.
  - Tail endpoint's non-int `since` query param falling back to 0.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http import state as _http_state
from octowright.http.routes import events as _events
from octowright.server import _state

# ─── fixtures ───────────────────────────────────────────────────────────────


class _FakeHttpPool:
    def __init__(self) -> None:
        self._sessions: dict[str, Any] = {}

    def maybe_get(self, instance_id: str) -> Any | None:
        return self._sessions.get(instance_id)

    def has_session(self, instance_id: str) -> bool:
        return instance_id in self._sessions

    def iter_sessions(self) -> tuple[Any, ...]:
        return tuple(self._sessions.values())


class _FakeHttpScenarioPool:
    def __init__(self) -> None:
        self._live: dict[str, Any] = {}

    def has_live(self, scenario_id: str) -> bool:
        return scenario_id in self._live

    def list_live(self) -> list[dict[str, Any]]:
        return []


@pytest.fixture
def isolated_recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    from octowright.http.discovery import invalidate_recording_index

    invalidate_recording_index()
    return rec


@pytest.fixture
def empty_pool(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    fake_pool = _FakeHttpPool()
    fake_spool = _FakeHttpScenarioPool()
    monkeypatch.setattr(_state, "pool", fake_pool)
    monkeypatch.setattr(_state, "scenario_pool", fake_spool)
    return {"pool": fake_pool, "scenario_pool": fake_spool}


@pytest.fixture
def client(isolated_recordings: Path, empty_pool: dict[str, Any]) -> TestClient:
    return TestClient(_http.build_app())


def _write_recording(rec_dir: Path, instance_id: str, rows: list[dict[str, Any]]) -> Path:
    name = f"20260101T000000Z-chromium-{instance_id}.jsonl"
    p = rec_dir / name
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


# ─── _sse_frame / _sse_comment format ────────────────────────────────────────


class TestSseFormatHelpers:
    def test_frame_has_event_and_data_lines(self) -> None:
        """SSE frame format: 'event: NAME\\ndata: JSON\\n\\n'."""
        out = _events._sse_frame("invalidate", {"scope": "sessions"})
        assert out == b'event: invalidate\ndata: {"scope":"sessions"}\n\n'

    def test_frame_uses_compact_json(self) -> None:
        """No spaces in JSON separators — wire-friendly."""
        out = _events._sse_frame("x", {"a": 1, "b": 2})
        assert b'"a":1,"b":2' in out
        assert b'": ' not in out  # no space after colon

    def test_comment_format(self) -> None:
        """Comments start with ': ' and end with double newline."""
        out = _events._sse_comment("heartbeat")
        assert out == b": heartbeat\n\n"


# ─── session_events: since=0 default + valid since=int ──────────────────────


class TestSessionEventsSinceDefault:
    def test_since_omitted_defaults_to_zero(self, client: TestClient, isolated_recordings: Path) -> None:
        """No since param → default of 0 (full read)."""
        _write_recording(
            isolated_recordings,
            "evtdef0001",
            [
                {"ts": "2026-01-01T00:00:00Z", "action": "launch", "kind": "chromium"},
                {"ts": "2026-01-01T00:00:01Z", "action": "navigate", "url": "https://x"},
            ],
        )
        r = client.get("/api/sessions/evtdef0001/events")
        assert r.status_code == 200
        body = r.json()
        # cursor should be the file's full byte length, events list non-empty.
        assert body["cursor"] > 0
        assert len(body["events"]) == 2


# ─── _read_console_from_jsonl / _read_downloads_from_jsonl helpers ──────────


class TestReadConsoleHelper:
    def test_routes_through_session_artifact_cache(
        self, isolated_recordings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Helper delegates to session_artifact_cache.get_console_rows."""
        captured: list[Path] = []

        def fake_get(jsonl: Path) -> list[dict[str, Any]]:
            captured.append(jsonl)
            return [{"level": "info", "text": "hi"}]

        monkeypatch.setattr(_events.session_artifact_cache, "get_console_rows", fake_get)
        p = isolated_recordings / "x.jsonl"
        result = _events._read_console_from_jsonl(p)
        assert captured == [p]
        assert result == [{"level": "info", "text": "hi"}]

    def test_extracts_console_rows_from_real_jsonl(self, isolated_recordings: Path) -> None:
        """End-to-end: write JSONL with console events, helper returns them."""
        p = _write_recording(
            isolated_recordings,
            "consread01",
            [
                {"ts": "1", "action": "launch", "kind": "chromium"},
                {"ts": "2", "action": "console", "level": "warn", "text": "uh-oh"},
                {"ts": "3", "action": "click", "selector": "#x"},
                {"ts": "4", "action": "console", "level": "error", "text": "boom"},
            ],
        )
        rows = _events._read_console_from_jsonl(p)
        assert len(rows) == 2
        assert rows[0]["level"] == "warn"
        assert rows[1]["level"] == "error"


class TestReadDownloadsHelper:
    def test_routes_through_session_artifact_cache(
        self, isolated_recordings: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Helper delegates to session_artifact_cache.get_download_rows."""
        captured: list[Path] = []

        def fake_get(jsonl: Path) -> list[dict[str, Any]]:
            captured.append(jsonl)
            return [{"url": "https://x", "path": "/tmp/f"}]

        monkeypatch.setattr(_events.session_artifact_cache, "get_download_rows", fake_get)
        p = isolated_recordings / "x.jsonl"
        result = _events._read_downloads_from_jsonl(p)
        assert captured == [p]
        assert result[0]["url"] == "https://x"

    def test_extracts_download_rows_from_real_jsonl(self, isolated_recordings: Path) -> None:
        """End-to-end: download_saved rows surface with the four canonical fields."""
        p = _write_recording(
            isolated_recordings,
            "dlsread001",
            [
                {"ts": "1", "action": "launch", "kind": "chromium"},
                {
                    "ts": "2",
                    "action": "download_saved",
                    "url": "https://x/file.zip",
                    "suggested_filename": "file.zip",
                    "path": "/tmp/file.zip",
                    "timestamp": "2026-01-01T00:00:02Z",
                },
            ],
        )
        rows = _events._read_downloads_from_jsonl(p)
        assert len(rows) == 1
        row = rows[0]
        assert row["url"] == "https://x/file.zip"
        assert row["suggested_filename"] == "file.zip"
        assert row["path"] == "/tmp/file.zip"
        assert row["timestamp"] == "2026-01-01T00:00:02Z"


# ─── session_console / session_downloads: closed session via sidecar ────────


class TestClosedSessionConsoleSidecarHit:
    def test_console_closed_session_hits_sidecar(self, client: TestClient, isolated_recordings: Path) -> None:
        """Closed-session console reads use the cache's sidecar fast path."""
        # Write a JSONL recording with two console events.
        p = _write_recording(
            isolated_recordings,
            "conscld001",
            [
                {"ts": "1", "action": "launch", "kind": "chromium"},
                {"ts": "2", "action": "console", "level": "info", "text": "first"},
                {"ts": "3", "action": "console", "level": "warn", "text": "second"},
            ],
        )
        # Pre-populate the sidecar so the call hits sidecar-fast-path, not scan.
        sig = _events.session_artifact_cache._signature(p)
        assert sig is not None
        sidecar = p.with_suffix(".console.index.json")
        sidecar.write_text(
            json.dumps(
                {
                    "version": _events.session_artifact_cache._read_index_file.__globals__["_SIDECAR_FORMAT_VERSION"],
                    "source": {"mtime_ns": sig[0], "size": sig[1]},
                    "rows": [{"level": "info", "text": "from-sidecar"}],
                }
            )
        )
        r = client.get("/api/sessions/conscld001/console")
        assert r.status_code == 200
        body = r.json()
        # Sidecar wins over a fresh scan.
        assert body["messages"] == [{"level": "info", "text": "from-sidecar"}]


# ─── dashboard SSE: hello frame + helper-level branches ────────────────────
#
# We don't drive the streaming endpoint end-to-end — Starlette's TestClient
# treats the SSE stream as a long-lived connection (heartbeat is 15s and the
# disconnect signal only fires after exiting the `with stream()` block) so
# any test that reads from the stream while still inside it deadlocks. We
# pin the moving parts at the helper level instead.


@pytest.mark.anyio
async def test_dashboard_events_endpoint_returns_streaming_response() -> None:
    """The endpoint returns a StreamingResponse with the SSE content-type
    and proxy-friendly cache-control headers."""
    from starlette.responses import StreamingResponse

    request = SimpleNamespace(is_disconnected=lambda: True, query_params={})
    response = await _events.dashboard_events_endpoint(request)  # type: ignore[arg-type]
    assert isinstance(response, StreamingResponse)
    assert response.media_type == "text/event-stream"
    assert response.headers.get("cache-control") == "no-cache"
    assert response.headers.get("x-accel-buffering") == "no"


@pytest.mark.anyio
async def test_dashboard_events_first_emits_hello_frame() -> None:
    """The streamer yields the hello frame as its first chunk."""

    class _Req:
        async def is_disconnected(self) -> bool:
            return True  # disconnect immediately so the loop ends after hello

        query_params: dict[str, Any] = {}

    response = await _events.dashboard_events_endpoint(_Req())  # type: ignore[arg-type]
    body_iter = response.body_iterator  # type: ignore[attr-defined]
    first = await body_iter.__anext__()
    if isinstance(first, str):
        first = first.encode()
    assert b"event: hello" in first
    assert b'"ok":true' in first


@pytest.mark.anyio
async def test_wait_for_dashboard_disconnect_returns_when_disconnected() -> None:
    """Helper polls request.is_disconnected; returns once it reports True."""
    state = {"disconnected": False}

    class _Req:
        async def is_disconnected(self) -> bool:
            return state["disconnected"]

    async def trigger() -> None:
        await asyncio.sleep(0.01)
        state["disconnected"] = True

    await asyncio.gather(_events._wait_for_dashboard_disconnect(_Req()), trigger())  # type: ignore[arg-type]


# ─── TailEndpoint: non-int since query param falls back to 0 ────────────────


class TestTailNonIntSince:
    def test_non_int_since_falls_back_to_zero(
        self, client: TestClient, isolated_recordings: Path, empty_pool: dict[str, Any]
    ) -> None:
        """Bad ?since=abc → cursor=0, full content streams in the first push."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-tlbadsnc01.jsonl"
        log_path.write_text(
            json.dumps({"ts": "1", "action": "launch", "kind": "chromium"})
            + "\n"
            + json.dumps({"ts": "2", "action": "navigate", "url": "https://x"})
            + "\n"
        )
        # Build a fake "live" session pointing at this log.
        fake_session = SimpleNamespace(
            instance_id="tlbadsnc01",
            kind="chromium",
            log_path=log_path,
            url="https://x",
            label=None,
            profile=None,
            video_path=None,
            trace_path=None,
            console=[],
            downloads=[],
            pages=[None],
            recorder=SimpleNamespace(event_count=2),
            console_count=0,
            download_count=0,
            page_count=1,
        )
        empty_pool["pool"]._sessions["tlbadsnc01"] = fake_session

        with client.websocket_connect("/api/sessions/tlbadsnc01/tail?since=not-an-int") as ws:
            payload = ws.receive_json()
            # Bad since fell back to 0 → both events present.
            assert len(payload["events"]) == 2
            assert payload["cursor"] > 0
            ws.close()


# ─── DASHBOARD_DISCONNECT_POLL_SECONDS / HEARTBEAT_SECONDS constants ───────


class TestModuleConstants:
    def test_disconnect_poll_is_subsecond(self) -> None:
        """Disconnect-poll cadence is fast enough that test client closes promptly."""
        assert _events.DASHBOARD_DISCONNECT_POLL_SECONDS <= 0.1

    def test_heartbeat_is_seconds_not_minutes(self) -> None:
        """Heartbeat at ~15s — under typical proxy idle-close (60s)."""
        assert 1 <= _events.DASHBOARD_HEARTBEAT_SECONDS <= 60


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
