# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.http.routes.media.

Targets the branches that ``tests/test_http_server.py`` doesn't already
pin: every parameter-validation path on ``session_screenshot_now``, the
extract-failure paths on ``session_frame``, content-disposition pins on
``session_video`` / ``session_trace``, the live-session cache-miss branch
on ``session_markdown`` (including UTF-8 round-trip and the
capture_markdown raise → 500 path), unknown-id and empty-dir paths on
``session_screenshots`` plus its field-shape pins.
"""

# ruff: noqa: F811  (pytest fixtures imported from sibling test module shadow
# parameter names by design; ruff's redefinition rule doesn't model that)
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from octowright.http import state as _http_state
from octowright.session.operation.gate import SessionOperationGate

# Reuse fixtures from the existing http test module so we don't fork the
# fake-pool plumbing.
from tests.test_http_server import (  # noqa: F401  (re-exported for pytest collection)
    _TINY_PNG,
    _FakeHttpPool,
    _FakeHttpScenarioPool,
    _write_recording,
    client,
    empty_pool,
    isolated_recordings,
)

# ─── helpers ────────────────────────────────────────────────────────────────


def _live_session(log_path: Path, **overrides: Any) -> SimpleNamespace:
    """Build a fake live BrowserSession that the http routes treat as live."""
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG-mock")
    instance_id = overrides.get("instance_id", log_path.stem.split("-")[-1])
    # A real gate (not a MagicMock) — session_screenshot_now/dashboard_session_detail
    # await `.operation(...)` as an async context manager.
    gate = SessionOperationGate(instance_id, "chromium", queue_timeout_seconds=30)
    base = SimpleNamespace(
        instance_id=instance_id,
        kind="chromium",
        label=None,
        profile=None,
        url="https://octowright.com",
        log_path=log_path,
        video_path=None,
        trace_path=None,
        markdown_path=None,
        websocket_path=None,
        console=[],
        downloads=[],
        pages=[page],
        recorder=SimpleNamespace(event_count=1),
        console_count=0,
        download_count=0,
        page_count=1,
        page=page,
        operation=gate.operation,
        operation_snapshot=gate.snapshot,
        _test_operation_gate=gate,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class _RaisingOperation:
    """A ``.operation()`` replacement whose entry raises immediately --
    proves a route's exception handler maps this specific gate error. A
    plain class (not ``@asynccontextmanager``) so there's no syntactically-
    required-but-unreachable ``yield`` after the ``raise`` for vulture to
    (correctly) flag as dead code."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def __call__(self, _name: str, *, wait_timeout_seconds: object = None) -> _RaisingOperation:
        return self

    async def __aenter__(self) -> None:
        raise self._exc

    async def __aexit__(self, *_args: object) -> bool:
        return False


# ─── /screenshot/now ────────────────────────────────────────────────────────


