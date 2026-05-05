# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTTP debugger sidecar tests.

Uses Starlette's TestClient (sync) for endpoint coverage, including the
WebSocket. Live state is faked with small pool doubles that expose the same
public lookup/listing methods as the real pools; closed sessions are exercised
by writing synthetic JSONL files to a tmp recordings dir and pointing
``RECORDINGS_DIR`` at it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from octowright import http as _http
from octowright.http import lifespan as _http_lifespan
from octowright.http import metrics as _http_metrics
from octowright.http import state as _http_state
from octowright.server import _state

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


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
    """Point every RECORDINGS_DIR consumer in `http_server` at a fresh tmp dir."""
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    return rec


@pytest.fixture
def empty_pool(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace the live pool/scenario_pool with empty stand-ins for the test run.

    Restored automatically by monkeypatch teardown.
    """
    fake_pool = _FakeHttpPool()
    fake_spool = _FakeHttpScenarioPool()
    monkeypatch.setattr(_state, "pool", fake_pool)
    monkeypatch.setattr(_state, "scenario_pool", fake_spool)
    return {"pool": fake_pool, "scenario_pool": fake_spool}


@pytest.fixture
def client(isolated_recordings: Path, empty_pool: dict[str, Any]) -> TestClient:
    """Build a fresh Starlette client backed by the isolated state."""
    app = _http.build_app()
    return TestClient(app)


def _write_recording(rec_dir: Path, instance_id: str, *, kind: str = "chromium") -> Path:
    """Synthesise a `<stamp>-<kind>-<id>.jsonl` recording with a launch + navigate + close.

    instance_id is what the http_server parses out of the filename (third dash-token).
    """
    name = f"20260101T000000Z-{kind}-{instance_id}.jsonl"
    p = rec_dir / name
    rows = [
        {
            "ts": "2026-01-01T00:00:00Z",
            "action": "launch",
            "kind": kind,
            "url": "https://example.com",
            "label": None,
            "profile": None,
        },
        {"ts": "2026-01-01T00:00:01Z", "action": "navigate", "url": "https://example.com/login"},
        {"ts": "2026-01-01T00:00:02Z", "action": "close", "video_path": None, "trace_path": None},
    ]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


# ---------------------------------------------------------------------------
# happy-path basics
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["version"], str)


def test_metrics_endpoint_and_counters(client: TestClient) -> None:
    _http_metrics.HTTP_METRICS = _http_metrics.HttpMetrics()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/sessions").status_code == 200
    r = client.get("/api/metrics")
    assert r.status_code == 200
    text = r.text
    assert "octowright_http_requests_total" in text
    assert "octowright_http_requests_by_status" in text


def test_metrics_endpoint_can_be_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_HTTP_METRICS", "0")
    with TestClient(_http.build_app()) as local_client:
        assert local_client.get("/api/metrics").status_code == 404


def test_list_sessions_empty(client: TestClient) -> None:
    r = client.get("/api/sessions")
    assert r.status_code == 200
    assert r.json() == {"live": [], "closed": []}


def test_list_sessions_with_closed_recording(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "abc123def456")
    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body["live"] == []
    assert len(body["closed"]) == 1
    closed = body["closed"][0]
    assert closed["id"] == "abc123def456"
    assert closed["kind"] == "chromium"
    assert closed["live"] is False
    assert closed["url"] == "https://example.com"


def test_list_sessions_with_live_session(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-firefox-livethere01.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "firefox"}) + "\n")
    fake_session = SimpleNamespace(
        instance_id="livethere01",
        kind="firefox",
        label="qa",
        profile="alice",
        url="https://x.test",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[],
        downloads=[],
        pages=[None],  # pages is a list; len() matters
    )
    empty_pool["pool"]._sessions["livethere01"] = fake_session

    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert len(body["live"]) == 1
    assert body["live"][0]["id"] == "livethere01"
    # Same JSONL must NOT also appear under closed.
    assert all(s["id"] != "livethere01" for s in body["closed"])


# ---------------------------------------------------------------------------
# session detail
# ---------------------------------------------------------------------------


def test_session_detail_404(client: TestClient) -> None:
    r = client.get("/api/sessions/nope")
    assert r.status_code == 404


def test_session_detail_closed(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "closeddata00")
    r = client.get("/api/sessions/closeddata00")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "closeddata00"
    assert body["live"] is False
    assert body["markdown_path"] is None
    assert body["action_count"] == 3
    assert body["video_path"] is None


def test_session_detail_closed_includes_markdown_path(client: TestClient, isolated_recordings: Path) -> None:
    jsonl = _write_recording(isolated_recordings, "mdcheck00")
    md_path = jsonl.with_suffix(".markdown.md")
    md_path.write_text("# cached markdown", encoding="utf-8")

    r = client.get("/api/sessions/mdcheck00")
    assert r.status_code == 200
    assert r.json()["markdown_path"] == str(md_path)


def test_session_detail_closed_includes_cache_report(client: TestClient, isolated_recordings: Path) -> None:
    jsonl = _write_recording(
        isolated_recordings,
        "cachedata00",
        kind="chromium",
    )
    md_path = jsonl.with_suffix(".markdown.md")
    md_path.write_text("# cached markdown", encoding="utf-8")
    ws_path = jsonl.with_suffix(".websocket.jsonl")
    ws_path.write_text("binary", encoding="utf-8")
    trace_path = jsonl.with_suffix(".trace.zip")
    trace_path.write_bytes(b"zip")
    video_path = isolated_recordings / "cachedata00.webm"
    video_path.write_bytes(b"video")
    (isolated_recordings / "cachedata00-shot.png").write_bytes(b"pngdata")

    rows = [
        {"action": "launch", "kind": "chromium"},
        {"action": "close", "video_path": str(video_path), "trace_path": str(trace_path)},
        {"action": "close", "markdown_path": str(md_path)},
    ]
    jsonl.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    r = client.get("/api/sessions/cachedata00")
    assert r.status_code == 200
    body = r.json()
    cache = body["cache"]
    assert cache["total_bytes"] > 0
    assert cache["components"]["markdown"]["size_bytes"] == md_path.stat().st_size
    assert cache["components"]["websocket"]["size_bytes"] == ws_path.stat().st_size
    assert cache["components"]["trace"]["size_bytes"] == trace_path.stat().st_size
    assert cache["components"]["video"]["size_bytes"] == video_path.stat().st_size
    assert cache["components"]["screenshots"]["count"] == 1
    assert cache["components"]["jsonl"]["path"] == str(jsonl)


def test_session_detail_live(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-detaillive00.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    page = MagicMock()
    page.locator = MagicMock(return_value=MagicMock(aria_snapshot=AsyncMock(return_value=None)))
    fake = SimpleNamespace(
        instance_id="detaillive00",
        kind="chromium",
        label=None,
        profile=None,
        url="https://x.y",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        markdown_path=None,
        console=[{"level": "log", "text": "hi"}],
        downloads=[],
        pages=[None, None],
        page=page,
    )
    empty_pool["pool"]._sessions["detaillive00"] = fake
    r = client.get("/api/sessions/detaillive00")
    assert r.status_code == 200
    body = r.json()
    assert body["live"] is True
    assert body["console_count"] == 1
    assert body["page_count"] == 2


def test_session_detail_live_includes_cache_report(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-livecache01.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    md_path = log_path.with_suffix(".markdown.md")
    md_path.write_text("# live markdown", encoding="utf-8")
    ws_path = log_path.with_suffix(".websocket.jsonl")
    ws_path.write_text("abc", encoding="utf-8")
    shot_path = isolated_recordings / "livecache01-shot.png"
    shot_path.write_bytes(b"img")
    page = MagicMock()
    page.locator = MagicMock(return_value=MagicMock(aria_snapshot=AsyncMock(return_value=None)))
    fake = SimpleNamespace(
        instance_id="livecache01",
        kind="chromium",
        label=None,
        profile=None,
        url="https://x.y",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        websocket_path=ws_path,
        markdown_path=md_path,
        console=[],
        downloads=[],
        pages=[None, None],
        page=page,
    )
    empty_pool["pool"]._sessions["livecache01"] = fake

    r = client.get("/api/sessions/livecache01")
    assert r.status_code == 200
    body = r.json()
    cache = body["cache"]
    assert cache["components"]["markdown"]["size_bytes"] == md_path.stat().st_size
    assert cache["components"]["websocket"]["size_bytes"] == ws_path.stat().st_size
    assert cache["components"]["screenshots"]["count"] == 1


# ---------------------------------------------------------------------------
# /events
# ---------------------------------------------------------------------------


def test_session_events_404(client: TestClient) -> None:
    r = client.get("/api/sessions/nosuch/events")
    assert r.status_code == 404


def test_session_events_full_read(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "evfull000abc")
    r = client.get("/api/sessions/evfull000abc/events")
    assert r.status_code == 200
    body = r.json()
    assert body["complete"] is True
    assert len(body["events"]) == 3
    assert body["events"][0]["action"] == "launch"


def test_session_events_partial_line(client: TestClient, isolated_recordings: Path) -> None:
    """File ends mid-line — cursor stops before the partial fragment."""
    p = isolated_recordings / "20260101T000000Z-chromium-evpart00abcd.jsonl"
    first = json.dumps({"action": "launch", "kind": "chromium"}) + "\n"
    fragment = '{"action": "type'
    p.write_text(first + fragment)
    r = client.get("/api/sessions/evpart00abcd/events")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == [{"action": "launch", "kind": "chromium"}]
    assert body["cursor"] == p.read_bytes().find(fragment.encode("utf-8"))
    assert body["complete"] is False


def test_session_events_invalid_since(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "evbadsince01")
    r = client.get("/api/sessions/evbadsince01/events?since=notanumber")
    assert r.status_code == 400


def test_session_events_with_since_offset(client: TestClient, isolated_recordings: Path) -> None:
    p = _write_recording(isolated_recordings, "evsincers001")
    full_size = p.stat().st_size
    r = client.get(f"/api/sessions/evsincers001/events?since={full_size}")
    assert r.status_code == 200
    body = r.json()
    assert body["events"] == []
    assert body["cursor"] == full_size


def test_session_events_sanitizes_binary_websocket_preview(client: TestClient, isolated_recordings: Path) -> None:
    name = "20260101T000000Z-chromium-evwsraw001.jsonl"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {
            "action": "websocket_framereceived",
            "is_binary": True,
            "payload_preview": "b'secret'",
            "payload_size": 6,
        },
        {"action": "close"},
    ]
    (isolated_recordings / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    r = client.get("/api/sessions/evwsraw001/events")
    assert r.status_code == 200
    body = r.json()
    websocket_event = body["events"][1]
    assert websocket_event["payload_preview"] == "[binary payload hidden: 6 bytes]"


def test_session_events_sanitizes_binary_websocket_preview_without_is_binary_flag(
    client: TestClient, isolated_recordings: Path
) -> None:
    name = "20260101T000000Z-chromium-evwsraw002.jsonl"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {
            "action": "websocket_framereceived",
            "payload_preview": "b'secret'",
            "payload_size": 6,
        },
        {"action": "close"},
    ]
    (isolated_recordings / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    r = client.get("/api/sessions/evwsraw002/events")
    assert r.status_code == 200
    body = r.json()
    websocket_event = body["events"][1]
    assert websocket_event["payload_preview"] == "[binary payload hidden: 6 bytes]"
    assert websocket_event["is_binary"] is True


# ---------------------------------------------------------------------------
# /frame
# ---------------------------------------------------------------------------


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x00\x00\x00\x00:~\x9bU"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_session_frame_no_video(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "framenovid01")
    r = client.get("/api/sessions/framenovid01/frame?t=0.5")
    assert r.status_code == 404
    assert r.json()["error"]


def test_session_frame_extracts_via_video_module(
    client: TestClient,
    isolated_recordings: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint uses video.extract_frames; we stub that to drop a real PNG on disk."""
    # Create a fake video file + recording that references it via close event.
    video_path = isolated_recordings / "videos" / "stub.webm"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"\x00")

    name = "20260101T000000Z-chromium-framewithv01.jsonl"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {"action": "close", "video_path": str(video_path), "trace_path": None},
    ]
    (isolated_recordings / name).write_text("".join(json.dumps(r) + "\n" for r in rows))

    def fake_extract(
        vp: Path, out_dir: Path, *, fps: float | None = None, at_times: list[float] | None = None
    ) -> list[Path]:
        # Mimic the real ffmpeg producer's filename pattern.
        out_dir.mkdir(parents=True, exist_ok=True)
        produced = out_dir / f"frame-000-t{at_times[0]:.3f}.png"
        produced.write_bytes(_TINY_PNG)
        return [produced]

    monkeypatch.setattr(_http_state._video, "extract_frames", fake_extract)
    r = client.get("/api/sessions/framewithv01/frame?t=1.5")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content == _TINY_PNG

    # Second request hits the cache (extract_frames must NOT be called again).
    monkeypatch.setattr(_http_state._video, "extract_frames", lambda *a, **kw: pytest.fail("cache miss!"))
    r2 = client.get("/api/sessions/framewithv01/frame?t=1.5")
    assert r2.status_code == 200
    assert r2.content == _TINY_PNG


