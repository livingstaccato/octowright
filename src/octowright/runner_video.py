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
``<stem>.webm`` and every later page's is ``<stem>-<n>.webm``, numbered from 2
in the order the pages opened. The stem is the sequence file's stem, or the
macro name for a ``[test]`` suite. Without an artifacts directory the video
stays where Playwright wrote it, in the launch's own videos directory under
the recordings root.
"""

from __future__ import annotations

import asyncio
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


def launch_kwargs(videos: list[Path] | None) -> dict[str, Any]:
    """``record_video=True`` when videos were asked for, and nothing otherwise,
    so a run without ``--record-video`` launches exactly as it always did."""
    return {"record_video": True} if videos is not None else {}


def artifacts_root(artifacts: Path | None) -> Path | None:
    """The artifacts directory, refused unless it sits under the recordings root."""
    if artifacts is None:
        return None
    return reject_unsafe_path(Path(artifacts), defaults.RECORDINGS_DIR, label="artifacts directory")


def video_names(stem: str, count: int) -> list[str]:
    safe = _UNSAFE_STEM.sub("_", stem).strip("._") or "run"
    return [f"{safe}.webm" if n == 1 else f"{safe}-{n}.webm" for n in range(1, count + 1)]


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

    async def finalise(self, *, artifacts: Path | None, stem: str) -> list[Path]:
        """Call only after the pool has closed the browser. Never raises.

        A video that cannot be copied is reported where Playwright left it, so
        a failed copy still names a file the operator can open.
        """
        sources: list[Path] = []
        for video in self._videos:
            try:
                sources.append(Path(await bounded(video.path(), operation="run_video_path")))
            except Exception as exc:
                log.warning("octowright.runner.video_unresolved", error=repr(exc))
        sources = [source for source in sources if source.is_file()]
        if artifacts is None:
            return sources
        return [
            await _copy_into(source, Path(artifacts) / name)
            for source, name in zip(sources, video_names(stem, len(sources)), strict=True)
        ]


async def _copy_into(source: Path, target: Path) -> Path:
    try:
        target = reject_unsafe_path(target, defaults.RECORDINGS_DIR, label="video path")
        target.parent.mkdir(parents=True, exist_ok=True)

        async def write(tmp: Path) -> None:
            await asyncio.to_thread(shutil.copyfile, source, tmp)

        await atomic_write_via_writer(target, write)
    except Exception as exc:
        log.warning("octowright.runner.video_copy_failed", source=str(source), error=repr(exc))
        return source
    return target
