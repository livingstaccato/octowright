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
from octowright.http.pairing import DASHBOARD_STREAM_LEASE_ATTR, DashboardStreamLease
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
        # The endpoint reads its admission lease off request.state; this test
        # bypasses the route guard that would normally attach it.
        state = SimpleNamespace(**{DASHBOARD_STREAM_LEASE_ATTR: DashboardStreamLease.bypass()})

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


# ─── _parse_since_cursor — unit tests ────────────────────────────────────────


class TestParseSinceCursor:
    def test_none_returns_zero(self) -> None:
        assert _events._parse_since_cursor(None) == 0

    def test_valid_int_string_parses(self) -> None:
        assert _events._parse_since_cursor("123") == 123

    def test_zero_string_parses(self) -> None:
        assert _events._parse_since_cursor("0") == 0

    def test_negative_int_string_clamps_to_zero(self) -> None:
        """Negative cursors → fh.seek(-N) raises OSError. Clamp at the
        boundary so a malformed ?since=-1 query param doesn't 500 the WS."""
        assert _events._parse_since_cursor("-5") == 0
        assert _events._parse_since_cursor("-1") == 0

    def test_non_integer_falls_back_to_zero(self) -> None:
        assert _events._parse_since_cursor("not-an-int") == 0

    def test_empty_string_falls_back_to_zero(self) -> None:
        assert _events._parse_since_cursor("") == 0


# ─── _parse_since (REST helper) — negative clamp ─────────────────────────────


class TestParseSinceClampsNegative:
    def test_rest_negative_clamps_to_zero(self, client: TestClient, isolated_recordings: Path) -> None:
        """REST /events with ?since=-1 must not 500 (negative seek). Clamp."""
        _write_recording(
            isolated_recordings,
            "negclmp001",
            [
                {"ts": "1", "action": "launch", "kind": "chromium"},
            ],
        )
        r = client.get("/api/sessions/negclmp001/events?since=-1")
        assert r.status_code == 200
        # Same result as since=0 — full content.
        assert len(r.json()["events"]) == 1

    def test_rest_large_negative_clamps_to_zero(self, client: TestClient, isolated_recordings: Path) -> None:
        """A wildly negative cursor still resolves to 0, not OSError."""
        _write_recording(
            isolated_recordings,
            "negclmp002",
            [{"ts": "1", "action": "launch", "kind": "chromium"}],
        )
        r = client.get("/api/sessions/negclmp002/events?since=-9999999")
        assert r.status_code == 200

    def test_rest_zero_unchanged(self, client: TestClient, isolated_recordings: Path) -> None:
        """since=0 stays 0 — clamp doesn't shift the legit baseline."""
        _write_recording(
            isolated_recordings,
            "negclmp003",
            [{"ts": "1", "action": "launch"}],
        )
        r = client.get("/api/sessions/negclmp003/events?since=0")
        assert r.status_code == 200
        assert len(r.json()["events"]) == 1


# ─── WS /tail negative ?since clamp ──────────────────────────────────────────


class TestTailNegativeSince:
    def test_negative_since_does_not_500(
        self, client: TestClient, isolated_recordings: Path, empty_pool: dict[str, Any]
    ) -> None:
        """WS /tail?since=-1 must not raise OSError from fh.seek(-1)."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-tlneg00001.jsonl"
        log_path.write_text(json.dumps({"ts": "1", "action": "launch", "kind": "chromium"}) + "\n")
        fake_session = SimpleNamespace(
            instance_id="tlneg00001",
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
            recorder=SimpleNamespace(event_count=1),
            console_count=0,
            download_count=0,
            page_count=1,
        )
        empty_pool["pool"]._sessions["tlneg00001"] = fake_session

        with client.websocket_connect("/api/sessions/tlneg00001/tail?since=-1") as ws:
            payload = ws.receive_json()
            # Clamped to 0 → first push has all events.
            assert len(payload["events"]) == 1
            assert payload["cursor"] > 0
            ws.close()


# ─── _stream_tail — heartbeat / empty-frame-skip behavior ────────────────────


class _FakeWebSocket:
    """Records every send_json + close call for assertion."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int | None = None, reason: str | None = None) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def receive(self) -> dict[str, Any]:
        # _stream_tail races receive() against the per-tick sleep; in tests the
        # sleep is monkeypatched to return immediately so receive must outlive
        # it — sleep forever and let the cancel branch tear it down.
        await asyncio.Event().wait()
        return {"type": "websocket.disconnect"}  # pragma: no cover


def _patch_stream_tail_loop(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshots: list[dict[str, Any]],
    live_after_tick: list[bool],
) -> None:
    """Wire `_tail_jsonl`, `_live_session_or_none`, `asyncio.sleep` so the
    stream loop terminates deterministically.

    `snapshots[i]` is returned on the ith call to `_tail_jsonl`.
    `live_after_tick[i]` is the result of `_live_session_or_none` on the
    ith call (False ends the loop). Both lists must be the same length.
    `asyncio.sleep` is no-op'd so the loop runs at full speed.
    """
    snapshot_iter = iter(snapshots)
    live_iter = iter(live_after_tick)

    def _fake_tail_jsonl(_path: Path, _cursor: int) -> dict[str, Any]:
        return next(snapshot_iter)

    def _fake_live(_sid: str) -> Any:
        return SimpleNamespace() if next(live_iter) else None

    async def _fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(_events, "_tail_jsonl", _fake_tail_jsonl)
    monkeypatch.setattr(_events, "_live_session_or_none", _fake_live)
    monkeypatch.setattr(_events.asyncio, "sleep", _fake_sleep)


