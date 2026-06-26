# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Handle the browser sessions lost when the shared Playwright driver dies (H4a).

``pool._reset_driver`` rebuilds the single shared driver after it dies (P3), but
every ``BrowserSession`` that rode the old driver is now dead — its pipe is gone.
This module is the policy for those lost sessions:

- **Always**: capture each one (instance_id, kind, url, profile), record a
  ``driver_lost`` incident, evict the dead handle from the pool, and keep a
  bounded list that ``octowright_status`` surfaces as ``pool.lost_sessions`` —
  so "my browsers vanished" is answerable instead of silent.
- **Optionally** (``OCTOWRIGHT_DRIVER_RELAUNCH`` = ``new-id`` / ``keep-id``):
  reopen each one to its last URL/profile on a fresh driver. ``new-id`` reopens
  with a fresh instance_id (clients rebind); ``keep-id`` rebinds the original id
  so existing handles keep resolving. OFF by default — reopening changes
  instance_ids and silently re-runs navigation across every connected client, a
  deliberate opt-in.

Loop guard: a session this module reopened is tagged ``_auto_relaunched`` and is
NOT recaptured if it dies again, so a driver that keeps dying can't spawn an
unbounded relaunch storm.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from typing import Any

from provide.telemetry import get_logger

from octowright._tracing import counter
from octowright.browser_pool import incidents

log = get_logger(__name__)

# Stability metrics (noop unless telemetry is enabled). A climbing driver-restart
# rate means the shared driver (SPOF) is unstable; driver_lost{outcome} shows how
# many sessions died with it and whether auto-relaunch reopened them.
_DRIVER_RESTART = counter(
    "octowright_driver_restart_total",
    description="Shared Playwright driver deaths that were rebuilt mid-run",
)
_DRIVER_LOST = counter(
    "octowright_driver_lost_total",
    description="Sessions lost when the shared driver died (outcome=surfaced|relaunched)",
)

# Bounded recent-lost-session ring surfaced in status. Sized like the incident
# ring; a long-lived daemon can't grow it without bound.
_LOST_SIZE = int(os.environ.get("OCTOWRIGHT_LOST_SESSION_RING_SIZE", "25"))
_LOST: deque[dict[str, Any]] = deque(maxlen=_LOST_SIZE)


def parse_mode(raw: str | None) -> str:
    """Parse OCTOWRIGHT_DRIVER_RELAUNCH → ``off`` (default) / ``new-id`` / ``keep-id``."""
    text = (raw or "").strip().lower().replace("_", "-")
    if text in ("new-id", "newid"):
        return "new-id"
    if text in ("keep-id", "keepid"):
        return "keep-id"
    return "off"


# Whether (and how) to auto-reopen sessions lost to driver death. OFF by default;
# reopening changes instance_ids and silently re-runs navigation across every
# connected client, so it's a deliberate opt-in. Lost sessions are ALWAYS
# captured + surfaced (status.pool.lost_sessions) regardless of this mode. Read
# here (not defaults.py, which is at its LOC ceiling), mirroring incidents/health.
DRIVER_RELAUNCH_MODE = parse_mode(os.environ.get("OCTOWRIGHT_DRIVER_RELAUNCH"))

# Live relaunch tasks, kept referenced so they aren't GC'd mid-flight (RUF006).
_TASKS: set[asyncio.Task[None]] = set()


def recent_lost(*, limit: int | None = None) -> list[dict[str, Any]]:
    """Recent lost-session records oldest→newest, optionally capped to ``limit``."""
    items = list(_LOST)
    return items[-limit:] if limit is not None else items


def reset() -> None:
    """Clear the lost-session ring (tests / operator process access)."""
    _LOST.clear()


def _mode() -> str:
    # Module-global read so a test/operator monkeypatch of DRIVER_RELAUNCH_MODE
    # takes effect without reloading the module.
    return DRIVER_RELAUNCH_MODE


def _descriptor(session: Any) -> dict[str, Any]:
    udd = session.user_data_dir
    return {
        "instance_id": session.instance_id,
        "kind": session.kind,
        "label": session.label,
        "profile": session.profile,
        "url": session.url,
        "user_data_dir": str(udd) if udd else None,
    }


