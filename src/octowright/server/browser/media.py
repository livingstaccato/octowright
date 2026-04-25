# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Video, trace, and frame-extraction tools."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from ... import video as video_mod
from .._state import log, mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Return the path to the video file recorded for an instance. "
        "Only populated after the instance is closed (Playwright finalises the file on close)."
    ),
)
def browser_video_path(instance_id: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    return {"video_path": str(session.video_path) if session.video_path else None}


@mcp.tool(
    structured_output=False,
    description=(
        "Open a saved Playwright trace .zip in the trace viewer. If `path` is omitted, "
        "uses the trace_path of the given instance_id. Spawns `npx playwright show-trace` "
        "as a detached background process and returns immediately with `{path, pid}`. "
        "Requires `npx` on PATH (Node.js + Playwright installed)."
    ),
)
def browser_open_trace(
    instance_id: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    if path:
        target = Path(path)
    elif instance_id:
        session = pool.get(instance_id)
        if session.trace_path is None:
            raise RuntimeError(
                f"instance {instance_id} was not launched with trace=True; "
                f"call browser_launch with trace=True next time, or pass path=<saved.zip> here"
            )
        target = session.trace_path
    else:
        raise ValueError("supply either instance_id (with trace=True at launch) or an explicit path")

    if not target.exists():
        raise FileNotFoundError(f"no trace file at {target}; close the instance first to flush, or check the path")

    if shutil.which("npx") is None:
        raise RuntimeError(
            "npx not found on PATH; install Node.js + run `npm i -g playwright` "
            "(or invoke the viewer manually: `python -m playwright show-trace <path>`)"
        )

    proc = subprocess.Popen(
        ["npx", "playwright", "show-trace", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("octowright.trace.opened", path=str(target), pid=proc.pid)
    return {"path": str(target), "pid": proc.pid}


@mcp.tool(
    structured_output=False,
    description=(
        "Extract frames from a recorded video via ffmpeg. Supply exactly one of fps (frames/second) "
        "or at_times (list of second-timestamps). Frames land in out_dir (default: next to the video)."
    ),
)
def browser_extract_frames(
    video_path: str,
    out_dir: str | None = None,
    fps: float | None = None,
    at_times: list[float] | None = None,
) -> dict[str, Any]:
    vp = Path(video_path)
    odir = Path(out_dir) if out_dir else vp.with_suffix(".frames")
    frames = video_mod.extract_frames(vp, odir, fps=fps, at_times=at_times)
    return {"video": str(vp), "out_dir": str(odir), "frames": [str(f) for f in frames]}
