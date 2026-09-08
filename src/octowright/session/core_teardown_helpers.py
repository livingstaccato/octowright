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

from octowright.session.timeouts import bounded

log = get_logger(__name__)

# Used only when the context.close() this precedes already timed out -- the
# target is already confirmed unresponsive, so this best-effort fallback gets
# a short budget instead of a second full DEFAULT_UNBOUNDED_CALL_TIMEOUT_SECONDS
# wait on what is almost always the same wedged process.
_FALLBACK_CLOSE_FAST_TIMEOUT_SECONDS = 3.0


async def stop_trace_if_enabled(session: Any) -> None:
    if not session.trace:
        return
    session.trace_path = session.log_path.with_suffix(".trace.zip")
    try:
        await session.context.tracing.stop(path=str(session.trace_path))
    except Exception as e:
        session.recorder.record("trace_stop_error", error=repr(e))
        session.trace_path = None


def describe_context_videos(session: Any) -> list[dict[str, Any]]:
    """Every page in this context, snapshotted BEFORE the context is closed.

    The session tracks ONE video: `page.video` for the page that existed at
    launch. A journey that opens a second page records a second video that
    nothing here resolves, and the tracked one can be a page that never
    painted -- which reaches the caller as a zero-byte file rather than as a
    missing one, and those are indistinguishable downstream.

    ORDER IS THE WHOLE POINT. `context.pages` is empty once the context has
    closed, so a description taken after the close reports `[]` for every
    session and answers nothing -- measured, on the first version of this
    diagnostic. It has to be captured while the context is still live and
    carried to the point where the video size is known.
    """
    described: list[dict[str, Any]] = []
    for index, page in enumerate(getattr(session.context, "pages", []) or []):
        video = getattr(page, "video", None)
        entry: dict[str, Any] = {"index": index, "has_video": video is not None}
        try:
            entry["url"] = str(getattr(page, "url", ""))[:200]
        except Exception:  # diagnostics never raise
            entry["url"] = "<unreadable>"
        described.append(entry)
    return described


async def _save_video_again(session: Any, size: int) -> int:
    """Ask Playwright to finish writing a video that came back empty.

    `Video.path()` only reports where the file WILL be; its guarantee is tied
    to the context closing, and measured against a real tier that guarantee
    does not always hold -- `guest_buyer.guest_checkout` at 390x844 produced a
    zero-byte .webm in 5 of 9 runs while its desktop and tablet siblings, and
    every other mobile unit in the same sweep, recorded normally. Page count at
    teardown is 0 for the passes that work and the passes that do not, so this
    is inside Playwright's writer rather than anything the close path controls.

    `save_as()` is the call that WAITS: "waits until the page is closed and the
    video is fully saved". It runs only when the file is already empty, so the
    passes that record normally keep the cheaper path and are unaffected.

    A repair that cannot fail is worse than none, so this reports what it got:
    the size after the retry, still 0 when the video was genuinely never
    written. The caller keeps `video_path` either way -- an empty video is a
    real artifact of a real run, and the harness is entitled to judge it.
    """
    try:
        await session._video.save_as(str(session.video_path))
        size = session.video_path.stat().st_size
    except Exception as exc:
        log.debug(
            "octowright.session.video_save_as_failed",
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )
    log.warning(
        "octowright.session.video_was_empty",
        instance_id=getattr(session, "instance_id", None),
        video_path=str(session.video_path),
        size_after_save_as=size,
        recovered=size > 0,
    )
    return size


async def resolve_video_path_after_close(session: Any) -> None:
    # Resolve video path after context close (Playwright finalises file on close).
    if session._video is None:
        return
    try:
        resolved = await session._video.path()
        session.video_path = Path(resolved)
        # An EMPTY video is not a missing one, and the difference is the whole
        # diagnosis: `path()` answers for the page this session tracked, so a
        # zero-byte file means that page produced no frames -- typically
        # because the journey ran on a page opened later. Recorded here, at
        # the only point where the context is still readable.
        try:
            size = session.video_path.stat().st_size
        except OSError:
            size = -1
        if size <= 0:
            size = await _save_video_again(session, size)
    except Exception as exc:
        # Per silent-swallow policy: video_path stays None and the dashboard
        # can't surface the video. Log so the failure is diagnosable rather
        # than just missing from the UI.
        log.debug(
            "octowright.session.video_path_resolve_failed",
            instance_id=getattr(session, "instance_id", None),
            error=repr(exc),
        )


async def close_browser_handle_after_context_close(session: Any, *, fast_timeout: bool = False) -> None:
    close_handle = getattr(session, "_browser_for_close", None) or session.browser
    if close_handle is None:
        return
    # context.close() may have already terminated the underlying browser
    # process (persistent contexts in particular). A second .close() then
    # raises and bypasses the recorder terminal-event write below — log and
    # continue. Also bounded: an unresponsive browser process can leave this
    # awaiting a CDP reply that never comes (observed on Windows as a
    # multi-hour wedge with near-zero CPU on both the driver and the browser).
    # ``fast_timeout`` -- set when context.close() itself just timed out on
    # this same session -- shortens the budget instead of stacking a second
    # full wait on what is almost always the same wedged target.
    timeout = _FALLBACK_CLOSE_FAST_TIMEOUT_SECONDS if fast_timeout else None
    try:
        await bounded(close_handle.close(), operation="browser_close_handle", timeout=timeout)
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
