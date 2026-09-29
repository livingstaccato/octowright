# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`octowright test --record-video`: the runner half, driven with a fake pool.

The fake reproduces the one property that matters, measured against real
Playwright on all three engines: a page's .webm exists but is EMPTY until its
context closes, and only ``Video.path()`` after the close names a complete
file. The fake pool writes the bytes inside ``close``, so a copy taken before
the close would copy nothing and fail these tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from octowright import defaults, runner


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    return rec


class FakeVideo:
    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")  # Playwright creates the file up front, empty

    async def path(self) -> str:
        return str(self._path)

    def finalise(self, body: bytes) -> None:
        self._path.write_bytes(body)


class FakePage:
    def __init__(self, video: FakeVideo | None) -> None:
        self.video = video


class FakeContext:
    def __init__(self, pages: list[FakePage]) -> None:
        self.pages = list(pages)
        self._listeners: dict[str, list[Callable[[Any], None]]] = {}

    def on(self, event: str, callback: Callable[[Any], None]) -> None:
        self._listeners.setdefault(event, []).append(callback)

    def open_page(self, page: FakePage) -> None:
        self.pages.append(page)
        for callback in self._listeners.get("page", []):
            callback(page)


def video_pool(recordings: Path, *, extra_pages: int = 0) -> tuple[MagicMock, FakeContext, list[FakeVideo]]:
    """A pool whose one launch records a video that close() finalises."""
    videos_dir = recordings / "videos" / "launch"
    videos = [FakeVideo(videos_dir / "page@first.webm")]
    context = FakeContext([FakePage(videos[0])])
    session = MagicMock()
    session.context = context
    for n in range(extra_pages):
        videos.append(FakeVideo(videos_dir / f"page@extra{n}.webm"))

    async def close(_iid: str, **_: Any) -> dict[str, Any]:
        for n, video in enumerate(videos):
            video.finalise(f"webm-{n}".encode())
        return {"closed": True}

    pool = MagicMock()
    pool.launch = AsyncMock(return_value={"instance_id": "abc123"})
    pool.get = MagicMock(return_value=session)
    pool.close = AsyncMock(side_effect=close)
    return pool, context, videos


