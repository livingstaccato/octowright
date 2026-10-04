# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``GET /api/sessions/{id}/frame?t=`` bounds what one query string can cost.

``t`` went straight from ``float()`` into an ffmpeg run and a cache filename:
``nan``/``inf``/negative values were accepted, ``1e300`` formatted to a
300-digit filename (ENAMETOOLONG, answered 500), and every distinct value --
``0.5``, ``0.5001``, ``0.50001`` ... -- started its own ffmpeg and left its own
PNG behind, with nothing bounding either.
"""

# ruff: noqa: F811  (pytest fixtures imported from a sibling test module)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from octowright.http import state as _http_state
from octowright.http.routes import media
from tests.test_http_server import (  # noqa: F401  (fixtures re-exported for pytest)
    _TINY_PNG,
    client,
    empty_pool,
    isolated_recordings,
)


def _session_with_video(recordings: Path, sid: str) -> Path:
    video_path = recordings / "videos" / f"{sid}.webm"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"\x00")
    rows = [
        {"action": "launch", "kind": "chromium"},
        {"action": "close", "video_path": str(video_path), "trace_path": None},
    ]
    (recordings / f"20260101T000000Z-chromium-{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return video_path


@pytest.fixture
def extractions(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every ffmpeg extraction and write a frame where the route looks."""
    calls: list[float] = []

    def fake_extract(_video: Path, out_dir: Path, *, at_times: list[float], **_kw: Any) -> None:
        calls.append(at_times[0])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"frame-000-t{at_times[0]:.3f}.png").write_bytes(_TINY_PNG)

    monkeypatch.setattr(_http_state._video, "extract_frames", fake_extract)
    monkeypatch.setattr(_http_state._video, "probe_video", lambda _p: {"duration_seconds": 10.0})
    media._VIDEO_DURATIONS.clear()
    return calls


@pytest.mark.parametrize("raw_t", ["nan", "inf", "-inf", "-1", "1e300", "-0.5"])
def test_a_non_finite_negative_or_absurd_time_is_refused_without_running_ffmpeg(
    client: TestClient, isolated_recordings: Path, extractions: list[float], raw_t: str
) -> None:
    _session_with_video(isolated_recordings, "frmbound0001")

    r = client.get(f"/api/sessions/frmbound0001/frame?t={raw_t}")

    assert r.status_code == 400
    assert extractions == []


def test_a_time_past_the_end_of_the_video_is_refused(
    client: TestClient, isolated_recordings: Path, extractions: list[float]
) -> None:
    _session_with_video(isolated_recordings, "frmbound0002")

    r = client.get("/api/sessions/frmbound0002/frame?t=10.5")

    assert r.status_code == 400
    assert "duration" in r.json()["error"]
    assert extractions == []


def test_an_unprobeable_video_still_gets_the_absolute_bound(
    client: TestClient, isolated_recordings: Path, extractions: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_ffprobe(_p: Path) -> dict[str, float]:
        raise RuntimeError("ffprobe not found on PATH")

    monkeypatch.setattr(_http_state._video, "probe_video", no_ffprobe)
    _session_with_video(isolated_recordings, "frmbound0003")

    assert client.get("/api/sessions/frmbound0003/frame?t=12").status_code == 200
    assert client.get("/api/sessions/frmbound0003/frame?t=1e300").status_code == 400


def test_nearby_times_share_one_extraction(
    client: TestClient, isolated_recordings: Path, extractions: list[float]
) -> None:
    _session_with_video(isolated_recordings, "frmbound0004")

    for raw_t in ("1.5", "1.5001", "1.50004", "1.53"):
        r = client.get(f"/api/sessions/frmbound0004/frame?t={raw_t}")
        assert r.status_code == 200
        assert r.content == _TINY_PNG

    assert extractions == [1.5]


def test_the_frame_cache_is_bounded_per_session(
    client: TestClient,
    isolated_recordings: Path,
    extractions: list[float],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media, "FRAME_CACHE_MAX_FILES", 3)
    _session_with_video(isolated_recordings, "frmbound0005")

    for i in range(6):
        assert client.get(f"/api/sessions/frmbound0005/frame?t={i}").status_code == 200

    cache_dir = isolated_recordings / ".frame-cache" / "frmbound0005"
    assert len(list(cache_dir.glob("*.png"))) <= 3


@pytest.mark.asyncio
async def test_waiting_extractions_do_not_occupy_the_default_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Extractions queued behind the concurrency bound used to sit in default
    executor threads blocked on a semaphore, starving every other
    ``to_thread``/``run_in_executor(None, ...)`` user in the daemon."""
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    release = threading.Event()

    def blocking_extract(_video: Path, out_dir: Path, *, at_times: list[float], **_kw: Any) -> None:
        release.wait(5)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"frame-000-t{at_times[0]:.3f}.png").write_bytes(_TINY_PNG)

    monkeypatch.setattr(_http_state._video, "extract_frames", blocking_extract)
    loop = asyncio.get_running_loop()
    small = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(small)
    video = tmp_path / "v.webm"
    video.write_bytes(b"\x00")
    try:
        pending = [
            asyncio.create_task(media._extract_into_cache(video, tmp_path / f"c{i}" / "f.png", float(i)))
            for i in range(4)
        ]
        await asyncio.sleep(0.05)
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 1) == "free"
    finally:
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)
        small.shutdown(wait=False)