def test_session_frame_invalid_t(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "framebadt001")
    r = client.get("/api/sessions/framebadt001/frame?t=abc")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /video, /trace, /screenshots
# ---------------------------------------------------------------------------


def test_session_video_404(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "vidmissing01")
    r = client.get("/api/sessions/vidmissing01/video")
    assert r.status_code == 404


def test_session_video_returns_file(client: TestClient, isolated_recordings: Path) -> None:
    video = isolated_recordings / "videos" / "v.webm"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"WEBMDATA")
    name = "20260101T000000Z-chromium-videxists01.jsonl"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {"action": "close", "video_path": str(video)},
    ]
    (isolated_recordings / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = client.get("/api/sessions/videxists01/video")
    assert r.status_code == 200
    assert r.content == b"WEBMDATA"


def test_session_markdown_returns_file(client: TestClient, isolated_recordings: Path) -> None:
    name = "20260101T000000Z-chromium-markdownexists01"
    jsonl = isolated_recordings / f"{name}.jsonl"
    jsonl.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    md_path = jsonl.with_suffix(".markdown.md")
    md_path.write_text("# cached markdown", encoding="utf-8")

    r = client.get("/api/sessions/markdownexists01/markdown")
    assert r.status_code == 200
    assert r.text == "# cached markdown"


def test_session_markdown_404_when_file_missing(client: TestClient, isolated_recordings: Path) -> None:
    name = "20260101T000000Z-chromium-markdownmissing01"
    jsonl = isolated_recordings / f"{name}.jsonl"
    md_path = isolated_recordings / f"{name}.markdown.md"
    md_path.write_text("", encoding="utf-8")
    md_path.unlink()
    jsonl.write_text(
        json.dumps({"action": "launch", "kind": "chromium"})
        + "\n"
        + json.dumps({"action": "close", "markdown_path": str(md_path)})
        + "\n",
        encoding="utf-8",
    )

    r = client.get("/api/sessions/markdownmissing01/markdown")
    assert r.status_code == 404
    assert r.json()["error"] == "no markdown cache available for this session"


@pytest.mark.asyncio
async def test_markdown_endpoint_roundtrip_live_and_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright")
    import octowright.browser_pool.pool as _pool
    from octowright import defaults as _defaults
    from octowright import http as _http
    from octowright import personas as _personas
    from octowright import profiles as _profiles
    from octowright.browser_pool import BrowserPool
    from octowright.http import state as _http_state
    from octowright.server import _state

    rec = tmp_path / "recordings"
    profiles = tmp_path / "profiles"
    rec.mkdir()
    profiles.mkdir()

    monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_http_state, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)

    class _NoScenarios:
        @staticmethod
        def list_live() -> list[dict[str, Any]]:
            return []

    pool = BrowserPool()
    monkeypatch.setattr(_state, "pool", pool)
    monkeypatch.setattr(_state, "scenario_pool", _NoScenarios())

    app = _http.build_app()
    client = TestClient(app)

    try:
        try:
            result = await pool.launch(
                kind="chromium",
                url="data:text/html,<html><body><h1>Integration</h1></body></html>",
                headed=False,
                label="e2e-markdown",
                session=True,
                viewport_w=640,
                viewport_h=480,
            )
        except Exception as exc:
            pytest.skip(f"browser launch unavailable in this environment: {exc!r}")

        sid = result["instance_id"]
        session = pool.get(sid)
        # Markdown capture is now scheduled automatically on launch + navigation.
        for _ in range(20):
            if session.markdown_path is not None and session.markdown_path.exists():
                break
            await asyncio.sleep(0.05)
        assert session.markdown_path is not None
        assert session.markdown_path.exists()

        live_resp = client.get(f"/api/sessions/{sid}/markdown")
        assert live_resp.status_code == 200
        assert "Integration" in live_resp.text

        await session.navigate("data:text/html,<html><body><h1>Integration Two</h1></body></html>")
        for _ in range(20):
            if session.markdown_path is not None and "Integration Two" in session.markdown_path.read_text(
                encoding="utf-8"
            ):
                break
            await asyncio.sleep(0.05)
        live_resp_two = client.get(f"/api/sessions/{sid}/markdown")
        assert live_resp_two.status_code == 200
        assert "Integration Two" in live_resp_two.text

        await pool.close(sid)
        await asyncio.sleep(0)

        closed_resp = client.get(f"/api/sessions/{sid}/markdown")
        assert closed_resp.status_code == 200
        assert "Integration" in closed_resp.text
    finally:
        await pool.shutdown()


