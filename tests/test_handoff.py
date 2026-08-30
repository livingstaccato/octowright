# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool import BrowserPool
from octowright.browser_pool import close_helpers as _close_helpers
from tests._pool_invariants import wait_until


def _fake_source(
    *,
    instance_id: str,
    kind: str = "chromium",
    label: str | None = None,
    profile: str | None = None,
    url: str = "https://octowright.com",
    user_data_dir: Any = None,
    har_path: Any = None,
    stabilize: bool = False,
    trace: bool = False,
) -> Any:
    """A duck-typed handoff/relaunch source carrying a REAL
    ``SessionOperationGate`` -- Task 8 routes ``close_original=True`` through
    ``close_with_preparation``, which drives ``_operation_gate`` directly and
    calls ``session.operation(...)`` from inside the preparation callback, so
    a bare ``SimpleNamespace`` (no gate, no ``.operation``) can no longer
    stand in for the source. Mirrors ``test_browser_pool_branches._fake_session``
    -- ``log_path``/``video_path``/``trace_path`` are required too: the real
    coordinator's ``close_response``/``publish_close_once`` read them
    unconditionally once teardown actually runs (a duck type missing them
    used to be safe here only because the old tests mocked ``pool.close``
    away entirely, never reaching that code)."""
    from octowright.session.operation_gate import SessionOperationGate

    gate = SessionOperationGate(instance_id, kind)
    source = SimpleNamespace(
        instance_id=instance_id,
        kind=kind,
        label=label,
        profile=profile,
        url=url,
        user_data_dir=user_data_dir,
        har_path=har_path,
        stabilize=stabilize,
        trace=trace,
        protected=False,
        protected_reason="explicit",
        page=SimpleNamespace(url=url),
        log_path=f"/tmp/{instance_id}.jsonl",
        video_path=None,
        trace_path=None,
        _teardown_after_close_cutoff=AsyncMock(),
        _operation_gate=gate,
    )
    source.operation = gate.operation
    source.operation_snapshot = gate.snapshot

    async def _set_protected_state(protected_value: bool, *, reason: str = "explicit") -> dict[str, object]:
        def _commit() -> dict[str, object]:
            source.protected = protected_value
            source.protected_reason = reason
            return {"instance_id": instance_id, "protected": protected_value}

        return await gate.control_update("browser_set_protected", _commit)

    source.set_protected_state = _set_protected_state
    return source


