# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Open a saved Playwright trace .zip in the trace viewer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from octowright import defaults
from octowright.server._state import log, mcp, pool


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

    # Normalize the LLM-supplied path and confine subprocess invocation to the
    # recordings root. `.resolve()` collapses symlinks so a symlink under
    # RECORDINGS_DIR pointing elsewhere (e.g. /etc/passwd) is rejected here
    # before we spawn `npx playwright show-trace` on it.
    try:
        target = target.expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"trace path {str(target)!r} could not be resolved: {exc}") from exc
    recordings_root = defaults.RECORDINGS_DIR.expanduser().resolve()
    try:
        target.relative_to(recordings_root)
    except ValueError as exc:
        raise ValueError(
            f"trace path {str(target)!r} is outside the recordings root {str(recordings_root)!r}; "
            f"only traces under OCTOWRIGHT_RECORDINGS may be opened"
        ) from exc

    if not target.exists():
        raise FileNotFoundError(f"no trace file at {target}; close the instance first to flush, or check the path")

    if shutil.which("npx") is None:
        raise RuntimeError(
            "npx not found on PATH; install Node.js + run `npm i -g playwright` "
            "(or invoke the viewer manually: `python -m playwright show-trace <path>`)"
        )

    # Fixed argv; trace path is recordings-root confined above.
    proc = subprocess.Popen(  # nosec B603 B607
        ["npx", "playwright", "show-trace", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    log.info("octowright.trace.opened", path=str(target), pid=proc.pid)
    return {"path": str(target), "pid": proc.pid}
