# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Post-merge review findings: identity-race hardening + bounded state lock.

Two defects found by the multi-agent review of PR #100/#101 after merge:

1. Originally: ``listeners._on_page_close`` scheduled an async full close and
   then called ``pool.close(instance_id, force=True)`` later, closing
   whatever the registry held under that id AT THAT (later) TIME --
   ``OCTOWRIGHT_DRIVER_RELAUNCH=keep-id`` deliberately rebinds the original
   instance_id to a NEW live session, so a stale deferred task could
   force-close an unrelated (possibly protected) browser. Task 7's durable
   close coordinator restructures this: ``_on_page_close`` now calls the
   synchronous ``pool._accept_external_close_nowait`` directly (no deferred
   task, no scheduling gap), and identity is checked at acceptance time via
   ``expected_session``. These tests now pin that seam's identity-safety
   directly instead of the (now-removed) deferred-task shape.

2. ``bridge_state._state_lock`` took a blocking ``flock(LOCK_EX)`` with no
   timeout on the caller's asyncio event loop (follower reconnect loop, leader
   housekeeping). A follower SIGSTOPped mid-transaction — the documented
   compaction-freeze scenario — holds the lock across the freeze (flock is not
   released on SIGSTOP), wedging every other process's event loop indefinitely.
   The lock wait must be bounded, and a contended snapshot must be skipped
   rather than run an unlocked read-modify-write that can erase a peer update.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import bridge_state
from octowright.browser_pool import BrowserPool, driver_relaunch
from tests._pool_invariants import wait_until


@pytest.fixture
def anyio_backend() -> str:
    # The close coordinator is asyncio-native (asyncio.Task/Future) -- see
    # session/operation_gate.py. These tests exercise it directly.
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolate_session_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import session_manifest

    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "default-session-manifest.json")


# --- 1. external-close identity safety -----------------------------------


class _EventTarget:
    def __init__(self) -> None:
        self.handlers: dict[str, list[object]] = {}

    def on(self, event: str, callback: object) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def fire(self, event: str) -> None:
        for callback in self.handlers.get(event, []):
            callback()  # type: ignore[operator]


def _session(instance_id: str) -> SimpleNamespace:
    from octowright.session.operation_gate import SessionOperationGate

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
        protected_reason="explicit",
        context=_EventTarget(),
        browser=None,
        recorder=MagicMock(),
        close=AsyncMock(),
        _teardown_after_close_cutoff=AsyncMock(),
        _operation_gate=SessionOperationGate(instance_id, "chromium"),
        _crashed=False,
    )


@pytest.mark.anyio
async def test_external_close_identity_check_ignores_keep_id_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late external-close signal captured against the OLD session object
    must not touch the registry once a keep-id rekey has moved that id to a
    NEW session -- ``expected_session`` identity is checked synchronously,
    with no scheduling gap for a rekey to land in (Task 7 removed the
    deferred-task shape this used to race: ``_accept_external_close_nowait``
    is called directly from the sync Playwright callback, no ``create_task``
    in between)."""
    from octowright.browser_pool import lifecycle

    pool = BrowserPool()
    original = _session("b1")
    replacement = _session("new1")
    pool._sessions["b1"] = original
    pool._sessions["new1"] = replacement
    monkeypatch.setattr(lifecycle, "remove_manifest_session", lambda _instance_id: None)

    # Simulate the id having already been rekeyed to `replacement` (as
    # keep-id relaunch does) before the OLD session's late external-close
    # signal arrives.
    pool._sessions["b1"] = replacement
    del pool._sessions["new1"]

    result = pool._accept_external_close_nowait("b1", expected_session=original, reason="user_close")
    assert result is None
    assert pool.maybe_get("b1") is replacement
    original._teardown_after_close_cutoff.assert_not_awaited()
    replacement._teardown_after_close_cutoff.assert_not_awaited()


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
    from octowright.browser_pool import lifecycle, listeners

    pool = BrowserPool()
    replacement = _session("new1")
    pool._sessions["new1"] = replacement
    listeners._wire_close_evictor(pool, replacement)
    monkeypatch.setattr(lifecycle, "remove_manifest_session", lambda _instance_id: None)
    publish = MagicMock()
    monkeypatch.setattr(listeners.session_event_bus, "publish_nowait", publish)

    final_id = await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id")
    assert final_id == "old1"

    replacement.context.fire("close")
    # The retained coordinator (not the sync listener callback) does the
    # manifest removal/publish; wait for it rather than a removed pending-
    # task set.
    await wait_until(lambda: "old1" not in pool._closing_sessions)

    assert pool.maybe_get("old1") is None
    assert pool._recently_evicted["old1"] is False
    replacement._teardown_after_close_cutoff.assert_awaited_once()
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
            pool._accept_external_close_nowait("new1", expected_session=replacement, reason="user_close")
        return result

    monkeypatch.setattr(session_manifest, "run_manifest_transaction_async", _close_during_rekey)

    with pytest.raises(RuntimeError, match="closed while it was being rebound"):
        await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id")
    assert pool.maybe_get("new1") is None
    assert pool.maybe_get("old1") is None
    assert session_manifest.read_manifest()["sessions"] == {}


@pytest.mark.anyio
async def test_close_follows_expected_session_across_keep_id_rekey(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller holding a STALE id (captured before a keep-id rekey) still
    closes the correct object: ``reserve_close_browser``'s identity-aware
    resolution scans by OBJECT identity, not just the caller's id."""
    from octowright.browser_pool import lifecycle

    pool = BrowserPool()
    replacement = _session("new1")
    pool._sessions["new1"] = replacement
    monkeypatch.setattr(lifecycle, "remove_manifest_session", lambda _instance_id: None)

    assert await driver_relaunch._finalize_id(pool, "new1", "old1", "keep-id") == "old1"
    await pool.close("new1", force=True, _expected_session=replacement)

    assert pool.maybe_get("old1") is None
    replacement._teardown_after_close_cutoff.assert_awaited_once()


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
