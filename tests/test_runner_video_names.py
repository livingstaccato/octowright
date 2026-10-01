# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""`--record-video` copies: every name unique in its directory, nothing overwritten,
an empty video retried then skipped, and page numbers that stay page numbers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from octowright import defaults, runner_video


@pytest.fixture
def recordings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    rec = tmp_path / "recordings"
    rec.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", rec)
    return rec


class FakeVideo:
    """A finalised page video. *body* ``b""`` models Playwright's empty file;
    *saved* is what ``save_as`` writes, as the real call finishes the file."""

    def __init__(self, path: Path, body: bytes, *, saved: bytes | None = None, resolves: bool = True) -> None:
        self._path = path
        self._saved = saved
        self._resolves = resolves
        self.save_as_calls: list[str] = []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    async def path(self) -> str:
        if not self._resolves:
            raise RuntimeError("page closed before its video resolved")
        return str(self._path)

    async def save_as(self, target: str) -> None:
        self.save_as_calls.append(target)
        if self._saved is not None:
            Path(target).write_bytes(self._saved)


def run_videos(*videos: FakeVideo) -> runner_video.RunVideos:
    watched = runner_video.RunVideos()
    for video in videos:
        watched._add(SimpleNamespace(video=video))
    return watched


def finalise(watched: runner_video.RunVideos, artifacts: Path, stem: str, names: Any = None) -> list[Path]:
    return asyncio.run(watched.finalise(artifacts=artifacts, stem=stem, names=names))


def src(recordings: Path, name: str) -> Path:
    return recordings / "videos" / "launch" / name


@pytest.mark.parametrize(
    ("first", "second"),
    [("a b", "a_b"), ("Login", "login"), ("x-2", "x")],
    ids=["sanitised-alike", "case-only", "page-number-alike"],
)
def test_two_stems_that_map_to_one_name_get_two_files(recordings: Path, first: str, second: str) -> None:
    artifacts = recordings / "evidence"
    names = runner_video.VideoNames()
    one = finalise(run_videos(FakeVideo(src(recordings, "1.webm"), b"one")), artifacts, first, names)
    # `x` with two pages would name its second page x-2.webm: the first test's file.
    two_pages = [FakeVideo(src(recordings, "2.webm"), b"two"), FakeVideo(src(recordings, "3.webm"), b"three")]
    two = finalise(run_videos(*two_pages), artifacts, second, names)
    paths = [*one, *two]
    assert len({p.name.casefold() for p in paths}) == len(paths)
    assert sorted(p.read_bytes() for p in paths) == [b"one", b"three", b"two"]
    assert one[0].read_bytes() == b"one"


def test_an_existing_file_is_never_overwritten(recordings: Path) -> None:
    artifacts = recordings / "evidence"
    artifacts.mkdir()
    (artifacts / "smoke.webm").write_bytes(b"yesterday")
    [copied] = finalise(run_videos(FakeVideo(src(recordings, "1.webm"), b"today")), artifacts, "smoke")
    assert (artifacts / "smoke.webm").read_bytes() == b"yesterday"
    assert copied == (artifacts / "smoke_2.webm").resolve()
    assert copied.read_bytes() == b"today"


def test_an_existing_file_differing_only_in_case_is_not_overwritten(recordings: Path) -> None:
    artifacts = recordings / "evidence"
    artifacts.mkdir()
    (artifacts / "SMOKE.webm").write_bytes(b"yesterday")
    [copied] = finalise(run_videos(FakeVideo(src(recordings, "1.webm"), b"today")), artifacts, "smoke")
    assert (artifacts / "SMOKE.webm").read_bytes() == b"yesterday"
    assert copied.name == "smoke_2.webm"


def test_an_empty_video_is_saved_again_before_it_is_copied(recordings: Path) -> None:
    video = FakeVideo(src(recordings, "1.webm"), b"", saved=b"finished")
    [copied] = finalise(run_videos(video), recordings / "evidence", "smoke")
    assert video.save_as_calls == [str(src(recordings, "1.webm"))]
    assert copied.read_bytes() == b"finished"


def test_a_video_still_empty_after_the_retry_is_skipped_and_flagged(recordings: Path) -> None:
    video = FakeVideo(src(recordings, "1.webm"), b"", saved=None)
    with patch.object(runner_video, "log") as log:
        copied = finalise(run_videos(video), recordings / "evidence", "smoke")
    assert copied == []
    assert not list((recordings / "evidence").glob("*.webm"))
    assert any(call.args[:1] == ("octowright.runner.video_empty",) for call in log.warning.mock_calls)


def test_a_video_with_content_is_not_saved_again(recordings: Path) -> None:
    video = FakeVideo(src(recordings, "1.webm"), b"complete")
    finalise(run_videos(video), recordings / "evidence", "smoke")
    assert video.save_as_calls == []


def test_pages_keep_their_numbers_when_an_earlier_one_is_dropped(recordings: Path) -> None:
    first = FakeVideo(src(recordings, "1.webm"), b"one", resolves=False)
    second = FakeVideo(src(recordings, "2.webm"), b"two")
    [copied] = finalise(run_videos(first, second), recordings / "evidence", "smoke")
    assert copied.name == "smoke-2.webm"


def test_without_artifacts_an_empty_video_is_not_reported(recordings: Path) -> None:
    empty = FakeVideo(src(recordings, "1.webm"), b"", saved=None)
    full = FakeVideo(src(recordings, "2.webm"), b"two")
    assert asyncio.run(run_videos(empty, full).finalise(artifacts=None, stem="s")) == [src(recordings, "2.webm")]
