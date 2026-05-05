# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from ..session_manifest import remove_session as remove_manifest_session

if TYPE_CHECKING:
    from .pool import BrowserPool

log = get_logger(__name__)


async def close_browser(pool: BrowserPool, instance_id: str) -> dict[str, Any]:
    # Remove from the registry before awaiting session.close(); that call fires
    # close events wired by listeners, which should then no-op.
    async with pool._sessions_lock:
        session = pool._sessions.pop(instance_id, None)
    if session is None:
        raise KeyError(pool._missing_session_message(instance_id))
    await session.close()
    try:
        remove_manifest_session(instance_id)
    except Exception as exc:
        log.warning("octowright.session_manifest.remove_failed", instance_id=instance_id, error=repr(exc))
    log.info(
        "octowright.browser.closed",
        instance_id=instance_id,
        kind=session.kind,
        profile=session.profile,
        log_path=str(session.log_path),
    )
    return {
        "closed": True,
        "log_path": str(session.log_path),
        "video_path": str(session.video_path) if session.video_path else None,
        "trace_path": str(session.trace_path) if session.trace_path else None,
        "har_path": str(session.har_path) if session.har_path else None,
    }


async def handoff_browser(
    pool: BrowserPool,
    old_instance_id: str,
    *,
    headed: bool | None = None,
    close_original: bool = True,
    accept_stateless: bool = False,
) -> dict[str, Any]:
    source = pool.get(old_instance_id)
    source_profile = source.profile
    source_user_data_dir = getattr(source, "user_data_dir", None)
    if source_profile is None and source_user_data_dir is None and not accept_stateless:
        raise ValueError(
            "handoff would be stateless: source has no profile/user_data_dir; pass accept_stateless=True to proceed"
        )
    if not close_original and (source_profile is not None or source_user_data_dir is not None):
        raise ValueError("persistent handoff requires close_original=True so the state directory can be safely reused")

    target_url = getattr(source.page, "url", None) or source.url
    session_scoped = source_profile is None and source_user_data_dir is not None
    close_result: dict[str, Any] | None = None
    if close_original:
        close_result = await pool.close(old_instance_id)

    launch = await pool.launch(
        kind=source.kind,
        url=target_url,
        headed=headed,
        label=source.label,
        profile=source_profile,
        stabilize=getattr(source, "stabilize", False),
        trace=getattr(source, "trace", False),
        har=bool(getattr(source, "har_path", None)),
        har_path=str(source.har_path) if getattr(source, "har_path", None) else None,
        session=session_scoped,
    )

    return {
        "ok": True,
        "old_instance_id": old_instance_id,
        "new_instance_id": launch["instance_id"],
        "old_closed": bool(close_result and close_result.get("closed")),
        "profile": source_profile,
        "kind": source.kind,
        "url": target_url,
        "har_path": launch.get("har_path"),
    }


async def shutdown_pool(pool: BrowserPool) -> None:
    await pool.close_all()
    if pool._pw is not None:
        await pool._pw.stop()
        pool._pw = None
    for tmpdir in pool._session_profile_dirs.values():
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except OSError:
            pass
    pool._session_profile_dirs.clear()