def test_session_trace_404(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "tracenone01x")
    r = client.get("/api/sessions/tracenone01x/trace")
    assert r.status_code == 404


def test_session_trace_returns_file(client: TestClient, isolated_recordings: Path) -> None:
    name = "20260101T000000Z-chromium-tracehas01x0"
    log = isolated_recordings / f"{name}.jsonl"
    trace = isolated_recordings / f"{name}.trace.zip"
    log.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    trace.write_bytes(b"PK\x03\x04ZIPDATA")
    r = client.get("/api/sessions/tracehas01x0/trace")
    assert r.status_code == 200
    assert r.content.startswith(b"PK")


def test_screenshots_listing(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "screenshot01")
    # Drop two screenshots whose filenames contain the session id (matches
    # the convention used by `BrowserSession.screenshot`).
    (isolated_recordings / "screenshot01-fail-1.png").write_bytes(_TINY_PNG)
    (isolated_recordings / "screenshot01-fail-2.png").write_bytes(_TINY_PNG)
    r = client.get("/api/sessions/screenshot01/screenshots")
    assert r.status_code == 200
    body = r.json()
    assert len(body["screenshots"]) == 2


def test_screenshot_file_404(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "screenshot02")
    r = client.get("/api/sessions/screenshot02/screenshots/missing.png")
    assert r.status_code == 404


