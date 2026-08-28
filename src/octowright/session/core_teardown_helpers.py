# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Teardown-body helpers for ``SessionOpsMixin._teardown_after_close_cutoff``.

Split out of ``core_ops_mixin.py`` (kept as free functions taking the session
as a duck-typed ``Any`` first argument, called from the mixin method) purely
to keep that file under the repository's LOC ceiling and its teardown method
under the xenon complexity bar -- no behavior change from when these were
private methods on the mixin itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

log = get_logger(__name__)


async def stop_trace_if_enabled(session: Any) -> None:
    if not session.trace:
        return
    session.trace_path = session.log_path.with_suffix(".trace.zip")
    try:
        await session.context.tracing.stop(path=str(session.trace_path))
    except Exception as e:
        session.recorder.record("trace_stop_error", error=repr(e))
        session.trace_path = None


async def resolve_video_path_after_close(session: Any) -> None:
    # Resolve video path after context close (Playwright finalises file on close).
    if session._video is None:
        return
    try:
        resolved = await session._video.path()
        session.video_path = Path(resolved)
    except Exception as exc:
        # Per silent-swallow policy: video_path stays None and the dashboard
        # can't surface the video. Log so the failure is diagnosable rather
        # than just missing from the UI.
        log.debug(
            "octowright.session.video_path_resolve_failed",
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )


async def close_browser_handle_after_context_close(session: Any) -> None:
    close_handle = getattr(session, "_browser_for_close", None) or session.browser
    if close_handle is None:
        return
    # context.close() may have already terminated the underlying browser
    # process (persistent contexts in particular). A second .close() then
    # raises and bypasses the recorder terminal-event write below — log and
    # continue.
    try:
        await close_handle.close()
    except Exception as exc:
        log.debug(
            "octowright.session.browser_close_after_context_close_failed",
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )


def flush_and_close_websocket_fh(session: Any) -> None:
    ws_fh = getattr(session, "_websocket_fh", None)
    if ws_fh is None:
        return
    try:
        # Flush any buffered frames before the close so a final batch isn't
        # lost behind the block-buffering window.
        ws_fh.flush()
    except Exception as exc:
        log.debug(
            "octowright.session.websocket_fh_flush_failed",
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
    try:
        ws_fh.close()
    except Exception as exc:
        log.debug(
            "octowright.session.websocket_fh_close_failed",
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
    session._websocket_fh = None


def close_recorder_fields(session: Any, reason: str | None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "video_path": str(session.video_path) if session.video_path else None,
        "trace_path": str(session.trace_path) if session.trace_path else None,
        "har_path": str(session.har_path) if session.har_path else None,
        "markdown_path": str(session.markdown_path) if session.markdown_path else None,
        "websocket_path": str(session.websocket_path) if session.websocket_path else None,
    }
    if reason is not None:
        # An explicit or shutdown close records no reason at all; an
        # external-close coordinator (crashed / user_close
        # / external_disconnect) records why the browser went away, mapped
        # down to "crashed" / "external" by the pool coordinator before it
        # ever reaches here.
        fields["reason"] = reason
    return fields
