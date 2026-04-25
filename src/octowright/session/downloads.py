# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import BrowserSession


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def save_download(session: BrowserSession, download: Any) -> dict[str, Any]:
    """Save a Playwright Download to disk under RECORDINGS_DIR/downloads/<instance_id>/.
    Appends the record to session.downloads and signals any pending waiters.
    Records download_save_error on failure."""
    from ..defaults import RECORDINGS_DIR

    target_dir = RECORDINGS_DIR / "downloads" / session.instance_id
    target_dir.mkdir(parents=True, exist_ok=True)
    suggested = download.suggested_filename
    target = target_dir / f"{len(session.downloads):03d}-{suggested}"
    try:
        await download.save_as(str(target))
        record = {
            "url": download.url,
            "suggested_filename": suggested,
            "path": str(target),
            "timestamp": _timestamp(),
        }
        session.downloads.append(record)
        session.recorder.record("download_saved", **record)
        for event in session._pending_download_events:
            event.set()
        session._pending_download_events.clear()
        return record
    except Exception as e:
        session.recorder.record("download_save_error", error=repr(e), url=download.url)
    return {}


async def wait_for_download_impl(session: BrowserSession, timeout_ms: int) -> dict[str, Any]:
    """Block until the next download completes. Raise TimeoutError on timeout.
    Returns immediately if a download has already been recorded."""
    if session.downloads:
        return session.downloads[-1]
    event = asyncio.Event()
    session._pending_download_events.append(event)
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_ms / 1000)
    except TimeoutError as e:
        try:
            session._pending_download_events.remove(event)
        except ValueError:
            pass
        raise TimeoutError(f"no download within {timeout_ms}ms") from e
    return session.downloads[-1]