def test_screenshot_file_returns_bytes(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "screenshot03")
    shot = isolated_recordings / "screenshot03-shot.png"
    shot.write_bytes(_TINY_PNG)
    r = client.get("/api/sessions/screenshot03/screenshots/screenshot03-shot.png")
    assert r.status_code == 200
    assert r.content == _TINY_PNG


def test_screenshot_path_traversal_rejected(client: TestClient, isolated_recordings: Path) -> None:
    """Filename with `../` must NOT escape the recordings dir."""
    _write_recording(isolated_recordings, "screenshot04")
    r = client.get("/api/sessions/screenshot04/screenshots/..%2F..%2Fetc%2Fpasswd")
    assert r.status_code in (400, 404)


# ---------------------------------------------------------------------------
# /scenarios, /personas, /macros
# ---------------------------------------------------------------------------


def test_scenarios_empty(client: TestClient, empty_pool: dict[str, Any]) -> None:
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert body["live"] == []
    assert "saved" in body


def test_scenarios_with_one_live(client: TestClient, empty_pool: dict[str, Any]) -> None:
    empty_pool["scenario_pool"].list_live = lambda: [
        {"scenario_id": "s1", "name": "demo", "participants": []},
    ]
    r = client.get("/api/scenarios")
    assert r.status_code == 200
    assert r.json()["live"][0]["scenario_id"] == "s1"


