# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One-call artifact manifest for a browser session, live or closed."""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp, pool


@mcp.tool(
    structured_output=False,
    description=(
        "Every known artifact path for one session in a single call: log_path (JSONL "
        "recording), video_path, trace_path, har_path -- each null if that artifact "
        "wasn't produced (e.g. launched without record_video/trace/har). Works for a "
        "still-live session AND a closed one (resolved from disk by instance_id), so "
        "you don't need to have captured browser_close's result to find a session's "
        "artifacts later. Use octowright_dashboard_url for a human-browsable view "
        "instead of calling this per-session."
    ),
)
def browser_artifact_manifest(instance_id: str) -> dict[str, Any]:
    from octowright.http.discovery import resolve_session_artifacts

    manifest = resolve_session_artifacts(instance_id)
    if manifest["log_path"] is None:
        return {
            "instance_id": instance_id,
            "error": f"no live session or recording found for instance_id {instance_id!r}",
        }
    return {
        "instance_id": instance_id,
        "live": pool.maybe_get(instance_id) is not None,
        **manifest,
    }