class TestStreamTailHeartbeat:
    @pytest.mark.anyio
    async def test_skips_empty_frames_during_quiet_period(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty snapshots while still_live → no send_json before heartbeat tick."""
        # Heartbeat = 100s, poll = 1s → 100 quiet ticks before a heartbeat fires.
        monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 1.0)
        monkeypatch.setattr(_http_state, "TAIL_HEARTBEAT_SECONDS", 100.0)
        # 5 quiet ticks then the session goes away, ending the loop.
        _patch_stream_tail_loop(
            monkeypatch,
            snapshots=[{"events": [], "cursor": 0}] * 6,
            live_after_tick=[True] * 5 + [False],
        )
        ws = _FakeWebSocket()
        await _events._stream_tail(ws, "sid", Path("/tmp/x.jsonl"), cursor=0, lease=DashboardStreamLease.bypass())
        # Only the live→closed transition should have triggered a send.
        assert len(ws.sent) == 1
        assert ws.sent[0]["events"] == []
        assert ws.sent[0]["complete"] is True
        assert ws.closed is True

    @pytest.mark.anyio
    async def test_event_arrival_pushes_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-empty snapshot pushes that frame on the same tick."""
        monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 1.0)
        monkeypatch.setattr(_http_state, "TAIL_HEARTBEAT_SECONDS", 100.0)
        events_payload = [{"ts": "1", "action": "navigate"}]
        # Tick 0: event arrives. Tick 1: live=False → final empty push.
        _patch_stream_tail_loop(
            monkeypatch,
            snapshots=[
                {"events": events_payload, "cursor": 42},
                {"events": [], "cursor": 42},
            ],
            live_after_tick=[True, False],
        )
        ws = _FakeWebSocket()
        await _events._stream_tail(ws, "sid", Path("/tmp/x.jsonl"), cursor=0, lease=DashboardStreamLease.bypass())
        # Two sends: events + close-with-complete.
        assert len(ws.sent) == 2
        assert ws.sent[0]["events"] == events_payload
        assert ws.sent[0]["cursor"] == 42
        assert ws.sent[0]["complete"] is False
        assert ws.sent[1]["events"] == []
        assert ws.sent[1]["complete"] is True

    @pytest.mark.anyio
    async def test_heartbeat_fires_at_configured_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After heartbeat_every empty ticks, an empty frame goes out."""
        # Heartbeat = 3s, poll = 1s → heartbeat_every=3.
        monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 1.0)
        monkeypatch.setattr(_http_state, "TAIL_HEARTBEAT_SECONDS", 3.0)
        # 3 quiet ticks → heartbeat on tick 3. Then session closes.
        _patch_stream_tail_loop(
            monkeypatch,
            snapshots=[{"events": [], "cursor": 0}] * 4,
            live_after_tick=[True, True, True, False],
        )
        ws = _FakeWebSocket()
        await _events._stream_tail(ws, "sid", Path("/tmp/x.jsonl"), cursor=0, lease=DashboardStreamLease.bypass())
        # 1 heartbeat (tick 3) + 1 closing push (tick 4) = 2 sends.
        assert len(ws.sent) == 2
        assert all(s["events"] == [] for s in ws.sent)
        assert ws.sent[0]["complete"] is False  # heartbeat while live
        assert ws.sent[1]["complete"] is True  # closing push

    @pytest.mark.anyio
    async def test_event_resets_heartbeat_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pushing an events frame resets the tick counter — no immediate
        heartbeat right after."""
        monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 1.0)
        monkeypatch.setattr(_http_state, "TAIL_HEARTBEAT_SECONDS", 3.0)
        # Tick 0: 2 quiet ticks. Tick 2: events arrive (resets counter).
        # Tick 3-4: 2 more quiet ticks (still < 3 since reset). Tick 5: close.
        _patch_stream_tail_loop(
            monkeypatch,
            snapshots=[
                {"events": [], "cursor": 0},
                {"events": [], "cursor": 0},
                {"events": [{"a": 1}], "cursor": 7},
                {"events": [], "cursor": 7},
                {"events": [], "cursor": 7},
                {"events": [], "cursor": 7},
            ],
            live_after_tick=[True, True, True, True, True, False],
        )
        ws = _FakeWebSocket()
        await _events._stream_tail(ws, "sid", Path("/tmp/x.jsonl"), cursor=0, lease=DashboardStreamLease.bypass())
        # Sends: events frame at tick 2 + close frame at tick 5. No heartbeat
        # fired in between since counter was reset.
        assert len(ws.sent) == 2
        assert ws.sent[0]["events"] == [{"a": 1}]
        assert ws.sent[1]["complete"] is True

    @pytest.mark.anyio
    async def test_heartbeat_every_clamps_to_at_least_one_tick(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If heartbeat_seconds < poll_seconds the integer ratio rounds to 0;
        the loop must still fire — clamped to 1 tick — instead of never sending."""
        # Heartbeat = 0.1s, poll = 1s → ratio 0 → clamps to 1.
        monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 1.0)
        monkeypatch.setattr(_http_state, "TAIL_HEARTBEAT_SECONDS", 0.1)
        _patch_stream_tail_loop(
            monkeypatch,
            snapshots=[
                {"events": [], "cursor": 0},
                {"events": [], "cursor": 0},
            ],
            live_after_tick=[True, False],
        )
        ws = _FakeWebSocket()
        await _events._stream_tail(ws, "sid", Path("/tmp/x.jsonl"), cursor=0, lease=DashboardStreamLease.bypass())
        # heartbeat_every=1 → heartbeat on every tick → 2 sends.
        assert len(ws.sent) == 2

    @pytest.mark.anyio
    async def test_session_closed_on_first_tick_pushes_complete(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Session disappears between connect and first poll → one final
        push with complete=True, then close."""
        monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 1.0)
        monkeypatch.setattr(_http_state, "TAIL_HEARTBEAT_SECONDS", 100.0)
        _patch_stream_tail_loop(
            monkeypatch,
            snapshots=[{"events": [], "cursor": 0}],
            live_after_tick=[False],
        )
        ws = _FakeWebSocket()
        await _events._stream_tail(ws, "sid", Path("/tmp/x.jsonl"), cursor=0, lease=DashboardStreamLease.bypass())
        assert len(ws.sent) == 1
        assert ws.sent[0]["complete"] is True
        assert ws.closed is True