def _pop_manifest_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real close coordinator best-effort-removes a session-manifest
    entry on every close; keep these unit tests off the real on-disk
    manifest (matching test_browser_pool_branches.py's pattern)."""
    monkeypatch.setattr(_close_helpers, "remove_manifest_session", lambda _id: None)


@pytest.mark.anyio
async def test_handoff_reuses_profile_and_closes_original(monkeypatch: pytest.MonkeyPatch) -> None:
    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(
        instance_id="old01",
        kind="webkit",
        profile="dante",
        label="lab",
        url="https://octowright.com/app",
        user_data_dir="/tmp/profile-dir",
    )
    source.page.url = "https://octowright.com/live"
    pool._sessions["old01"] = source
    launched: dict[str, Any] = {}

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.update(kwargs)
        return {
            "instance_id": "new01",
            "kind": kwargs["kind"],
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/new01.jsonl",
            "record_video": kwargs.get("record_video", False),
            "trace": kwargs.get("trace", False),
        }

    monkeypatch.setattr(pool, "launch", _fake_launch)

    result = await pool.handoff("old01", headed=False)

    assert result["old_instance_id"] == "old01"
    assert result["new_instance_id"] == "new01"
    assert result["old_closed"] is True
    assert result["profile"] == "dante"
    source._teardown_after_close_cutoff.assert_awaited_once()
    # Regression: handoff replacements must keep the corner badge (a bare
    # `badge: bool = False` default in _launch_from_snapshot silently
    # dropped it for every non-fluid handoff).
    assert launched["badge"] is True


@pytest.mark.anyio
async def test_handoff_rejects_stateless_without_opt_in() -> None:
    pool = BrowserPool()
    pool._sessions["old02"] = _fake_source(instance_id="old02", profile=None, user_data_dir=None)

    with pytest.raises(ValueError, match="accept_stateless=True"):
        await pool.handoff("old02", headed=True)


@pytest.mark.anyio
async def test_handoff_rejects_keep_original_for_persistent() -> None:
    pool = BrowserPool()
    pool._sessions["old03"] = _fake_source(
        instance_id="old03",
        kind="firefox",
        profile="mortimer",
        label="mortimer",
        user_data_dir="/tmp/ops",
    )

    with pytest.raises(ValueError, match="close_original=True"):
        await pool.handoff("old03", headed=False, close_original=False)


@pytest.mark.asyncio
async def test_handoff_preserves_session_scoped_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(
        instance_id="old-session",
        label="scratch",
        profile=None,
        user_data_dir=tmp_path / "session-dir",
    )
    pool._sessions["old-session"] = source
    launched: dict[str, object] = {}

    async def fake_launch(**kwargs: object) -> dict[str, object]:
        launched.update(kwargs)
        return {
            "instance_id": "new-session",
            "kind": "chromium",
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/new.jsonl",
            "record_video": False,
            "trace": False,
        }

    monkeypatch.setattr(pool, "launch", fake_launch)

    result = await BrowserPool.handoff(pool, "old-session", headed=False)

    assert result["new_instance_id"] == "new-session"
    assert launched["session"] is True
    assert launched["profile"] is None


# ─── Eviction-mid-handoff race regression ────────────────────────────────────


def _get_then_evict(pool: BrowserPool) -> Any:
    """Simulate a REAL Playwright external-close eviction firing in the gap
    between ``handoff_browser``'s ``pool.get(old_instance_id)`` snapshot and
    the close reservation resolving the SAME identity: the session is
    returned once, then immediately routed through
    ``pool._accept_external_close_nowait`` -- the actual seam
    ``listeners._evict`` uses, which both pops ``_sessions`` AND installs a
    teardown-only ``ClosingSession`` entry for the whole eviction duration.

    A direct ``_sessions.pop`` (the old version of this helper) only
    reproduces the ``KeyError`` half of the race: it leaves no
    ``_closing_sessions`` entry behind, so ``reserve_close_browser`` never
    exercises its ``require_fresh``-vs-``SessionClosingError`` branch --
    exactly the gap a real eviction hits, since the external coordinator
    IS already draining the session by the time the fallback runs."""

    def _get(instance_id: str) -> Any:
        session = pool._sessions.get(instance_id)
        if session is None:
            raise KeyError(pool._missing_session_message(instance_id))
        won = pool._accept_external_close_nowait(instance_id, expected_session=session, reason="user_close")
        assert won is not None, "external-close acceptance seam declined to take ownership"
        return session

    return _get


@pytest.mark.anyio
async def test_handoff_survives_eviction_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a REAL Playwright external-close eviction can fire AFTER
    handoff_browser's `pool.get(old_instance_id)` snapshot but BEFORE the
    close reservation resolves the same identity. The external coordinator
    already owns a `_closing_sessions` entry for it by then, so
    `reserve_close_browser(require_fresh=True)` raises `SessionClosingError`
    (not `KeyError`) -- both must be caught, or the entire handoff aborts
    with no replacement launched. The replacement is still launched from a
    pre-close fallback snapshot of `source`.
    """
    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(
        instance_id="evict01",
        profile="dante",
        label="dante-lab",
        url="https://octowright.com/app",
        user_data_dir="/tmp/profile-dir",
    )
    source.page.url = "https://octowright.com/live"
    pool._sessions["evict01"] = source
    monkeypatch.setattr(pool, "get", _get_then_evict(pool))

    launched: dict[str, Any] = {}

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.update(kwargs)
        return {
            "instance_id": "newAfterEvict",
            "kind": kwargs["kind"],
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/newAfterEvict.jsonl",
            "record_video": False,
            "trace": False,
        }

    monkeypatch.setattr(pool, "launch", _fake_launch)

    # Before the fix: this raised SessionClosingError and aborted the whole
    # handoff. After the fix: handoff completes, launching the replacement
    # with the pre-close snapshotted fields.
    result = await pool.handoff("evict01", headed=False)

    assert result["new_instance_id"] == "newAfterEvict"
    assert result["old_instance_id"] == "evict01"
    # old_closed=False because OUR close raced the external eviction (it
    # never got its own ticket) -- the external coordinator did the actual
    # teardown, tracked separately below.
    assert result["old_closed"] is False
    assert result["profile"] == "dante"
    assert launched["profile"] == "dante"
    assert launched["kind"] == "chromium"
    assert launched["label"] == "dante-lab"
    # Handoff replacements keep the corner badge (regression: a bare
    # `badge: bool = False` default silently dropped it).
    assert launched["badge"] is True

    # The external coordinator's own teardown-only close must still run to
    # completion and clear the registry -- it isn't ours to await directly.
    await wait_until(lambda: "evict01" not in pool._closing_sessions)
    source._teardown_after_close_cutoff.assert_awaited_once()


@pytest.mark.anyio
async def test_relaunch_fluid_survives_eviction_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same race as handoff, but for relaunch_fluid: a REAL external-close
    eviction fires between pool.get() snapshot and the close reservation
    resolving, landing on SessionClosingError (see test_handoff_survives_
    eviction_race). The replacement must still launch. relaunch_fluid is a
    LIVE production path (server/browser/lifecycle.browser_relaunch_fluid),
    so this is the regression with real user-facing blast radius.
    """
    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(
        instance_id="fluid01",
        profile=None,
        label="scratch",
        url="https://octowright.com/app",
        user_data_dir=None,
    )
    source.page.url = "https://octowright.com/live"
    pool._sessions["fluid01"] = source
    monkeypatch.setattr(pool, "get", _get_then_evict(pool))

    launched: dict[str, Any] = {}

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        launched.update(kwargs)
        return {
            "instance_id": "fluidAfterEvict",
            "kind": kwargs["kind"],
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/fluidAfterEvict.jsonl",
            "record_video": False,
            "trace": False,
        }

    monkeypatch.setattr(pool, "launch", _fake_launch)

    result = await pool.relaunch_fluid("fluid01")

    assert result["new_instance_id"] == "fluidAfterEvict"
    assert result["old_instance_id"] == "fluid01"
    assert result["old_closed"] is False
    assert result["mode"] == "fluid"
    assert launched["kind"] == "chromium"
    assert launched["label"] == "scratch"
    assert launched["badge"] is True
    # stateless source → ephemeral=True
    assert launched["ephemeral"] is True

    await wait_until(lambda: "fluid01" not in pool._closing_sessions)
    source._teardown_after_close_cutoff.assert_awaited_once()


@pytest.mark.anyio
async def test_handoff_close_aborted_by_ceiling_propagates_instead_of_stale_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ceiling breach mid-teardown is NOT the ordinary close-vs-eviction
    race ``_close_with_fallback_snapshot`` otherwise falls back from (Task 3
    review round 3, D1). Driven end to end through ``pool.handoff``, not
    just at the gate level: ``_teardown_after_close_cutoff`` hangs,
    preparation (spied on below, not assumed) has already produced a fresh
    snapshot by the time the ceiling fires, and the close is expected to
    raise ``SessionCloseAbortedError`` -- discarding that snapshot for a
    stale pre-close read and launching a replacement over an unconfirmed
    teardown would risk exactly the ``SingletonLock`` collision the module
    docstring warns about.
    """
    import octowright.browser_pool.relaunch as relaunch_mod
    from octowright.session.operation_gate import SessionCloseAbortedError

    _pop_manifest_noop(monkeypatch)
    pool = BrowserPool()
    source = _fake_source(
        instance_id="wedge-handoff",
        profile="dante",
        label="dante-lab",
        url="https://octowright.com/app",
        user_data_dir="/tmp/profile-dir",
    )
    pool._sessions["wedge-handoff"] = source

    original_prepare = relaunch_mod._prepare_handoff_snapshot
    prepared_snapshots: list[Any] = []

    async def _spy_prepare(session: Any) -> Any:
        result = await original_prepare(session)
        prepared_snapshots.append(result)
        return result

    monkeypatch.setattr(relaunch_mod, "_prepare_handoff_snapshot", _spy_prepare)

    teardown_entered = asyncio.Event()
    never = asyncio.Event()

    async def _hang_teardown(reason: str | None = None) -> None:
        teardown_entered.set()
        await never.wait()

    source._teardown_after_close_cutoff = _hang_teardown

    launch_calls = {"count": 0}

    async def _fake_launch(**kwargs: Any) -> dict[str, Any]:
        launch_calls["count"] += 1
        return {"instance_id": "should-not-launch", "kind": kwargs["kind"], "log_path": "/tmp/x.jsonl"}

    monkeypatch.setattr(pool, "launch", _fake_launch)

    handoff_task = asyncio.create_task(pool.handoff("wedge-handoff", headed=False))
    async with asyncio.timeout(5):
        await teardown_entered.wait()

    # Preparation genuinely ran and produced a snapshot before the ceiling
    # fired -- not inferred from timing alone.
    assert len(prepared_snapshots) == 1
    # The coordinator (not some other displaced owner) holds the gate under
    # its own reservation's root operation name throughout preparation AND
    # the now-hung teardown.
    assert source.operation_snapshot()["active_operation"] == "browser_handoff"

    async with asyncio.timeout(5):
        assert await source._operation_gate.enforce_active_timeout(0.0) is True

    async with asyncio.timeout(5):
        with pytest.raises(SessionCloseAbortedError):
            await handoff_task

    # No replacement was launched over the unconfirmed teardown.
    assert launch_calls["count"] == 0