class TestScreenshotNow:
    def test_invalid_format_400(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """format=avif → 400 with explicit error mentioning png/jpeg."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-snapshotnow1.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        empty_pool["pool"]._sessions["snapshotnow1"] = _live_session(log_path)
        r = client.get("/api/sessions/snapshotnow1/screenshot/now?format=avif")
        assert r.status_code == 400
        assert "png" in r.json()["error"] and "jpeg" in r.json()["error"]

    def test_invalid_quality_string_400(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """quality=hi → 400."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-snapshotnow2.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        empty_pool["pool"]._sessions["snapshotnow2"] = _live_session(log_path)
        r = client.get("/api/sessions/snapshotnow2/screenshot/now?quality=hi")
        assert r.status_code == 400

    @pytest.mark.parametrize("q", [0, 101, -1, 200])
    def test_quality_out_of_range_400(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
        q: int,
    ) -> None:
        """quality must be between 1 and 100 inclusive."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-snapshotnow3.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        empty_pool["pool"]._sessions["snapshotnow3"] = _live_session(log_path)
        r = client.get(f"/api/sessions/snapshotnow3/screenshot/now?quality={q}")
        assert r.status_code == 400

    def test_invalid_full_page_400(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """full_page=maybe → 400."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-snapshotnow4.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        empty_pool["pool"]._sessions["snapshotnow4"] = _live_session(log_path)
        r = client.get("/api/sessions/snapshotnow4/screenshot/now?full_page=maybe")
        assert r.status_code == 400

    def test_unknown_session_404(self, client: TestClient) -> None:
        """No live session and no recording → 404 with 'no session with id' wording."""
        r = client.get("/api/sessions/missingsessio/screenshot/now")
        assert r.status_code == 404
        assert "no session with id" in r.json()["error"]

    def test_closed_session_404_distinguished(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Recording exists but no live → 404 with 'session is closed' wording."""
        _write_recording(isolated_recordings, "snapshotclosed")
        r = client.get("/api/sessions/snapshotclosed/screenshot/now")
        assert r.status_code == 404
        assert "session is closed" in r.json()["error"]

    def test_live_png_default_returns_bytes(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """Default format=png; bytes returned with image/png + Cache-Control: no-store."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-livepng001x.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        empty_pool["pool"]._sessions["livepng001x"] = _live_session(log_path)
        r = client.get("/api/sessions/livepng001x/screenshot/now")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"
        assert r.headers["cache-control"] == "no-store"
        assert r.content == b"\x89PNG-mock"

    def test_live_jpeg_includes_quality_kwarg(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """jpeg branch passes ``quality`` to page.screenshot; png doesn't."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-livejpeg001.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        captured: dict[str, Any] = {}

        async def screenshot_capture(**kwargs: Any) -> bytes:
            captured.update(kwargs)
            return b"\xff\xd8\xff-jpeg"

        session = _live_session(log_path)
        session.page.screenshot = screenshot_capture
        empty_pool["pool"]._sessions["livejpeg001"] = session
        r = client.get("/api/sessions/livejpeg001/screenshot/now?format=jpeg&quality=42&full_page=true")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert captured == {"type": "jpeg", "full_page": True, "quality": 42}

    def test_live_png_omits_quality_kwarg(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """png branch must NOT pass ``quality`` (Playwright errors on it)."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-livepng002x.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        captured: dict[str, Any] = {}

        async def screenshot_capture(**kwargs: Any) -> bytes:
            captured.update(kwargs)
            return b"png"

        session = _live_session(log_path)
        session.page.screenshot = screenshot_capture
        empty_pool["pool"]._sessions["livepng002x"] = session
        r = client.get("/api/sessions/livepng002x/screenshot/now?quality=80")
        assert r.status_code == 200
        assert "quality" not in captured

    def test_live_screenshot_failure_503(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """page.screenshot raises → 503 with the error string."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-livefail001.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        session = _live_session(log_path)
        session.page.screenshot = AsyncMock(side_effect=RuntimeError("page navigated"))
        empty_pool["pool"]._sessions["livefail001"] = session
        r = client.get("/api/sessions/livefail001/screenshot/now")
        assert r.status_code == 503
        assert "page navigated" in r.json()["error"]

    def test_live_screenshot_awaits_coroutine(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """If screenshot returns a coroutine (async stub), the route awaits it."""
        log_path = isolated_recordings / "20260101T000000Z-chromium-liveasync01.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")

        async def async_screenshot(**_kwargs: Any) -> bytes:
            return b"\x89PNG-async"

        session = _live_session(log_path)
        session.page.screenshot = async_screenshot
        empty_pool["pool"]._sessions["liveasync01"] = session
        r = client.get("/api/sessions/liveasync01/screenshot/now")
        assert r.status_code == 200
        assert r.content == b"\x89PNG-async"


@pytest.mark.anyio
async def test_live_screenshot_waits_for_session_gate(
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    """A concurrent dashboard_screenshot request must queue behind an
    already-held session operation instead of racing it -- proving
    session_screenshot_now's ``async with live.operation("dashboard_screenshot")``
    boundary is real, not a no-op wrapper around an already-unguarded call."""
    import asyncio
    from types import SimpleNamespace as _SimpleNamespace

    from octowright.http.routes import media as _media

    log_path = isolated_recordings / "20260101T000000Z-chromium-gatewait0001.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    session = _live_session(log_path, instance_id="gatewait0001")
    empty_pool["pool"]._sessions["gatewait0001"] = session

    async with session.operation("owner"):
        request = _SimpleNamespace(path_params={"id": "gatewait0001"}, query_params={})
        response_task = asyncio.create_task(_media.session_screenshot_now(request))

        async with asyncio.timeout(1):
            while session.operation_snapshot()["queue_depth"] != 1:
                await asyncio.sleep(0)
        session.page.screenshot.assert_not_awaited()

    response = await asyncio.wait_for(response_task, timeout=1.0)
    assert response.status_code == 200
    session.page.screenshot.assert_awaited_once()


@pytest.mark.anyio
async def test_live_screenshot_gate_busy_times_out_fast_not_300s(
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dashboard_screenshot must fail fast on a busy gate instead of
    inheriting the operation gate's 300s MCP-tool default -- a human
    watching the dashboard needs a legible failure, not a silent multi-
    minute stall. Overrides the dashboard timeout down to make the real
    gate's timeout path exercisable in a fast test without waiting out the
    real default."""
    import asyncio
    import time
    from types import SimpleNamespace as _SimpleNamespace

    from octowright.http.routes import media as _media

    monkeypatch.setenv("OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS", "0.05")

    log_path = isolated_recordings / "20260101T000000Z-chromium-gatebusy0001.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    session = _live_session(log_path, instance_id="gatebusy0001")
    empty_pool["pool"]._sessions["gatebusy0001"] = session

    release = asyncio.Event()

    async def _hold() -> None:
        async with session.operation("owner"):
            await release.wait()

    holder = asyncio.create_task(_hold())
    async with asyncio.timeout(1):
        while session.operation_snapshot()["active_operation"] != "owner":
            await asyncio.sleep(0)

    request = _SimpleNamespace(path_params={"id": "gatebusy0001"}, query_params={})
    started = time.monotonic()
    response = await asyncio.wait_for(_media.session_screenshot_now(request), timeout=2.0)
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    # Comfortably under the 300s MCP-tool default -- proves the dashboard
    # override is actually in effect, not just documented.
    assert elapsed < 1.0
    session.page.screenshot.assert_not_awaited()

    release.set()
    await holder


@pytest.mark.anyio
async def test_live_screenshot_session_busy_timeout_error_maps_to_503(
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    """A SessionBusyTimeoutError from the gate maps to 503, distinctly from
    the generic screenshot-failure 503 branch -- locks in Task 10's Step 5
    error-mapping contract."""
    from types import SimpleNamespace as _SimpleNamespace

    from octowright.http.routes import media as _media
    from octowright.session.operation.gate import SessionBusyTimeoutError

    log_path = isolated_recordings / "20260101T000000Z-chromium-busyerr00001.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    session = _live_session(log_path, instance_id="busyerr00001")

    session.operation = _RaisingOperation(
        SessionBusyTimeoutError("session 'busyerr00001' operation 'dashboard_screenshot' timed out")
    )
    empty_pool["pool"]._sessions["busyerr00001"] = session

    request = _SimpleNamespace(path_params={"id": "busyerr00001"}, query_params={})
    response = await _media.session_screenshot_now(request)

    assert response.status_code == 503
    session.page.screenshot.assert_not_awaited()


@pytest.mark.anyio
async def test_live_screenshot_session_closing_error_maps_to_409(
    isolated_recordings: Path,
    empty_pool: dict[str, Any],
) -> None:
    """A SessionClosingError from the gate maps to 409, not the generic
    screenshot-failure 503 branch -- locks in Task 10's Step 5 error-mapping
    contract."""
    from types import SimpleNamespace as _SimpleNamespace

    from octowright.http.routes import media as _media
    from octowright.session.operation.gate import SessionClosingError

    log_path = isolated_recordings / "20260101T000000Z-chromium-closingerr01.jsonl"
    log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
    session = _live_session(log_path, instance_id="closingerr01")

    session.operation = _RaisingOperation(
        SessionClosingError("session 'closingerr01' operation 'dashboard_screenshot' rejected: gate is closing")
    )
    empty_pool["pool"]._sessions["closingerr01"] = session

    request = _SimpleNamespace(path_params={"id": "closingerr01"}, query_params={})
    response = await _media.session_screenshot_now(request)

    assert response.status_code == 409
    session.page.screenshot.assert_not_awaited()


# ─── /frame edge cases ──────────────────────────────────────────────────────


class TestFrameEdgeCases:
    def test_extract_raises_500(
        self,
        client: TestClient,
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """extract_frames raises → 500 with 'frame extraction failed' wording."""
        video_path = isolated_recordings / "videos" / "stub.webm"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00")
        name = "20260101T000000Z-chromium-frmboom00001"
        rows = [
            {"action": "launch", "kind": "chromium"},
            {"action": "close", "video_path": str(video_path), "trace_path": None},
        ]
        (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

        def boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("ffmpeg crashed")

        monkeypatch.setattr(_http_state._video, "extract_frames", boom)
        r = client.get("/api/sessions/frmboom00001/frame?t=0.5")
        assert r.status_code == 500
        assert "frame extraction failed" in r.json()["error"]

    def test_extract_produces_no_file_500(
        self,
        client: TestClient,
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """extract_frames returns silently but no PNG appears on disk → 500."""
        video_path = isolated_recordings / "videos" / "stub.webm"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00")
        name = "20260101T000000Z-chromium-frmnofile0001"
        rows = [
            {"action": "launch", "kind": "chromium"},
            {"action": "close", "video_path": str(video_path), "trace_path": None},
        ]
        (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

        # Stub silently returns nothing; the route's "else: not cached.exists()" branch fires.
        monkeypatch.setattr(_http_state._video, "extract_frames", lambda *a, **kw: None)
        r = client.get("/api/sessions/frmnofile0001/frame?t=0.5")
        assert r.status_code == 500
        assert "no file" in r.json()["error"]

    def test_extract_produces_at_target_path_no_rename(
        self,
        client: TestClient,
        isolated_recordings: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If extract_frames already wrote to the cached path, no rename needed."""
        video_path = isolated_recordings / "videos" / "stub.webm"
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(b"\x00")
        name = "20260101T000000Z-chromium-frmnornm00001"
        rows = [
            {"action": "launch", "kind": "chromium"},
            {"action": "close", "video_path": str(video_path), "trace_path": None},
        ]
        (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

        def emit_at_cache_target(vp: Path, out_dir: Path, *, at_times: list[float], **_kw: Any) -> Any:
            out_dir.mkdir(parents=True, exist_ok=True)
            # Emit at exactly the cache-target filename rather than `frame-000-tX.png`.
            cached_target = out_dir / f"{at_times[0]:.3f}.png"
            cached_target.write_bytes(_TINY_PNG)

        monkeypatch.setattr(_http_state._video, "extract_frames", emit_at_cache_target)
        r = client.get("/api/sessions/frmnornm00001/frame?t=2.5")
        assert r.status_code == 200
        assert r.content == _TINY_PNG


# ─── /video, /trace filename header ────────────────────────────────────────


class TestVideoTraceFilename:
    def test_video_filename_header_set(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """FileResponse uses video_path.name → Content-Disposition includes filename."""
        name = "20260101T000000Z-chromium-vidfilename1"
        webm = isolated_recordings / "videos" / "vidfilename1.webm"
        webm.parent.mkdir(parents=True, exist_ok=True)
        webm.write_bytes(b"x")
        rows = [
            {"action": "launch", "kind": "chromium"},
            {"action": "close", "video_path": str(webm), "trace_path": None},
        ]
        (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        r = client.get("/api/sessions/vidfilename1/video")
        assert r.status_code == 200
        assert r.headers["content-type"] == "video/webm"
        assert "vidfilename1.webm" in r.headers.get("content-disposition", "")

    def test_trace_filename_header_set(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """FileResponse uses trace_path.name → Content-Disposition includes filename."""
        name = "20260101T000000Z-chromium-trcfilename1"
        zip_ = isolated_recordings / f"{name}.trace.zip"
        zip_.write_bytes(b"PK\x03\x04")
        rows = [
            {"action": "launch", "kind": "chromium"},
            {"action": "close", "video_path": None, "trace_path": str(zip_)},
        ]
        (isolated_recordings / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        r = client.get("/api/sessions/trcfilename1/trace")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert ".trace.zip" in r.headers.get("content-disposition", "")


# ─── /markdown live-session paths ──────────────────────────────────────────


class TestMarkdownLive:
    def test_live_session_with_existing_cache_returns_text(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """Live session + existing markdown cache → returns the text directly."""
        name = "20260101T000000Z-chromium-mdlivecache1"
        log_path = isolated_recordings / f"{name}.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        md = log_path.with_suffix(".markdown.md")
        md.write_text("# already cached\n", encoding="utf-8")
        live = _live_session(log_path, markdown_path=md)
        live.capture_markdown = AsyncMock(side_effect=AssertionError("must not be called"))
        empty_pool["pool"]._sessions["mdlivecache1"] = live
        r = client.get("/api/sessions/mdlivecache1/markdown")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        assert "already cached" in r.text

    def test_live_session_first_request_triggers_capture(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """No markdown_path on live session → capture_markdown invoked, then served."""
        name = "20260101T000000Z-chromium-mdlivefresh1"
        log_path = isolated_recordings / f"{name}.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        md = log_path.with_suffix(".markdown.md")
        live = _live_session(log_path, markdown_path=None)
        capture_called = {"n": 0}

        async def capture() -> Path:
            capture_called["n"] += 1
            md.write_text("# fresh capture\n", encoding="utf-8")
            live.markdown_path = md
            return md

        live.capture_markdown = capture
        empty_pool["pool"]._sessions["mdlivefresh1"] = live
        r = client.get("/api/sessions/mdlivefresh1/markdown")
        assert r.status_code == 200
        assert capture_called["n"] == 1
        assert "fresh capture" in r.text

    def test_live_session_capture_raises_500(
        self,
        client: TestClient,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
    ) -> None:
        """capture_markdown raises → 500 with 'could not generate markdown' wording."""
        name = "20260101T000000Z-chromium-mdliveboom01"
        log_path = isolated_recordings / f"{name}.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        live = _live_session(log_path, markdown_path=None)
        live.capture_markdown = AsyncMock(side_effect=RuntimeError("page closed"))
        empty_pool["pool"]._sessions["mdliveboom01"] = live
        r = client.get("/api/sessions/mdliveboom01/markdown")
        assert r.status_code == 500
        assert "could not generate markdown" in r.json()["error"]

    def test_markdown_round_trips_utf8(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Closed session: non-ASCII content survives the read+response roundtrip."""
        name = "20260101T000000Z-chromium-mdunicode001"
        log_path = isolated_recordings / f"{name}.jsonl"
        log_path.write_text(json.dumps({"action": "launch", "kind": "chromium"}) + "\n")
        md = log_path.with_suffix(".markdown.md")
        # Pile of poo + Cyrillic + accented Latin to be sure.
        md.write_text("# 💩 Привет café\n", encoding="utf-8")
        r = client.get("/api/sessions/mdunicode001/markdown")
        assert r.status_code == 200
        assert "💩" in r.text
        assert "Привет" in r.text
        assert "café" in r.text


# ─── /screenshots listing ──────────────────────────────────────────────────


class TestScreenshotsListing:
    def test_unknown_session_404(self, client: TestClient) -> None:
        """No log path resolvable → 404 with 'no session with id' wording."""
        r = client.get("/api/sessions/missingsess0/screenshots")
        assert r.status_code == 404
        assert "no session with id" in r.json()["error"]

    def test_empty_dir_returns_empty_list(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Recording dir exists but has no matching pngs → []."""
        _write_recording(isolated_recordings, "ssempty00001")
        r = client.get("/api/sessions/ssempty00001/screenshots")
        assert r.status_code == 200
        assert r.json() == {"screenshots": []}

    def test_field_shape_pinned(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Each entry carries path/filename/ts/size_bytes — every key load-bearing for the dashboard."""
        _write_recording(isolated_recordings, "ssfields0001")
        shot = isolated_recordings / "ssfields0001-shot.png"
        shot.write_bytes(_TINY_PNG)
        r = client.get("/api/sessions/ssfields0001/screenshots")
        assert r.status_code == 200
        body = r.json()
        assert len(body["screenshots"]) == 1
        item = body["screenshots"][0]
        assert set(item.keys()) == {"path", "filename", "ts", "size_bytes"}
        assert item["filename"] == "ssfields0001-shot.png"
        assert item["size_bytes"] == len(_TINY_PNG)
        assert item["path"].endswith("ssfields0001-shot.png")

    def test_results_are_sorted_alphabetically(
        self,
        client: TestClient,
        isolated_recordings: Path,
    ) -> None:
        """Glob order may not be deterministic; route sorts by path."""
        _write_recording(isolated_recordings, "sssort000001")
        for filename in ("sssort000001-c.png", "sssort000001-a.png", "sssort000001-b.png"):
            (isolated_recordings / filename).write_bytes(_TINY_PNG)
        r = client.get("/api/sessions/sssort000001/screenshots")
        names = [s["filename"] for s in r.json()["screenshots"]]
        assert names == sorted(names)

    def test_screenshot_file_unknown_session_404(self, client: TestClient) -> None:
        """No log path resolvable → 404 (not 400)."""
        r = client.get("/api/sessions/missingsess1/screenshots/x.png")
        assert r.status_code == 404

    @pytest.mark.parametrize(
        "filename",
        [
            "../../etc/passwd",  # decoded ../../etc/passwd
            "../evil.png",  # decoded ../evil.png
            "/etc/shadow",  # absolute path escape
        ],
    )
    @pytest.mark.asyncio
    async def test_screenshot_file_path_traversal_rejected(
        self,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
        filename: str,
    ) -> None:
        """Defence-in-depth: filename that escapes the session screenshot
        directory must return 400, NOT serve the off-tree file.

        Note on testing: Starlette's ``{filename}`` path converter does not
        match ``/`` so HTTP requests like ``GET .../screenshots/..%2Fevil``
        return Starlette's own 404 (the segment can't be routed). The path
        traversal guard exists for defence-in-depth — to catch the case
        where some future caller constructs the request with a router/path
        converter that allows ``/`` in the filename, or invokes the handler
        directly. We therefore exercise the handler with a hand-built
        request whose ``path_params`` carries the malicious filename.
        """
        from starlette.requests import Request

        from octowright.http.routes import media as media_routes

        _write_recording(isolated_recordings, "trav00000001")

        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/api/sessions/trav00000001/screenshots/x",
            "headers": [],
            "path_params": {"id": "trav00000001", "filename": filename},
            "query_string": b"",
        }

        async def _receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        request = Request(scope, _receive)
        response = await media_routes.session_screenshot_file(request)
        assert response.status_code == 400
        body = json.loads(response.body)
        assert body["error"] == "invalid filename"

    @pytest.mark.asyncio
    async def test_screenshot_file_passes_resolved_path_to_file_response(
        self,
        isolated_recordings: Path,
        empty_pool: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """TOCTOU regression: FileResponse must receive the already-validated
        ``resolved`` path, not the pre-resolve ``target``. Without this, a
        symlink swap between the containment check and FileResponse's
        ``open()`` could redirect to a file outside the recordings root."""
        from starlette.requests import Request

        from octowright.http.routes import media as media_routes

        _write_recording(isolated_recordings, "regr00000001")
        shot_path = isolated_recordings / "regr00000001-shot.png"
        shot_path.write_bytes(_TINY_PNG)

        captured: dict[str, Any] = {}

        class _FakeFileResponse:
            def __init__(self, path: str, **kwargs: Any) -> None:
                captured["path"] = path
                captured["kwargs"] = kwargs
                self.status_code = 200
                self.body = b""

        monkeypatch.setattr(media_routes, "FileResponse", _FakeFileResponse)

        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/api/sessions/regr00000001/screenshots/regr00000001-shot.png",
            "headers": [],
            "path_params": {
                "id": "regr00000001",
                "filename": "regr00000001-shot.png",
            },
            "query_string": b"",
        }

        async def _receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        request = Request(scope, _receive)
        await media_routes.session_screenshot_file(request)

        # The path passed must equal Path.resolve() of the target. Pin against
        # the resolved form so a future regression to ``str(target)`` (the
        # unresolved path) is caught.
        assert "path" in captured, "FileResponse was not invoked"
        passed_path = Path(captured["path"])
        assert passed_path == shot_path.resolve(), (
            f"FileResponse got {passed_path} but expected the resolved path {shot_path.resolve()}; "
            "passing the unresolved path leaves a TOCTOU window for a symlink swap."
        )