# ─── _close_for_unknown_or_closed_session — handshake codes ──────────────────


class TestCloseUnknownOrClosed:
    @pytest.mark.anyio
    async def test_closed_session_emits_1003_with_redirect_hint(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Recording on disk → WS code 1003 + 'use GET /events instead'."""
        monkeypatch.setattr(_http_state, "RECORDINGS_DIR", tmp_path)
        monkeypatch.setattr(_events, "_find_recording_for", lambda _sid, _dir: tmp_path / "x.jsonl")
        ws = _FakeWebSocket()
        await _events._close_for_unknown_or_closed_session(ws, "sid42")
        assert ws.closed is True
        assert ws.close_code == 1003
        assert ws.close_reason is not None
        assert "GET" in ws.close_reason

    @pytest.mark.anyio
    async def test_unknown_session_emits_1008_with_id_in_reason(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No recording → WS code 1008 + reason includes the requested id."""
        monkeypatch.setattr(_http_state, "RECORDINGS_DIR", tmp_path)
        monkeypatch.setattr(_events, "_find_recording_for", lambda _sid, _dir: None)
        ws = _FakeWebSocket()
        await _events._close_for_unknown_or_closed_session(ws, "ghostid")
        assert ws.closed is True
        assert ws.close_code == 1008
        assert ws.close_reason is not None
        assert "ghostid" in ws.close_reason


# ─── _sleep_or_disconnect — disconnect race tears down promptly ─────────────


class _RaceWebSocket:
    """Fake WS whose ``receive`` resolves at a configurable time."""

    def __init__(self, disconnect_after: float | None) -> None:
        self._disconnect_after = disconnect_after

    async def receive(self) -> dict[str, Any]:
        if self._disconnect_after is None:
            await asyncio.Event().wait()  # never returns
        await asyncio.sleep(self._disconnect_after)
        return {"type": "websocket.disconnect"}

    async def send_json(self, payload: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError("should not be called in this test")

    async def close(self, code: int | None = None, reason: str | None = None) -> None:  # pragma: no cover
        return None


class TestSleepOrDisconnect:
    @pytest.mark.anyio
    async def test_disconnect_during_sleep_returns_within_ms(self) -> None:
        """A disconnect mid-sleep must tear down well under the sleep budget.

        Sleep budget: 1.0s. Disconnect at 10ms. Total elapsed should be ~10ms,
        not ~1s. Allow 200ms slop for CI scheduler jitter — even that is 5x
        better than the pre-fix worst case of TAIL_POLL_SECONDS.
        """
        ws = _RaceWebSocket(disconnect_after=0.01)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        ok = await _events._sleep_or_disconnect(ws, seconds=1.0)  # type: ignore[arg-type]
        elapsed = loop.time() - t0
        assert ok is False
        assert elapsed < 0.2, f"cleanup took {elapsed:.3f}s, expected <0.2s"

    @pytest.mark.anyio
    async def test_sleep_completes_normally_when_no_disconnect(self) -> None:
        """When the client stays connected, the sleep elapses and returns True."""
        ws = _RaceWebSocket(disconnect_after=None)
        ok = await _events._sleep_or_disconnect(ws, seconds=0.02)  # type: ignore[arg-type]
        assert ok is True