def test_personas_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http_state._personas, "list_personas", lambda: [])
    r = client.get("/api/personas")
    assert r.status_code == 200
    assert r.json() == []


def test_personas_listing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _http_state._personas,
        "list_personas",
        lambda: [{"name": "alice", "display_name": "Alice", "engines": ["chromium"], "last_used": "2026-01-01"}],
    )
    r = client.get("/api/personas")
    assert r.status_code == 200
    body = r.json()
    assert body[0]["name"] == "alice"
    assert body[0]["engines"] == ["chromium"]


def test_macros_empty(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_http_state._macros, "list_macros", lambda: [])
    r = client.get("/api/macros")
    assert r.status_code == 200
    assert r.json() == []


def test_macros_listing(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _http_state._macros,
        "list_macros",
        lambda: [{"name": "login", "description": "do login", "parameters": ["email"], "updated_at": "2026-01-01"}],
    )
    r = client.get("/api/macros")
    assert r.status_code == 200
    assert r.json()[0]["name"] == "login"


# ---------------------------------------------------------------------------
# /trace/open
# ---------------------------------------------------------------------------


def test_trace_open_404(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "topennone001")
    r = client.post("/api/sessions/topennone001/trace/open")
    assert r.status_code == 404


def test_trace_open_spawns(
    client: TestClient,
    isolated_recordings: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "20260101T000000Z-chromium-toopenok0123"
    (isolated_recordings / f"{name}.jsonl").write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    (isolated_recordings / f"{name}.trace.zip").write_bytes(b"PK\x03\x04")

    captured: dict[str, Any] = {}

    def fake_popen(args: list[str], **kwargs: Any) -> Any:
        captured["args"] = list(args)
        return SimpleNamespace(pid=9999)

    monkeypatch.setattr(_http_state.shutil, "which", lambda name: "/fake/npx")
    monkeypatch.setattr(_http_state.subprocess, "Popen", fake_popen)

    r = client.post("/api/sessions/toopenok0123/trace/open")
    assert r.status_code == 200
    body = r.json()
    assert body["pid"] == 9999
    assert "trace.zip" in body["trace_path"]
    assert captured["args"][:3] == ["npx", "playwright", "show-trace"]


def test_trace_open_no_npx(
    client: TestClient,
    isolated_recordings: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = "20260101T000000Z-chromium-tonpxgone001"
    (isolated_recordings / f"{name}.jsonl").write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    (isolated_recordings / f"{name}.trace.zip").write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(_http_state.shutil, "which", lambda name: None)
    r = client.post("/api/sessions/tonpxgone001/trace/open")
    assert r.status_code == 500


# ---------------------------------------------------------------------------
# WebSocket /tail
# ---------------------------------------------------------------------------


def test_tail_unknown_session_closes_with_1008(client: TestClient) -> None:
    """Unknown session id (no live, no recording) → policy-violation close."""
    from starlette.websockets import WebSocketDisconnect

    with client.websocket_connect("/api/sessions/wsnope0000abc/tail") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
        assert excinfo.value.code == 1008
        assert "no session" in (excinfo.value.reason or "")


def test_tail_closed_session_closes_with_1003(client: TestClient, isolated_recordings: Path) -> None:
    """A session whose recording exists on disk but is not live → 1003 + redirect note."""
    from starlette.websockets import WebSocketDisconnect

    _write_recording(isolated_recordings, "wsclosed0001")
    with client.websocket_connect("/api/sessions/wsclosed0001/tail") as ws:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()
        assert excinfo.value.code == 1003
        # Reason must point users at the right endpoint for closed sessions.
        assert "/events" in (excinfo.value.reason or "")


def test_tail_respects_since_query_param(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``?since=N`` makes the tail start AFTER cursor N, not from byte 0.

    Without this, the dashboard would render the launch event twice (once from
    the initial GET /events, once from the first WS push).
    """
    log_path = isolated_recordings / "20260101T000000Z-chromium-wssince0001.jsonl"
    line1 = json.dumps({"action": "launch", "kind": "chromium"}) + "\n"
    line2 = json.dumps({"action": "navigate", "url": "https://x"}) + "\n"
    log_path.write_text(line1 + line2)
    empty_pool["pool"]._sessions["wssince0001"] = SimpleNamespace(
        instance_id="wssince0001",
        log_path=log_path,
        video_path=None,
        trace_path=None,
    )
    monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 0.01)

    # Pass since = byte length of line1 — first WS frame should ONLY contain line2.
    since = len(line1.encode())
    with client.websocket_connect(f"/api/sessions/wssince0001/tail?since={since}") as ws:
        msg = ws.receive_json()
        assert len(msg["events"]) == 1
        assert msg["events"][0]["action"] == "navigate"


def test_tail_live_session_streams(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For a live session, the WS pushes at least one tick before we disconnect."""
    log_path = isolated_recordings / "20260101T000000Z-chromium-wslive00abcd.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    empty_pool["pool"]._sessions["wslive00abcd"] = SimpleNamespace(
        instance_id="wslive00abcd",
        log_path=log_path,
        video_path=None,
        trace_path=None,
    )

    # Speed the loop up so the test isn't waiting a full second.
    monkeypatch.setattr(_http_state, "TAIL_POLL_SECONDS", 0.01)

    with client.websocket_connect("/api/sessions/wslive00abcd/tail") as ws:
        msg = ws.receive_json()
        assert msg["complete"] is False
        assert msg["events"][0]["action"] == "launch"
        # Append a new line and ensure the next tick picks it up.
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"action": "click", "selector": "#go"}) + "\n")
        # Drain ticks until we see the new event.
        seen_click = False
        for _ in range(50):
            msg = ws.receive_json()
            for ev in msg["events"]:
                if ev.get("action") == "click":
                    seen_click = True
                    break
            if seen_click:
                break
        assert seen_click


