# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Post-merge review findings: deferred-close identity race + bounded state lock.

Two defects found by the multi-agent review of PR #100/#101 after merge:

1. ``listeners._on_page_close`` schedules an async full close and then calls
   ``pool.close(instance_id, force=True)``. The "last page is gone" decision is
   made in the SYNC callback, but the task runs later and closes whatever the
   registry holds under that id AT THAT TIME. ``OCTOWRIGHT_DRIVER_RELAUNCH=keep-id``
   deliberately rebinds the original instance_id to a NEW live session, so the
   stale task could force-close an unrelated (possibly protected) browser —
   ``force=True`` skips the protection refusal by design.

2. ``bridge_state._state_lock`` took a blocking ``flock(LOCK_EX)`` with no
   timeout on the caller's asyncio event loop (follower reconnect loop, leader
   housekeeping). A follower SIGSTOPped mid-transaction — the documented
   compaction-freeze scenario — holds the lock across the freeze (flock is not
   released on SIGSTOP), wedging every other process's event loop indefinitely.
   Before the lock existed no process could block another's write, so the fix
   must bound the wait and fall back to the old wait-free behavior.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from octowright import bridge_state

# --- 1. deferred full close must re-validate session identity -----------------


class _FakePool:
    def __init__(self, registry: dict[str, Any]) -> None:
        self._registry = registry
        self.closed: list[tuple[str, bool]] = []

    def maybe_get(self, instance_id: str) -> Any:
        return self._registry.get(instance_id)

    async def close(self, instance_id: str, *, force: bool = False, _reason: str | None = None) -> None:
        self.closed.append((instance_id, force))


class _FakeSession:
    def __init__(self, name: str) -> None:
        self.name = name


@pytest.mark.anyio
async def test_deferred_close_skips_when_instance_id_rebound() -> None:
    """A keep-id driver relaunch rebinds the id to a NEW session before the
    deferred task runs; the task must not force-close the replacement."""
    from octowright.browser_pool.listeners import _run_deferred_full_close

    original = _FakeSession("original")
    replacement = _FakeSession("replacement")
    pool = _FakePool({"b1": replacement})  # id now points at a different session

    await _run_deferred_full_close(pool, "b1", original, "user_close")

    assert pool.closed == [], "stale deferred close force-closed a rebound instance_id"


@pytest.mark.anyio
async def test_deferred_close_runs_for_the_same_session() -> None:
    from octowright.browser_pool.listeners import _run_deferred_full_close

    session = _FakeSession("original")
    pool = _FakePool({"b1": session})

    await _run_deferred_full_close(pool, "b1", session, "user_close")

    assert pool.closed == [("b1", True)]


@pytest.mark.anyio
async def test_deferred_close_skips_when_already_evicted() -> None:
    from octowright.browser_pool.listeners import _run_deferred_full_close

    session = _FakeSession("original")
    pool = _FakePool({})  # racing context.close already evicted it

    await _run_deferred_full_close(pool, "b1", session, "user_close")

    assert pool.closed == []


# --- 2. bridge-state lock must be bounded, never an indefinite block ----------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_state_lock_gives_up_instead_of_blocking_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A foreign holder must not wedge the caller: the lock attempt is bounded
    and then proceeds unlocked (the pre-lock wait-free behavior)."""
    import fcntl

    monkeypatch.setattr(bridge_state, "STATE_LOCK_TIMEOUT_SECONDS", 0.25)
    state_path = tmp_path / "bridge-state.json"
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Hold the flock from an independent open file description (this conflicts
    # even within one process), emulating a frozen peer mid-transaction.
    holder = open(lock_path, "a+b")  # noqa: SIM115
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        loop = asyncio.new_event_loop()
        try:
            # Bound the whole call: if _state_lock blocks, this never returns.
            entered = False
            with bridge_state._state_lock(state_path):
                entered = True
            assert entered
        finally:
            loop.close()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_state_lock_still_serializes_when_uncontended(tmp_path: Path) -> None:
    """The lost-update fix must survive: an uncontended transaction still locks."""
    state_path = tmp_path / "bridge-state.json"
    with bridge_state._state_lock(state_path):
        pass
    assert state_path.with_suffix(state_path.suffix + ".lock").exists()