def sequence_file(tmp_path: Path, steps: list[dict[str, Any]], name: str = "repair.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(steps))
    return path


def run_seq(tmp_path: Path, recordings: Path, pool: Any, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "sequence": sequence_file(tmp_path, [{"macro": "m1"}, {"macro": "m2"}]),
        "kind": "chromium",
        "persona": None,
        "artifacts": recordings / "evidence",
        "redact_errors": False,
        "out_path": None,
        "pool": pool,
    }
    kwargs.update(overrides)
    return asyncio.run(runner.run_sequence_file(**kwargs))


async def passing(*_: Any, **__: Any) -> dict[str, Any]:
    return {"executed": 1}


def test_the_flag_reaches_pool_launch(tmp_path: Path, recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    with patch("octowright.runner.macro_mod.run_macro", side_effect=passing):
        run_seq(tmp_path, recordings, pool, videos=[])
    assert pool.launch.await_args.kwargs["record_video"] is True


def test_without_the_flag_record_video_is_not_passed(tmp_path: Path, recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    with patch("octowright.runner.macro_mod.run_macro", side_effect=passing):
        result = run_seq(tmp_path, recordings, pool)
    assert "record_video" not in pool.launch.await_args.kwargs
    assert not (recordings / "evidence" / "repair.webm").exists()
    assert "video_paths" not in result


def test_the_finalised_video_is_copied_under_the_sequence_stem(tmp_path: Path, recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    videos: list[Path] = []
    with patch("octowright.runner.macro_mod.run_macro", side_effect=passing):
        run_seq(tmp_path, recordings, pool, videos=videos)
    target = (recordings / "evidence" / "repair.webm").resolve()
    assert videos == [target]
    assert target.read_bytes() == b"webm-0"


def test_extra_pages_are_numbered_in_the_order_they_opened(tmp_path: Path, recordings: Path) -> None:
    pool, context, fakes = video_pool(recordings, extra_pages=2)

    async def opens_pages(*_: Any, **__: Any) -> dict[str, Any]:
        for video in fakes[1:]:
            context.open_page(FakePage(video))
        context.open_page(FakePage(None))  # a page without a video is not an error
        return {"executed": 1}

    videos: list[Path] = []
    with patch("octowright.runner.macro_mod.run_macro", side_effect=opens_pages):
        run_seq(tmp_path, recordings, pool, videos=videos)
    evidence = (recordings / "evidence").resolve()
    assert videos == [evidence / "repair.webm", evidence / "repair-2.webm", evidence / "repair-3.webm"]
    assert [v.read_bytes() for v in videos] == [b"webm-0", b"webm-1", b"webm-2"]


def test_a_failed_sequence_still_leaves_its_video(tmp_path: Path, recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)

    async def fails(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError({"macro": "m1", "failed_at_step": 0, "failed_action": {"action": "click"}})

    videos: list[Path] = []
    with patch("octowright.runner.macro_mod.run_macro", side_effect=fails):
        result = run_seq(tmp_path, recordings, pool, videos=videos, redact_errors=True)
    assert result["failed"] == 2
    assert videos == [(recordings / "evidence" / "repair.webm").resolve()]
    assert videos[0].read_bytes() == b"webm-0"


def test_an_exception_escaping_mid_sequence_still_leaves_its_video(tmp_path: Path, recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)

    async def interrupted(*_: Any, **__: Any) -> dict[str, Any]:
        raise asyncio.CancelledError  # not an Exception: escapes the per-step handler

    videos: list[Path] = []
    with (
        patch("octowright.runner.macro_mod.run_macro", side_effect=interrupted),
        pytest.raises(asyncio.CancelledError),
    ):
        run_seq(tmp_path, recordings, pool, videos=videos)
    pool.close.assert_awaited_once()
    assert videos == [(recordings / "evidence" / "repair.webm").resolve()]
    assert videos[0].read_bytes() == b"webm-0"


def test_a_close_that_raises_still_reports_the_video(tmp_path: Path, recordings: Path) -> None:
    pool, _, fakes = video_pool(recordings)

    async def close_fails(_iid: str, **_: Any) -> dict[str, Any]:
        fakes[0].finalise(b"partial")
        raise RuntimeError("context.close timed out")

    pool.close = AsyncMock(side_effect=close_fails)
    videos: list[Path] = []
    with (
        patch("octowright.runner.macro_mod.run_macro", side_effect=passing),
        pytest.raises(RuntimeError, match="timed out"),
    ):
        run_seq(tmp_path, recordings, pool, videos=videos)
    assert videos == [(recordings / "evidence" / "repair.webm").resolve()]


def test_without_artifacts_the_video_stays_in_the_launch_videos_dir(tmp_path: Path, recordings: Path) -> None:
    pool, _, fakes = video_pool(recordings)
    videos: list[Path] = []
    with patch("octowright.runner.macro_mod.run_macro", side_effect=passing):
        run_seq(tmp_path, recordings, pool, videos=videos, artifacts=None, out_path=str(recordings / "r.xml"))
    assert videos == [Path(asyncio.run(fakes[0].path()))]
    assert videos[0].read_bytes() == b"webm-0"


def test_a_copy_that_fails_reports_the_original_instead(tmp_path: Path, recordings: Path) -> None:
    pool, _, fakes = video_pool(recordings)
    videos: list[Path] = []
    with (
        patch("octowright.runner.macro_mod.run_macro", side_effect=passing),
        patch("octowright.runner_video.shutil.copyfile", side_effect=OSError("disk full")),
    ):
        run_seq(tmp_path, recordings, pool, videos=videos)
    assert videos == [Path(asyncio.run(fakes[0].path()))]
    assert not list((recordings / "evidence").glob("*.webm"))


def suite_patches(names: list[str]) -> Any:
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(patch("octowright.runner.macro_mod.list_macros", return_value=[{"name": n} for n in names]))
    stack.enter_context(
        patch(
            "octowright.runner.macro_mod.load_macro",
            side_effect=lambda n: {"name": n, "description": "[test] t", "actions": []},
        )
    )
    stack.enter_context(patch("octowright.runner.macro_mod.run_macro", side_effect=passing))
    return stack


def test_suite_copies_one_video_per_test_named_after_the_macro(recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    videos: list[Path] = []
    with suite_patches(["login"]):
        asyncio.run(
            runner.run_suite(
                kind="chromium",
                pool=pool,
                out_path=str(recordings / "s.xml"),
                artifacts=recordings / "evidence",
                videos=videos,
            )
        )
    assert pool.launch.await_args.kwargs["record_video"] is True
    assert videos == [(recordings / "evidence" / "login.webm").resolve()]


def test_suite_without_the_flag_passes_no_record_video(recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    with suite_patches(["login"]):
        asyncio.run(runner.run_suite(kind="chromium", pool=pool, out_path=str(recordings / "s.xml")))
    assert "record_video" not in pool.launch.await_args.kwargs


def test_a_macro_name_cannot_steer_the_copy_out_of_artifacts(recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    videos: list[Path] = []
    with suite_patches(["../../escape"]):
        asyncio.run(
            runner.run_suite(
                kind="chromium",
                pool=pool,
                out_path=str(recordings / "s.xml"),
                artifacts=recordings / "evidence",
                videos=videos,
            )
        )
    assert len(videos) == 1
    assert videos[0].parent == (recordings / "evidence").resolve()
    assert not (recordings.parent / "escape.webm").exists()


def test_suite_refuses_artifacts_outside_the_recordings_root(tmp_path: Path, recordings: Path) -> None:
    pool, _, _ = video_pool(recordings)
    with suite_patches(["login"]), pytest.raises(ValueError, match="artifacts directory"):
        asyncio.run(
            runner.run_suite(
                kind="chromium",
                pool=pool,
                out_path=str(recordings / "s.xml"),
                artifacts=tmp_path / "elsewhere",
                videos=[],
            )
        )
    pool.launch.assert_not_called()