# ---------------------------------------------------------------------------
# /console
# ---------------------------------------------------------------------------


def test_console_404_unknown_session(client: TestClient) -> None:
    r = client.get("/api/sessions/nope/console")
    assert r.status_code == 404
    assert "no session" in r.json()["error"]


def test_console_live_session_returns_messages(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-conslive00xx.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    fake = SimpleNamespace(
        instance_id="conslive00xx",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[
            {"level": "log", "text": "hello", "page_index": None},
            {"level": "warn", "text": "watch out", "page_index": None},
            {"level": "error", "text": "boom", "page_index": 1},
        ],
        downloads=[],
        list_downloads=lambda: [],
    )
    empty_pool["pool"]._sessions["conslive00xx"] = fake
    r = client.get("/api/sessions/conslive00xx/console")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["cursor"] == 3
    assert len(body["messages"]) == 3
    assert body["messages"][0] == {"level": "log", "text": "hello", "page_index": None}


def test_console_level_filter(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-consfilter000.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    empty_pool["pool"]._sessions["consfilter000"] = SimpleNamespace(
        instance_id="consfilter000",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[
            {"level": "log", "text": "a", "page_index": None},
            {"level": "error", "text": "b", "page_index": None},
            {"level": "log", "text": "c", "page_index": None},
        ],
        downloads=[],
        list_downloads=lambda: [],
    )
    r = client.get("/api/sessions/consfilter000/console?level=error")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["messages"][0]["text"] == "b"


def test_console_since_cursor(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-conssince0000.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    empty_pool["pool"]._sessions["conssince0000"] = SimpleNamespace(
        instance_id="conssince0000",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[
            {"level": "log", "text": "1", "page_index": None},
            {"level": "log", "text": "2", "page_index": None},
            {"level": "log", "text": "3", "page_index": None},
        ],
        downloads=[],
        list_downloads=lambda: [],
    )
    r = client.get("/api/sessions/conssince0000/console?since=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["cursor"] == 3
    assert [m["text"] for m in body["messages"]] == ["3"]


def test_console_closed_session_returns_empty(client: TestClient, isolated_recordings: Path) -> None:
    """Closed-session console view is empty when the recording has no console rows."""
    _write_recording(isolated_recordings, "consclosed01x")
    r = client.get("/api/sessions/consclosed01x/console")
    assert r.status_code == 200
    body = r.json()
    assert body == {"messages": [], "cursor": 0, "total": 0}


def test_console_closed_session_reads_persisted_rows(client: TestClient, isolated_recordings: Path) -> None:
    """Closed-session console view reconstructs persisted action='console' rows."""
    name = "20260101T000000Z-chromium-conspersist01"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {"action": "console", "level": "warn", "text": "deprecated API"},
        {"action": "console", "level": "error", "text": "oops", "page_index": 1},
    ]
    (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = client.get("/api/sessions/conspersist01/console")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["messages"][0] == {"level": "warn", "text": "deprecated API"}
    assert body["messages"][1] == {"level": "error", "text": "oops", "page_index": 1}

    filtered = client.get("/api/sessions/conspersist01/console?level=error").json()
    assert filtered["total"] == 1
    assert filtered["messages"] == [{"level": "error", "text": "oops", "page_index": 1}]


# ---------------------------------------------------------------------------
# /downloads
# ---------------------------------------------------------------------------


def test_downloads_404_unknown_session(client: TestClient) -> None:
    r = client.get("/api/sessions/nope/downloads")
    assert r.status_code == 404


def test_downloads_live_session_with_path_exists(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
    tmp_path: Path,
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-dllive00abcd.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")

    real_file = tmp_path / "report.csv"
    real_file.write_text("col1,col2\n")
    missing_file = tmp_path / "deleted.csv"  # never created

    rows = [
        {
            "url": "https://x.test/report.csv",
            "suggested_filename": "report.csv",
            "path": str(real_file),
            "timestamp": "20260101T000000Z",
        },
        {
            "url": "https://x.test/deleted.csv",
            "suggested_filename": "deleted.csv",
            "path": str(missing_file),
            "timestamp": "20260101T000001Z",
        },
    ]
    empty_pool["pool"]._sessions["dllive00abcd"] = SimpleNamespace(
        instance_id="dllive00abcd",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[],
        downloads=rows,
        list_downloads=lambda: list(rows),
    )
    r = client.get("/api/sessions/dllive00abcd/downloads")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert body["cursor"] == 2
    assert body["downloads"][0]["path_exists"] is True
    assert body["downloads"][1]["path_exists"] is False
    assert body["downloads"][0]["suggested_filename"] == "report.csv"


def test_downloads_since_cursor(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-dlsince0000xx.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    rows = [
        {"url": f"https://x.test/{i}.bin", "suggested_filename": f"{i}.bin", "path": "/nope", "timestamp": "t"}
        for i in range(3)
    ]
    empty_pool["pool"]._sessions["dlsince0000xx"] = SimpleNamespace(
        instance_id="dlsince0000xx",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[],
        downloads=rows,
        list_downloads=lambda: list(rows),
    )
    r = client.get("/api/sessions/dlsince0000xx/downloads?since=2")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["cursor"] == 3
    assert [d["suggested_filename"] for d in body["downloads"]] == ["2.bin"]


def test_downloads_closed_session_reads_jsonl(client: TestClient, isolated_recordings: Path, tmp_path: Path) -> None:
    """Closed sessions reconstruct downloads from action='download_saved' rows."""
    real_file = tmp_path / "doc.pdf"
    real_file.write_bytes(b"%PDF-1.4")
    name = "20260101T000000Z-chromium-dlclosed01xy"
    rows = [
        {"action": "launch", "kind": "chromium"},
        {
            "action": "download_saved",
            "url": "https://x.test/doc.pdf",
            "suggested_filename": "doc.pdf",
            "path": str(real_file),
            "timestamp": "20260101T000005Z",
        },
        {
            "action": "download_saved",
            "url": "https://x.test/old.pdf",
            "suggested_filename": "old.pdf",
            "path": str(tmp_path / "vanished.pdf"),
            "timestamp": "20260101T000006Z",
        },
        {"action": "close"},
    ]
    (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    r = client.get("/api/sessions/dlclosed01xy/downloads")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    by_name = {d["suggested_filename"]: d for d in body["downloads"]}
    assert by_name["doc.pdf"]["path_exists"] is True
    assert by_name["old.pdf"]["path_exists"] is False


def test_downloads_invalid_since(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    log_path = isolated_recordings / "20260101T000000Z-chromium-dlbadsince01.jsonl"
    log_path.write_text(json.dumps({"action": "launch"}) + "\n")
    empty_pool["pool"]._sessions["dlbadsince01"] = SimpleNamespace(
        instance_id="dlbadsince01",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[],
        downloads=[],
        list_downloads=lambda: [],
    )
    r = client.get("/api/sessions/dlbadsince01/downloads?since=notanint")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# port-picking helpers
# ---------------------------------------------------------------------------


def test_port_is_free_and_pick_port(monkeypatch: pytest.MonkeyPatch) -> None:
    # Real syscalls — but only on localhost ephemeral ports, so this is hermetic.
    import socket as _socket

    # Bind an ephemeral port to make sure _port_is_free is False for it.
    s = _socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        busy = s.getsockname()[1]
        assert _http_lifespan._port_is_free("127.0.0.1", busy) is False
        # Pick should walk past busy.
        chosen = _http_lifespan._pick_port("127.0.0.1", busy, retries=20)
        assert chosen is not None
        assert chosen != busy
    except PermissionError as exc:
        pytest.skip(f"port binding unavailable in this environment: {exc!r}")
    finally:
        s.close()


def test_runtime_status_when_not_started() -> None:
    # Module-level globals are reset by `serve_app` on shutdown; out of band
    # this should report not-running.
    _http_state._RUNTIME_HOST = None
    _http_state._RUNTIME_PORT = None
    _http_state._RUNTIME_ERROR = None
    status = _http.runtime_status()
    assert status["running"] is False
    assert _http.runtime_url() is None
    assert _http.runtime_session_url("xyz") is None


# ---------------------------------------------------------------------------
# Frontend bundle mount + SPA deep-link fallback
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_frontend_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Drop a fake bundled frontend at FRONTEND_DIR so static-mount routes resolve."""
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<!DOCTYPE html><html><body>dashboard</body></html>")
    (bundle / "session.html").write_text("<!DOCTYPE html><html><body>session debugger</body></html>")
    (bundle / "styles.css").write_text("body { color: red; }")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)
    return bundle


def test_index_html_served_at_root(stub_frontend_bundle: Path) -> None:
    with TestClient(_http.build_app()) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "dashboard" in r.text
        assert r.headers["content-type"].startswith("text/html")


def test_static_asset_served(stub_frontend_bundle: Path) -> None:
    with TestClient(_http.build_app()) as client:
        r = client.get("/styles.css")
        assert r.status_code == 200
        assert "color: red" in r.text


def test_session_deep_link_serves_session_html(stub_frontend_bundle: Path) -> None:
    """SPA fallback: /sessions/<id> must serve session.html (frontend reads id from URL)."""
    with TestClient(_http.build_app()) as client:
        r = client.get("/sessions/abc123")
        assert r.status_code == 200
        assert "session debugger" in r.text
        assert r.headers["content-type"].startswith("text/html")


def test_session_deep_link_with_complex_id(stub_frontend_bundle: Path) -> None:
    """The path catchall handles ids with arbitrary characters."""
    with TestClient(_http.build_app()) as client:
        for sid in ["abc-123", "ABC123def456", "id_with_underscores", "0123456789ab"]:
            r = client.get(f"/sessions/{sid}")
            assert r.status_code == 200, f"failed for id={sid}"
            assert "session debugger" in r.text


def test_no_frontend_routes_when_bundle_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If the frontend hasn't been built yet, the API still works — the dashboard is just 404."""
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", tmp_path / "does-not-exist")
    with TestClient(_http.build_app()) as client:
        # API still works.
        assert client.get("/api/health").status_code == 200
        # Static routes are absent; / is unhandled by Starlette and 404s.
        assert client.get("/").status_code == 404
        assert client.get("/sessions/abc").status_code == 404


# ---------------------------------------------------------------------------
# /screenshot/now (live preview)
# ---------------------------------------------------------------------------


class _FakePage:
    """Minimal stand-in for a Playwright Page that records screenshot kwargs."""

    def __init__(self, *, returns: bytes = _TINY_PNG, raises: Exception | None = None) -> None:
        self.returns = returns
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def screenshot(self, **kwargs: Any) -> bytes:
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return self.returns


def _install_live_session_with_page(
    pool: SimpleNamespace,
    isolated_recordings: Path,
    sid: str,
    page: _FakePage,
) -> None:
    log_path = isolated_recordings / f"20260101T000000Z-chromium-{sid}.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    pool._sessions[sid] = SimpleNamespace(
        instance_id=sid,
        kind="chromium",
        label=None,
        profile=None,
        url="https://x.test",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        console=[],
        downloads=[],
        pages=[None],
        page=page,
    )


def test_screenshot_now_live_session_returns_png(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    page = _FakePage(returns=_TINY_PNG)
    _install_live_session_with_page(empty_pool["pool"], isolated_recordings, "snnowlive001", page)
    r = client.get("/api/sessions/snnowlive001/screenshot/now")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "no-store"
    assert r.content == _TINY_PNG
    assert page.calls[0]["type"] == "png"
    assert page.calls[0]["full_page"] is False


def test_screenshot_now_closed_session_404(client: TestClient, isolated_recordings: Path) -> None:
    _write_recording(isolated_recordings, "snnowclosed01")
    r = client.get("/api/sessions/snnowclosed01/screenshot/now")
    assert r.status_code == 404
    assert "session is closed" in r.json()["error"]


def test_screenshot_now_unknown_id_404(client: TestClient) -> None:
    r = client.get("/api/sessions/nopesuchid/screenshot/now")
    assert r.status_code == 404
    assert "no session with id" in r.json()["error"]


def test_screenshot_now_invalid_format_400(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    page = _FakePage()
    _install_live_session_with_page(empty_pool["pool"], isolated_recordings, "snnowfmt0001", page)
    r = client.get("/api/sessions/snnowfmt0001/screenshot/now?format=webp")
    assert r.status_code == 400
    assert "format" in r.json()["error"]


def test_screenshot_now_jpeg_format_passes_quality(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    page = _FakePage(returns=b"\xff\xd8\xff\xe0fake-jpeg")
    _install_live_session_with_page(empty_pool["pool"], isolated_recordings, "snnowjpeg001", page)
    r = client.get("/api/sessions/snnowjpeg001/screenshot/now?format=jpeg&quality=42&full_page=true")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content.startswith(b"\xff\xd8")
    assert page.calls[0] == {"type": "jpeg", "quality": 42, "full_page": True}


def test_screenshot_now_invalid_quality_400(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    page = _FakePage()
    _install_live_session_with_page(empty_pool["pool"], isolated_recordings, "snnowqual001", page)
    r = client.get("/api/sessions/snnowqual001/screenshot/now?quality=999")
    assert r.status_code == 400


def test_screenshot_now_invalid_full_page_400(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    page = _FakePage()
    _install_live_session_with_page(empty_pool["pool"], isolated_recordings, "snnowfull001", page)
    r = client.get("/api/sessions/snnowfull001/screenshot/now?full_page=maybe")
    assert r.status_code == 400


def test_screenshot_now_page_screenshot_raises_503(
    client: TestClient,
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    page = _FakePage(raises=RuntimeError("page navigated"))
    _install_live_session_with_page(empty_pool["pool"], isolated_recordings, "snnowfail001", page)
    r = client.get("/api/sessions/snnowfail001/screenshot/now")
    assert r.status_code == 503
    assert "page navigated" in r.json()["error"]


def test_session_deep_link_falls_back_to_404_when_session_html_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If FRONTEND_DIR exists but session.html somehow isn't there, return a clear 404."""
    bundle = tmp_path / "frontend"
    bundle.mkdir()
    (bundle / "index.html").write_text("<html>only index</html>")
    monkeypatch.setattr(_http_state, "FRONTEND_DIR", bundle)
    with TestClient(_http.build_app()) as client:
        r = client.get("/sessions/abc")
        assert r.status_code == 404
        assert "session.html" in r.text
