# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Helpers extracted from pool.py launch() to keep both LOC and cyclomatic
complexity below the project gates. Each helper handles one cohesive slice
of launch wiring: kwargs assembly, context open, recorder event, manifest
write."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.defaults import DEFAULT_VIEWPORT_H, DEFAULT_VIEWPORT_W, RECORDINGS_DIR
from octowright.profiles import profile_dir
from octowright.recorder import Recorder
from octowright.session_manifest import record_launch as _manifest_record_launch

log = get_logger(__name__)


def _build_viewport_kwargs(
    headless: bool, viewport_w: int | None, viewport_h: int | None
) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    """Headed launches with no explicit size let Playwright adopt the OS
    window via no_viewport=True. Headless and explicit-size launches pin a
    fixed viewport. Returns (kwargs, recorder_payload, explicit_size_flag)."""
    explicit_size = viewport_w is not None or viewport_h is not None
    if headless or explicit_size:
        vw = viewport_w or DEFAULT_VIEWPORT_W
        vh = viewport_h or DEFAULT_VIEWPORT_H
        return {"viewport": {"width": vw, "height": vh}}, {"w": vw, "h": vh}, explicit_size
    return {"no_viewport": True}, None, explicit_size


def _build_video_kwargs(
    record_video: bool,
    headless: bool,
    explicit_size: bool,
    viewport_w: int | None,
    viewport_h: int | None,
) -> tuple[dict[str, Any], Path | None]:
    """Allocate a per-launch videos/ dir and assemble the record_video_*
    context kwargs. Pins video size to the viewport so Playwright doesn't
    auto-scale to its 800x800 default."""
    if not record_video:
        return {}, None
    video_dir = RECORDINGS_DIR / "videos" / uuid.uuid4().hex[:8]
    video_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"record_video_dir": str(video_dir)}
    if headless or explicit_size:
        out["record_video_size"] = {
            "width": viewport_w or DEFAULT_VIEWPORT_W,
            "height": viewport_h or DEFAULT_VIEWPORT_H,
        }
    return out, video_dir


def _build_har_kwargs(
    *,
    har: bool,
    har_path_opt: str | None,
    har_mode: str,
    har_url_filter: str | None,
    har_content: str | None,
    log_path: Path,
) -> tuple[Path | None, dict[str, Any]]:
    """Resolve the HAR output path (relative paths land under RECORDINGS_DIR)
    and assemble the record_har_* context kwargs."""
    if not (har or har_path_opt):
        return None, {}
    har_path = Path(har_path_opt) if har_path_opt else log_path.with_suffix(".har")
    if not har_path.is_absolute():
        har_path = (RECORDINGS_DIR / har_path).resolve()
    har_path.parent.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {
        "record_har_path": str(har_path),
        "record_har_mode": har_mode,
    }
    if har_url_filter:
        out["record_har_url_filter"] = har_url_filter
    if har_content:
        out["record_har_content"] = har_content
    return har_path, out


async def _open_browser_context(
    *,
    browser_type: Any,
    kind: str,
    profile: str | None,
    session_user_data_dir: str | None,
    headless: bool,
    viewport_kwargs: dict[str, Any],
    ctx_video_kwargs: dict[str, Any],
    ctx_har_kwargs: dict[str, Any],
    launch_kwargs: dict[str, Any],
) -> tuple[Any, Any, Any, str | None]:
    """Open a Playwright BrowserContext + Page. Persistent profile and
    session-tmpdir paths both go through launch_persistent_context (no
    standalone Browser); the ephemeral path goes through Browser.new_context.
    Cleanup-on-error is handled by the caller's outer except block.

    Returns (browser, context, page, user_data_dir). browser is None for the
    persistent path."""
    if profile or session_user_data_dir:
        if profile:
            pdir = profile_dir(kind, profile)
            pdir.mkdir(parents=True, exist_ok=True)
            user_data_dir: str | None = str(pdir)
        else:
            user_data_dir = session_user_data_dir
        context = await browser_type.launch_persistent_context(
            user_data_dir,
            headless=headless,
            accept_downloads=True,
            **viewport_kwargs,
            **ctx_video_kwargs,
            **ctx_har_kwargs,
            **launch_kwargs,
        )
        browser = None
        page = context.pages[0] if context.pages else await context.new_page()
    else:
        browser = await browser_type.launch(headless=headless, **launch_kwargs)
        context = await browser.new_context(
            accept_downloads=True,
            **viewport_kwargs,
            **ctx_video_kwargs,
            **ctx_har_kwargs,
        )
        page = await context.new_page()
        user_data_dir = None
    return browser, context, page, user_data_dir


def _record_launch_event(
    recorder: Recorder,
    *,
    instance_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    user_data_dir: str | None,
    target_url: str,
    headless: bool,
    log_viewport: dict[str, Any] | None,
    stabilize: bool,
    record_video: bool,
    video_dir: Path | None,
    trace: bool,
    har_path: Path | None,
    har_mode: str,
    har_url_filter: str | None,
    har_content: str | None,
) -> None:
    """Emit the JSONL `launch` event with all the conditional fields. Pulled
    out of launch() to keep its complexity rank below the gate."""
    recorder.record(
        "launch",
        instance_id=instance_id,
        kind=kind,
        label=label,
        profile=profile,
        user_data_dir=user_data_dir,
        url=target_url,
        headed=not headless,
        viewport=log_viewport,
        stabilize=stabilize,
        record_video=record_video,
        video_dir=str(video_dir) if video_dir else None,
        trace=trace,
        har=bool(har_path),
        har_path=str(har_path) if har_path else None,
        har_mode=har_mode if har_path else None,
        har_url_filter=har_url_filter if har_path else None,
        har_content=har_content if har_path else None,
    )


def _safe_manifest_record(
    *,
    instance_id: str,
    kind: str,
    label: str | None,
    profile: str | None,
    user_data_dir: str | None,
    log_path: Path,
) -> None:
    """Best-effort manifest write. The manifest is purely an out-of-band
    convenience for the dashboard; a write failure must not block the launch."""
    try:
        _manifest_record_launch(
            session_id=instance_id,
            kind=kind,
            label=label,
            profile=profile,
            user_data_dir=user_data_dir,
            log_path=log_path,
        )
    except Exception as exc:
        log.warning("octowright.session_manifest.write_failed", instance_id=instance_id, error=repr(exc))
