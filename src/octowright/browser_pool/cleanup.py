# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path
from typing import Any

from octowright.recorder import Recorder


async def safe_close(target: Any | None) -> None:
    """Best-effort close on any object exposing async close(). No-op on None."""
    if target is None:
        return
    try:
        await target.close()
    except Exception:
        pass


def remove_empty_video_dir(video_dir: Path | None) -> None:
    """Drop an empty per-launch video_dir; keep partial recordings for debugging."""
    if video_dir is None:
        return
    try:
        video_dir.rmdir()
    except OSError:
        pass


async def cleanup_on_launch_failure(
    *,
    context: Any | None,
    browser: Any | None,
    video_dir: Path | None,
) -> None:
    """Best-effort teardown shared by launch failure paths."""
    await safe_close(context)
    await safe_close(browser)
    persistent_browser = getattr(context, "browser", None) if browser is None else None
    await safe_close(persistent_browser)
    remove_empty_video_dir(video_dir)


async def cleanup_unregistered_launch(
    *,
    context: Any | None,
    browser: Any | None,
    video_dir: Path | None,
    recorder: Recorder | None,
) -> None:
    await cleanup_on_launch_failure(context=context, browser=browser, video_dir=video_dir)
    if recorder is not None:
        try:
            recorder.close()
        except Exception:
            pass
