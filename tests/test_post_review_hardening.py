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
   The lock wait must be bounded, and a contended snapshot must be skipped
   rather than run an unlocked read-modify-write that can erase a peer update.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import bridge_state
from octowright.browser_pool import BrowserPool, driver_relaunch


@pytest.fixture(autouse=True)
def _isolate_session_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import session_manifest

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "default-session-manifest.json")


# --- 1. deferred full close must re-validate session identity -----------------


class _EventTarget:
    def __init__(self) -> None:
        self.handlers: dict[str, list[object]] = {}

    def on(self, event: str, callback: object) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def fire(self, event: str) -> None:
        for callback in self.handlers.get(event, []):
            callback()  # type: ignore[operator]


def _session(instance_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        instance_id=instance_id,
        kind="chromium",
        label=instance_id,
        profile=None,
        url=f"https://example.test/{instance_id}",
        log_path=f"/tmp/{instance_id}.jsonl",
        video_path=None,
        trace_path=None,
        har_path=None,
        protected=False,
        context=_EventTarget(),
        browser=None,
        recorder=MagicMock(),
        close=AsyncMock(),
    )


@pytest.mark.anyio
async def test_deferred_close_cannot_pop_keep_id_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identity validation and pop must share the registry lock with keep-id.

    Holding the real BrowserPool lock lets the deferred close validate the old
    session and then stall before its pop. A keep-id relaunch used to re-key a
    replacement during that gap, so the stale close popped and force-closed it.
    """
    from octowright.browser_pool import lifecycle
    from octowright.browser_pool.listeners import _run_deferred_full_close

    pool = BrowserPool()
    original = _session("b1")
    replacement = _session("new1")
    pool._sessions["b1"] = original
    pool._sessions["new1"] = replacement
    monkeypatch.setattr(lifecycle, "remove_manifest_session", lambda _instance_id: None)

    close_started = asyncio.Event()
    real_close = pool.close

    async def _observed_close(*args: object, **kwargs: object) -> dict[str, object]:
        close_started.set()
        return await real_close(*args, **kwargs)  # type: ignore[arg-type]

    async def _reuse_registered_replacement(**_kwargs: object) -> dict[str, str]:
        return {"instance_id": "new1"}

    monkeypatch.setattr(pool, "close", _observed_close)
    monkeypatch.setattr(pool, "launch", _reuse_registered_replacement)
    descriptor = {
        "instance_id": "b1",
        "kind": "chromium",
        "label": "replacement",
        "profile": None,
        "url": "https://example.test/b1",
        "user_data_dir": None,
        "lost_record": {"relaunched_to": None},
    }

    async with pool._sessions_lock:
        close_task = asyncio.create_task(_run_deferred_full_close(pool, "b1", original, "user_close"))
        await asyncio.wait_for(close_started.wait(), timeout=1.0)
        rekey_task = asyncio.create_task(driver_relaunch._relaunch_one(pool, descriptor, "keep-id"))
        await asyncio.sleep(0)
        assert not close_task.done()
        assert not rekey_task.done()

    await asyncio.gather(close_task, rekey_task)

    assert pool.maybe_get("b1") is replacement
    assert pool.maybe_get("new1") is None
    original.close.assert_awaited_once()
    replacement.close.assert_not_awaited()


@pytest.mark.anyio
async def test_stale_external_close_listener_cannot_evict_keep_id_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late old-session callback must not evict the session now using its id."""
    from octowright import session_manifest
    from octowright.browser_pool.listeners import _wire_close_evictor

    pool = BrowserPool()
    original = _session("b1")
    replacement = _session("new1")
    pool._sessions["b1"] = original
    pool._sessions["new1"] = replacement
    _wire_close_evictor(pool, original)
    monkeypatch.setattr(session_manifest, "remove_session", lambda _instance_id: None)

    final_id = await driver_relaunch._finalize_id(pool, "new1", "b1", "keep-id")
    assert final_id == "b1"
    assert pool.maybe_get("b1") is replacement

    original.context.fire("close")

    assert pool.maybe_get("b1") is replacement
    assert "b1" not in pool._recently_evicted
    original.recorder.record.assert_not_called()


@pytest.mark.anyio
async def test_rekeyed_replacement_listener_evicts_its_current_instance_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replacement's own listener must follow its keep-id rekey."""
    from octowright import session_manifest
    from octowright.browser_pool import listeners

    pool = BrowserPool()
    replacement = _session("new1")
    pool._sessions["new1"] = replacement
    listeners._wire_close_evictor(pool, replacement)
    remove_manifest = MagicMock()
    monkeypatch.setattr(session_manifest, "remove_session", remove_manifest)
    publish = MagicMock()
    monkeypatch.setattr(listeners.session_event_bus, "publish_nowait", publish)

    final_id = await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id")
    assert final_id == "old1"

    replacement.context.fire("close")
    await asyncio.gather(*tuple(listeners._PENDING_MANIFEST_REMOVALS))

    assert pool.maybe_get("old1") is None
    assert pool._recently_evicted["old1"] is False
    remove_manifest.assert_called_once_with("old1")
    assert publish.call_args.args[0].instance_id == "old1"