def _snapshot_and_evict(pool: Any, reason: str | None) -> list[dict[str, Any]]:
    """Capture + record + evict the sessions lost with the dead driver. Sessions
    this module previously relaunched are skipped (loop guard)."""
    descriptors: list[dict[str, Any]] = []
    for session in pool.iter_sessions():
        if getattr(session, "_auto_relaunched", False):
            continue
        desc = _descriptor(session)
        inc = incidents.record(
            incidents.CATEGORY_DRIVER_LOST,
            instance_id=desc["instance_id"],
            kind=desc["kind"],
            url=desc["url"],
            reason=reason,
            outcome="lost",
        )
        record = {"ts": inc["ts"], "reason": reason, **desc, "relaunched_to": None}
        _LOST.append(record)
        _DRIVER_LOST.add(1, attributes={"outcome": "surfaced", "kind": desc["kind"]})
        descriptors.append({**desc, "lost_record": record})
    for desc in descriptors:
        pool._evict_session_nowait(desc["instance_id"])
    return descriptors


def on_driver_reset(pool: Any, *, reason: str | None) -> asyncio.Task[None] | None:
    """Record the driver restart, capture/evict the lost sessions, and (when
    configured) schedule their relaunch. Returns the relaunch task, or ``None``
    when relaunch is off / there's nothing to do / there's no running loop.

    Capture is synchronous so the lost sessions are surfaced + evicted even when
    there is no event loop to schedule a relaunch on."""
    incidents.record(
        incidents.CATEGORY_DRIVER_RESTART,
        restart_count=pool._driver_restarts,
        reason=reason,
    )
    _DRIVER_RESTART.add(1)
    descriptors = _snapshot_and_evict(pool, reason)
    mode = _mode()
    if not descriptors or mode == "off":
        return None
    return _schedule_relaunch(pool, descriptors, mode)


def _schedule_relaunch(pool: Any, descriptors: list[dict[str, Any]], mode: str) -> asyncio.Task[None] | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_relaunch_all(pool, descriptors, mode))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return task


async def _relaunch_all(pool: Any, descriptors: list[dict[str, Any]], mode: str) -> None:
    for desc in descriptors:
        try:
            await _relaunch_one(pool, desc, mode)
        except Exception as exc:
            log.warning(
                "octowright.driver_relaunch.failed",
                instance_id=desc["instance_id"],
                error=repr(exc),
            )


async def _relaunch_one(pool: Any, desc: dict[str, Any], mode: str) -> None:
    profile = desc["profile"]
    udd = desc["user_data_dir"]
    session_scoped = profile is None and udd is not None
    stateless = profile is None and udd is None
    result = await pool.launch(
        kind=desc["kind"],
        url=desc["url"],
        headed=None,
        label=desc["label"],
        profile=profile,
        ephemeral=stateless,
        session=session_scoped,
        badge=True,
    )
    new_id = result["instance_id"]
    old_id = desc["instance_id"]
    final_id = _finalize_id(pool, new_id, old_id, mode)
    fresh = pool.maybe_get(final_id)
    if fresh is not None:
        fresh._auto_relaunched = True
    desc["lost_record"]["relaunched_to"] = final_id
    _DRIVER_LOST.add(1, attributes={"outcome": "relaunched", "kind": desc["kind"]})
    incidents.record(
        incidents.CATEGORY_DRIVER_LOST,
        instance_id=old_id,
        kind=desc["kind"],
        url=desc["url"],
        outcome="relaunched",
        new_instance_id=final_id,
    )
    log.info("octowright.driver_relaunch.relaunched", old_instance_id=old_id, new_instance_id=final_id, mode=mode)


def _finalize_id(pool: Any, new_id: str, old_id: str, mode: str) -> str:
    """For keep-id, re-key the fresh session back to the original instance_id so
    existing client handles keep resolving; return the client-facing id."""
    if mode != "keep-id" or new_id == old_id:
        return new_id
    session = pool.maybe_get(new_id)
    if session is None:
        return new_id
    # Atomic-enough pop+set (no await between), matching the lockless eviction
    # path. The recording file stays under new_id — a documented keep-id wart.
    session.instance_id = old_id
    pool._sessions.pop(new_id, None)
    pool._sessions[old_id] = session
    pool._recently_evicted.pop(old_id, None)
    return old_id
