# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from octowright.session.core import BrowserSession


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


async def save_download(session: BrowserSession, download: Any) -> dict[str, Any]:
    """Save a Playwright Download to disk under RECORDINGS_DIR/downloads/<instance_id>/.
    Appends the record to session.downloads and signals any pending waiters.
    Records download_save_error on failure."""
    from octowright.defaults import RECORDINGS_DIR

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
        session.download_count += 1
        session.recorder.record("download_saved", **record)
        for event in session._pending_download_events:
            event.set()
        session._pending_download_events.clear()
        return record
    except Exception as e:
        session.recorder.record("download_save_error", error=repr(e), url=download.url)
    return {}


async def wait_for_download_impl(session: BrowserSession, timeout_ms: int) -> dict[str, Any]:
    """Block until the NEXT download completes (relative to call time). Raise
    TimeoutError on timeout. Prior downloads do not satisfy the wait — callers
    expect this to fire on a fresh event so they can pair it with an action
    that triggers the download."""
    start_len = len(session.downloads)
    event = asyncio.Event()
    session._pending_download_events.append(event)
    try:
        while len(session.downloads) <= start_len:
            try:
                await asyncio.wait_for(event.wait(), timeout=timeout_ms / 1000)
            except TimeoutError as e:
                try:
                    session._pending_download_events.remove(event)
                except ValueError:
                    pass
                raise TimeoutError(f"no download within {timeout_ms}ms") from e
            # save_download clears the pending list and signals every event;
            # if our wait was woken by a non-download caller, reset and loop.
            if len(session.downloads) <= start_len:
                event.clear()
                if event not in session._pending_download_events:
                    session._pending_download_events.append(event)
    finally:
        try:
            session._pending_download_events.remove(event)
        except ValueError:
            pass
    return session.downloads[-1]