@pytest.mark.anyio
async def test_keep_id_rekeys_replacement_manifest_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pool's preserved id and launch-manifest identity must stay aligned."""
    from octowright import session_manifest

    manifest_path = tmp_path / "session-manifest.json"
    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", manifest_path)
    for instance_id, log_name in (("old1", "stale.jsonl"), ("new1", "replacement.jsonl")):
        session_manifest.record_launch(
            session_id=instance_id,
            kind="chromium",
            label=instance_id,
            profile=None,
            user_data_dir=None,
            log_path=tmp_path / log_name,
        )

    pool = BrowserPool()
    replacement = _session("new1")
    pool._sessions["new1"] = replacement

    assert await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id") == "old1"

    sessions = session_manifest.read_manifest()["sessions"]
    assert list(sessions) == ["old1"]
    assert sessions["old1"]["session_id"] == "old1"
    assert sessions["old1"]["log_path"] == str(tmp_path / "replacement.jsonl")
    assert session_manifest.remove_session("old1")
    assert session_manifest.read_manifest()["sessions"] == {}


@pytest.mark.anyio
async def test_keep_id_rekey_reconciles_replacement_closed_during_manifest_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sync close callback during the off-thread await must not resurrect or KeyError."""
    from octowright import session_manifest

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "session-manifest.json")
    session_manifest.record_launch(
        session_id="new1",
        kind="chromium",
        label=None,
        profile=None,
        user_data_dir=None,
        log_path=tmp_path / "replacement.jsonl",
    )
    pool = BrowserPool()
    replacement = _session("new1")
    pool._sessions["new1"] = replacement
    original_runner = session_manifest.run_manifest_transaction_async

    async def _close_during_rekey(func: object, *args: object, **kwargs: object) -> object:
        result = await original_runner(func, *args, **kwargs)  # type: ignore[arg-type]
        if getattr(func, "__name__", "") == "rekey_session":
            pool._evict_session_nowait("new1", expected_session=replacement)
        return result

    monkeypatch.setattr(session_manifest, "run_manifest_transaction_async", _close_during_rekey)

    with pytest.raises(RuntimeError, match="closed while it was being rebound"):
        await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id")
    assert pool.maybe_get("new1") is None
    assert pool.maybe_get("old1") is None
    assert session_manifest.read_manifest()["sessions"] == {}


@pytest.mark.anyio
async def test_deferred_close_follows_expected_session_across_keep_id_rekey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A task scheduled under the temporary id must close that object after rekey."""
    from octowright.browser_pool import lifecycle
    from octowright.browser_pool.listeners import _run_deferred_full_close

    pool = BrowserPool()
    replacement = _session("new1")
    pool._sessions["new1"] = replacement
    monkeypatch.setattr(lifecycle, "remove_manifest_session", lambda _instance_id: None)

    assert await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id") == "old1"
    await _run_deferred_full_close(pool, "new1", replacement, "user_close")

    assert pool.maybe_get("old1") is None
    replacement.close.assert_awaited_once()


# --- 2. bridge-state lock must be bounded, never an indefinite block ----------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_contended_record_snapshot_is_skipped_within_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A foreign holder bounds the caller and leaves shared state untouched."""
    import fcntl

    monkeypatch.setattr(bridge_state, "STATE_LOCK_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(bridge_state, "STATE_LOCK_POLL_SECONDS", 0.005)
    state_path = tmp_path / "bridge-state.json"
    bridge_state.record_snapshot(
        path=state_path,
        follower_pid=123,
        remote_url="http://127.0.0.1:8765/mcp/",
        remote_session_id="before-contention",
        last_error=None,
        in_flight=0,
        reconnect_attempts=0,
        request_timeouts=0,
    )
    before = state_path.read_text()
    lock_path = state_path.with_suffix(state_path.suffix + ".lock")

    # Hold the flock from an independent open file description (this conflicts
    # even within one process), emulating a frozen peer mid-transaction.
    holder = open(lock_path, "a+b")  # noqa: SIM115
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        started = time.monotonic()
        bridge_state.record_snapshot(
            path=state_path,
            follower_pid=123,
            remote_url="http://127.0.0.1:8765/mcp/",
            remote_session_id="must-be-skipped",
            last_error="contended",
            in_flight=1,
            reconnect_attempts=1,
            request_timeouts=1,
        )
        elapsed = time.monotonic() - started
        after = state_path.read_text()
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    assert elapsed < 0.5
    assert after == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock semantics")
def test_state_lock_still_serializes_when_uncontended(tmp_path: Path) -> None:
    """The lost-update fix must survive: an uncontended transaction still locks."""
    state_path = tmp_path / "bridge-state.json"
    with bridge_state._state_lock(state_path) as acquired:
        assert acquired is True
    assert state_path.with_suffix(state_path.suffix + ".lock").exists()
