# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Videos for ``octowright test --record-video``.

A launch with ``record_video`` gives each page its own ``Video``. The file
exists from the moment the page opens but is EMPTY until the context closes --
measured on chromium, firefox and webkit: zero bytes before
``context.close()``, a complete WebM (EBML magic ``1a45dfa3``) from
``Video.path()`` immediately after it. So the videos are collected while the
run is live and read only once the pool has closed the browser, never by
polling a file for "done".

Naming, when an artifacts directory is given: the launch page's video is
``<stem>.webm`` and every later page's is ``<stem>-<n>.webm``, where *n* is the
page's position in the order the pages opened (from 2), kept even when an
earlier page's video is dropped. The stem is the sequence file's stem, or the
macro name for a ``[test]`` suite, reduced to a safe file name. A name is
never reused: when one is already taken -- by another test or page of the same
run, or by a file already in the directory, compared case-insensitively
because macOS and Windows file systems are -- ``_2``, ``_3``, ... is added
before ``.webm``. Nothing is overwritten. Without an artifacts directory the
video stays where Playwright wrote it, in the launch's own videos directory
under the recordings root.

A video still empty after the close is saved once more through
``Video.save_as`` (the call that waits for the file, as session teardown does
for the launch page) and, if still empty, left out and logged as
``octowright.runner.video_empty``: an empty file is not a video anyone can open.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright import defaults
from octowright._paths import atomic_write_via_writer, reject_unsafe_path
from octowright.session.timeouts import bounded

log = get_logger(__name__)

_UNSAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
#: How many ``_<k>`` suffixes a name tries before giving up on a copy.
_MAX_NAME_ATTEMPTS = 1000


def launch_kwargs(videos: list[Path] | None) -> dict[str, Any]:
    """``record_video=True`` when videos were asked for, and nothing otherwise,
    so a run without ``--record-video`` launches exactly as it always did."""
    return {"record_video": True} if videos is not None else {}


def artifacts_root(artifacts: Path | None) -> Path | None:
    """The artifacts directory, refused unless it sits under the recordings root."""
    if artifacts is None:
        return None
    return reject_unsafe_path(Path(artifacts), defaults.RECORDINGS_DIR, label="artifacts directory")


def video_name(stem: str, page: int) -> str:
    """The name page *page* (from 1) of a run with *stem* asks for."""
    safe = _UNSAFE_STEM.sub("_", stem).strip("._") or "run"
    return f"{safe}.webm" if page == 1 else f"{safe}-{page}.webm"


class VideoNames:
    """The file names one run has claimed, so no two of its videos share one.

    Shared by every test of a suite: ``a b`` and ``a_b`` sanitise alike, macro
    ``x-2`` asks for the name page 2 of macro ``x`` would, and ``Login`` and
    ``login`` are one file on a case-insensitive file system.
    """

    def __init__(self) -> None:
        self._taken: set[tuple[str, str]] = set()

    def claim(self, directory: Path, wanted: str) -> Path:
        """Create and return an empty, ``0600``, previously unused file in *directory*.

        Unused means: not claimed by this run and not present on disk under any
        case. The exclusive create is what makes "never overwrite" hold against
        a file that appears after the listing.
        """
        directory.mkdir(parents=True, exist_ok=True)
        on_disk = {entry.name.casefold() for entry in directory.iterdir()}
        base = wanted.removesuffix(".webm")
        for attempt in range(1, _MAX_NAME_ATTEMPTS + 1):
            name = wanted if attempt == 1 else f"{base}_{attempt}.webm"
            key = (str(directory), name.casefold())
            if key in self._taken or name.casefold() in on_disk:
                continue
            target = reject_unsafe_path(directory / name, defaults.RECORDINGS_DIR, label="video path")
            try:
                os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            except FileExistsError:
                continue
            self._taken.add(key)
            return target
        raise FileExistsError(f"no free video name for {wanted!r} in {str(directory)!r}")


class RunVideos:
    """Every page video of one launch, in the order its pages opened.

    Built with :meth:`watch`. Pages are followed through the context's
    ``page`` event rather than read from ``session.pages`` at the end, because
    that list forgets a page the run closed and one that crash recovery
    replaced -- and each of those recorded a video of its own.
    """

    def __init__(self) -> None:
        self._videos: list[Any] = []

    @classmethod
    async def watch(cls, session: Any) -> RunVideos:
        videos = cls()
        async with session.operation("record_video_watch"):
            context = session.context
            for page in list(context.pages):
                videos._add(page)
            context.on("page", videos._add)
        return videos

    def _add(self, page: Any) -> None:
        video = getattr(page, "video", None)
        if video is not None and all(video is not seen for seen in self._videos):
            self._videos.append(video)

    async def finalise(self, *, artifacts: Path | None, stem: str, names: VideoNames | None = None) -> list[Path]:
        """Call only after the pool has closed the browser. Never raises.

        *names* is the run's `VideoNames`; a suite passes one shared by all its
        tests. A video that cannot be copied is reported where Playwright left
        it, so a failed copy still names a file the operator can open.
        """
        pages: list[tuple[int, Path]] = []
        for page, video in enumerate(self._videos, start=1):
            try:
                source = Path(await bounded(video.path(), operation="run_video_path"))
            except Exception as exc:
                log.warning("octowright.runner.video_unresolved", page=page, error=repr(exc))
                continue
            if await _has_frames(video, source, page=page):
                pages.append((page, source))
        if artifacts is None:
            return [source for _, source in pages]
        claimed = names if names is not None else VideoNames()
        return [await _copy_into(source, Path(artifacts), video_name(stem, page), claimed) for page, source in pages]


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


async def _has_frames(video: Any, source: Path, *, page: int) -> bool:
    """Whether *source* holds a video, after one ``save_as`` if it came back empty."""
    if _size(source) > 0:
        return True
    try:
        await bounded(video.save_as(str(source)), operation="run_video_save_as_retry")
    except Exception as exc:
        log.debug("octowright.runner.video_save_as_failed", page=page, error=repr(exc))
    size = _size(source)
    if size <= 0:
        log.warning("octowright.runner.video_empty", page=page, source=str(source))
    return size > 0


async def _copy_into(source: Path, directory: Path, wanted: str, names: VideoNames) -> Path:
    target: Path | None = None
    try:
        target = names.claim(directory, wanted)

        async def write(tmp: Path) -> None:
            await asyncio.to_thread(shutil.copyfile, source, tmp)

        await atomic_write_via_writer(target, write, root=defaults.RECORDINGS_DIR)
    except Exception as exc:
        log.warning("octowright.runner.video_copy_failed", source=str(source), error=repr(exc))
        if target is not None:
            # The empty placeholder the claim created; the name stays claimed.
            target.unlink(missing_ok=True)
        return source
    return target
