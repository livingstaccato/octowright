# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from octowright.session.core import BrowserSession

# Restrict an on-disk download name to a single safe basename component.
_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _safe_download_name(suggested: str | None) -> str:
    """Reduce a remote-controlled ``suggested_filename`` to one safe basename.

    The value comes from the visited page's Content-Disposition, so it is fully
    attacker-controlled. ``Path(...).name`` drops any directory components
    (including ``../``), then the charset filter strips anything that could
    re-introduce a separator or NUL. Falls back to ``download`` when nothing
    usable remains (e.g. a bare ``..``). This is what keeps Playwright's
    ``save_as`` — which ``os.makedirs`` the target's parent and would otherwise
    materialise a ``NNN-..`` traversal — from writing outside the session dir.
    """
    base = Path(suggested or "").name
    safe = _UNSAFE_NAME_RE.sub("-", base).strip("-.")
    return safe or "download"


#: Upper bound on the index search for a free destination name. Each step is
#: one ``O_EXCL`` create in the session's own downloads directory.
_MAX_NAME_ATTEMPTS = 10_000


def _reserve_target(target_dir: Path, recordings_root: Path, first_index: int, name: str) -> Path:
    """Claim a destination that no earlier or concurrent download holds.

    The index used to be ``len(session.downloads)``, which is not unique: a
    keep-id driver relaunch starts ``downloads`` empty under the same
    ``downloads/<instance_id>/`` and two saves in flight read the same length.
    Each candidate is created exclusively (``O_EXCL``, never following a
    symlink), so the first free ``NNN-<name>`` wins and a held one is skipped.
    """
    from octowright._paths import reject_unsafe_path

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    for index in range(first_index, first_index + _MAX_NAME_ATTEMPTS):
        target = target_dir / f"{index:03d}-{name}"
        # Belt-and-suspenders: the sanitized basename has no separators, but run
        # the containment helper so the write provably stays under the root.
        reject_unsafe_path(target, recordings_root, label="download path")
        try:
            os.close(os.open(target, flags, 0o600))
        except FileExistsError:
            continue
        return target
    raise FileExistsError(f"no free download name for {name!r} in {str(target_dir)!r}")


def _release_reservation(target: Path) -> None:
    """Drop an unfilled reservation after a failed transfer (best effort)."""
    try:
        if target.stat().st_size == 0:
            target.unlink()
    except OSError:
        pass


async def save_download(session: BrowserSession, download: Any) -> dict[str, Any]:
    """Save a Playwright Download to disk under <recordings_root>/downloads/<instance_id>/.
    Appends the record to session.downloads and signals any pending waiters.
    Records download_save_error on failure -- including a refusal from the
    session's operation gate.

    The session lease is held only to reserve the destination and to publish
    the record, not across ``save_as``: a large transfer would otherwise block
    every tool call on the session until the gate timed out. The reservation
    is what keeps concurrent saves apart once the lease is released.

    The recordings root is the parent of ``session.log_path`` — i.e. the root
    the owning pool was configured with (new_log_path writes the JSONL directly
    under it), so a pool given a custom recordings_dir keeps its downloads
    beside its recordings instead of leaking into the process-global root."""
    recordings_root = session.log_path.parent
    target_dir = recordings_root / "downloads" / session.instance_id
    suggested = download.suggested_filename
    target: Path | None = None
    failure: Exception | None = None
    try:
        async with session.operation("download_save"):
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _reserve_target(
                target_dir, recordings_root, len(session.downloads), _safe_download_name(suggested)
            )
        await download.save_as(str(target))
        async with session.operation("download_save"):
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
        failure = e
    if target is not None:
        _release_reservation(target)
    # Outside the lease: waiting for the close verdict must not hold the gate.
    await _record_save_error(session, download, failure)
    return {}


# How long a save that died with its browser waits for the close signal to judge
# the exit. The rejection lands a few ms BEFORE that signal (2 ms in the field);
# teardown drains background tasks for 1 s before cancelling them.
EXIT_VERDICT_WAIT_SECONDS = 2.0

_CAUSES = {"crashed": "browser_crashed", "closed": "browser_closed"}


async def _record_save_error(session: BrowserSession, download: Any, failure: Exception | None) -> None:
    """Record why a download was lost, naming a browser crash when that is why.

    Only a ``TargetClosedError`` waits, and it waits for the verdict the
    evicting close listener computes (``process_crash``) rather than sampling
    the process itself: on a Firefox user close this rejection arrives after
    the browser has already exited cleanly, so a sample here would call it a
    crash. Recorded in ``finally`` so a teardown cancelling the wait cannot
    drop the row.
    """
    from octowright.browser_pool import process_crash

    fields: dict[str, Any] = {"error": repr(failure), "url": download.url}
    try:
        if type(failure).__name__ == "TargetClosedError":
            verdict = await process_crash.wait_for_exit_verdict(session, timeout=EXIT_VERDICT_WAIT_SECONDS)
            if verdict is not None:
                fields["cause"] = _CAUSES[verdict]
            incident = getattr(session, "_process_crash_incident", None)
            if verdict == "crashed" and incident is not None:
                incident["lost_downloads"] = int(incident.get("lost_downloads", 0)) + 1
    finally:
        session.recorder.record("download_save_error", **fields)


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
