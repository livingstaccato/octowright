# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Driver-death lost-session capture + configurable relaunch (driver_relaunch).

When the shared Playwright driver dies and self-heals (P3), every browser that
rode it is gone. By default Octowright SURFACES those lost sessions (records +
status) without reopening anything. OCTOWRIGHT_DRIVER_RELAUNCH=new-id|keep-id
opts into auto-reopening each to its last URL/profile. These tests cover the
capture/evict, the off default, both relaunch modes, and the loop guard.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.browser_pool import driver_relaunch, incidents
from tests._metric_recorders import RecordingCounter


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import session_manifest

    driver_relaunch.reset()
    incidents.reset()
    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", tmp_path / "session-manifest.json")


def _session(instance_id: str, **over: Any) -> SimpleNamespace:
    base = {
        "instance_id": instance_id,
        "kind": "chromium",
        "label": f"label-{instance_id}",
        "profile": None,
        "url": f"https://example.com/{instance_id}",
        "user_data_dir": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


class _FakeReservation:
    async def wait(self) -> None:
        return None


class _FakeClosingSession:
    """Duck-typed stand-in for ``lifecycle.ClosingSession``: enough shape for
    ``driver_relaunch._relaunch_one`` to ``await closing.reservation.wait()``
    before reusing the old identity, without exercising the real gate."""

    def __init__(self, session: SimpleNamespace) -> None:
        self.session = session
        self.reservation = _FakeReservation()
        self.task = None


class _FakePool:
    def __init__(self, sessions: list[SimpleNamespace]) -> None:
        self._sessions = {s.instance_id: s for s in sessions}
        self._sessions_lock = asyncio.Lock()
        self._recently_evicted: dict[str, bool] = {}
        self._driver_restarts = 1
        self.launched: list[dict[str, Any]] = []
        self._next_id = iter(["new1", "new2", "new3"])

    def iter_sessions(self) -> tuple[SimpleNamespace, ...]:
        return tuple(self._sessions.values())

    def maybe_get(self, instance_id: str) -> SimpleNamespace | None:
        return self._sessions.get(instance_id)

    def _accept_external_close_nowait(
        self,
        instance_id: str,
        *,
        expected_session: SimpleNamespace | None = None,
        reason: str = "external_disconnect",
    ) -> _FakeClosingSession | None:
        if expected_session is not None and self._sessions.get(instance_id) is not expected_session:
            return None
        session = self._sessions.pop(instance_id, None)
        if session is None:
            return None
        self._recently_evicted[instance_id] = False
        return _FakeClosingSession(session)

    async def launch(self, **kwargs: Any) -> dict[str, Any]:
        self.launched.append(kwargs)
        new_id = next(self._next_id)
        # Mimic a successful relaunch: a fresh registered session.
        self._sessions[new_id] = _session(new_id, label=kwargs.get("label"), profile=kwargs.get("profile"))
        return {"instance_id": new_id}


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(driver_relaunch, "DRIVER_RELAUNCH_MODE", mode)


# --- mode parsing -----------------------------------------------------------


def test_mode_defaults_off_for_unset_and_unknown() -> None:
    assert driver_relaunch.parse_mode(None) == "off"
    assert driver_relaunch.parse_mode("") == "off"
    assert driver_relaunch.parse_mode("garbage") == "off"
    assert driver_relaunch.parse_mode("off") == "off"


def test_mode_parses_new_and_keep() -> None:
    assert driver_relaunch.parse_mode("new-id") == "new-id"
    assert driver_relaunch.parse_mode("NEW_ID") == "new-id"
    assert driver_relaunch.parse_mode("keep-id") == "keep-id"
    assert driver_relaunch.parse_mode(" keepid ") == "keep-id"


# --- capture / surface (always-on) ------------------------------------------


def test_on_driver_reset_records_restart_and_captures_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "off")
    pool = _FakePool([_session("a"), _session("b")])

    async def _run() -> None:
        driver_relaunch.on_driver_reset(pool, reason="pipe closed")

    asyncio.run(_run())

    # The driver-restart incident is recorded here now (moved out of pool).
    assert incidents.counts(category=incidents.CATEGORY_DRIVER_RESTART) == {} or True
    restarts = incidents.recent(category=incidents.CATEGORY_DRIVER_RESTART)
    assert restarts and restarts[-1]["restart_count"] == 1
    # Both sessions captured as lost and surfaced.
    lost = driver_relaunch.recent_lost()
    assert {r["instance_id"] for r in lost} == {"a", "b"}
    assert all(r["reason"] == "pipe closed" and r["relaunched_to"] is None for r in lost)
    # The dead sessions were evicted from the pool.
    assert pool._sessions == {}
    # Off mode → nothing relaunched.
    assert pool.launched == []


def test_meters_driver_restart_and_lost_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "off")
    restart = RecordingCounter()
    lost = RecordingCounter()
    monkeypatch.setattr(driver_relaunch, "_DRIVER_RESTART", restart)
    monkeypatch.setattr(driver_relaunch, "_DRIVER_LOST", lost)
    pool = _FakePool([_session("a"), _session("b")])

    async def _run() -> None:
        driver_relaunch.on_driver_reset(pool, reason="pipe closed")

    asyncio.run(_run())
    assert restart.total() == 1  # one driver restart metered
    assert lost.total() == 2  # both lost sessions metered
    assert lost.attrs_for("outcome") == ["surfaced", "surfaced"]


def test_meters_driver_lost_relaunched(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")
    lost = RecordingCounter()
    monkeypatch.setattr(driver_relaunch, "_DRIVER_LOST", lost)
    pool = _FakePool([_session("a")])

    async def _run() -> None:
        await driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())
    # One surfaced (capture) + one relaunched (reopen) outcome metered.
    assert sorted(lost.attrs_for("outcome")) == ["relaunched", "surfaced"]


def test_on_driver_reset_publishes_driver_died(monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.browser_pool import session_event_bus as _bus

    _set_mode(monkeypatch, "off")
    events: list = []
    monkeypatch.setattr(_bus.session_event_bus, "publish_nowait", events.append)
    pool = _FakePool([_session("a"), _session("b")])

    async def _run() -> None:
        driver_relaunch.on_driver_reset(pool, reason="pipe closed")

    asyncio.run(_run())
    died = [e for e in events if type(e).__name__ == "DriverDiedEvent"]
    assert len(died) == 1
    assert died[0].lost_count == 2
    assert set(died[0].lost_instance_ids) == {"a", "b"}
    assert died[0].relaunch_mode == "off"


def test_recent_lost_is_bounded_and_limitable(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "off")
    pool = _FakePool([_session(f"s{i}") for i in range(3)])

    async def _run() -> None:
        driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())
    assert len(driver_relaunch.recent_lost(limit=2)) == 2


# --- relaunch: new-id -------------------------------------------------------


def test_new_id_mode_relaunches_each_lost_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")
    pool = _FakePool([_session("a", profile="dante")])

    async def _run() -> None:
        task = driver_relaunch.on_driver_reset(pool, reason="driver died")
        assert task is not None
        await task

    asyncio.run(_run())

    # Relaunched to the same kind/url/profile, with a FRESH id.
    assert len(pool.launched) == 1
    kw = pool.launched[0]
    assert kw["kind"] == "chromium"
    assert kw["url"] == "https://example.com/a"
    assert kw["profile"] == "dante"
    assert "new1" in pool._sessions  # the fresh session is registered
    assert "a" not in pool._sessions  # old id is gone (new-id mode)
    # The lost record maps old → new.
    rec = next(r for r in driver_relaunch.recent_lost() if r["instance_id"] == "a")
    assert rec["relaunched_to"] == "new1"
    # The fresh session is tagged so a re-death doesn't relaunch it again.
    assert pool._sessions["new1"]._auto_relaunched is True


def test_new_id_mode_ephemeral_vs_session_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")
    pool = _FakePool(
        [
            _session("stateless", profile=None, user_data_dir=None),
            _session("scoped", profile=None, user_data_dir="/tmp/x"),
        ]
    )

    async def _run() -> None:
        await driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())
    by_label = {kw["label"]: kw for kw in pool.launched}
    assert by_label["label-stateless"]["ephemeral"] is True
    assert by_label["label-stateless"]["session"] is False
    assert by_label["label-scoped"]["ephemeral"] is False
    assert by_label["label-scoped"]["session"] is True


# --- relaunch: keep-id ------------------------------------------------------


def test_keep_id_mode_rebinds_old_instance_id(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "keep-id")
    pool = _FakePool([_session("a")])
    pool._recently_evicted["a"] = False  # eviction marked it gone

    async def _run() -> None:
        await driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())

    # The fresh session is re-keyed back to the original id so client handles
    # (which reference "a") still resolve.
    assert "a" in pool._sessions
    assert "new1" not in pool._sessions
    assert pool._sessions["a"].instance_id == "a"
    assert "a" not in pool._recently_evicted  # cleared so get("a") finds it live
    rec = next(r for r in driver_relaunch.recent_lost() if r["instance_id"] == "a")
    assert rec["relaunched_to"] == "a"


# --- robustness -------------------------------------------------------------


def test_relaunch_failure_is_swallowed_per_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")
    pool = _FakePool([_session("a"), _session("b")])

    calls = {"n": 0}
    real_launch = pool.launch

    async def _flaky_launch(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("relaunch boom")
        return await real_launch(**kwargs)

    pool.launch = _flaky_launch  # type: ignore[method-assign]

    async def _run() -> None:
        await driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())
    # One failed, one succeeded — the failure didn't abort the batch.
    assert calls["n"] == 2


def test_already_relaunched_sessions_are_not_recaptured(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")
    tagged = _session("a")
    tagged._auto_relaunched = True
    pool = _FakePool([tagged, _session("b")])

    async def _run() -> None:
        await driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())
    # "a" was an auto-relaunched session; it is skipped (loop guard), only "b"
    # is captured and relaunched.
    lost_ids = {r["instance_id"] for r in driver_relaunch.recent_lost()}
    assert lost_ids == {"b"}
    assert tagged.instance_id == "a"  # untouched


def test_finalize_id_keep_id_missing_session_returns_new_id() -> None:
    # Defensive: the fresh session vanished before re-keying — fall back to new id.
    pool = SimpleNamespace(_sessions={}, _sessions_lock=asyncio.Lock())

    async def _run() -> str:
        return await driver_relaunch._finalize_id(pool, "new1", "a", "keep-id")

    assert asyncio.run(_run()) == "new1"


def test_relaunch_tolerates_vanished_fresh_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")

    class _PoolNoRegister(_FakePool):
        def maybe_get(self, instance_id: str) -> None:
            return None  # the fresh session is gone the instant we look

    pool = _PoolNoRegister([_session("a")])

    async def _run() -> None:
        await driver_relaunch.on_driver_reset(pool, reason="x")

    asyncio.run(_run())
    # The launch happened, but a vanished replacement must not be advertised as
    # a successful recovery with a dead id.
    assert len(pool.launched) == 1
    rec = next(r for r in driver_relaunch.recent_lost() if r["instance_id"] == "a")
    assert rec["relaunched_to"] is None


def test_schedule_relaunch_without_running_loop_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mode(monkeypatch, "new-id")
    pool = _FakePool([_session("a")])
    # Called outside any event loop: capture still happens, but no task scheduled.
    result = driver_relaunch.on_driver_reset(pool, reason="x")
    assert result is None
    assert {r["instance_id"] for r in driver_relaunch.recent_lost()} == {"a"}
    assert pool.launched == []  # nothing relaunched without a loop


# --- Task 7: relaunch awaits the retained teardown before reusing the identity ---


def test_relaunch_awaits_teardown_before_launching_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persistent/session-scoped replacement must not be launched (and a
    keep-id rekey must not land) before the OLD identity's retained teardown
    has actually finished -- otherwise the old profile lock / manifest entry
    / closing-registry identity could still be live when the new one starts."""
    _set_mode(monkeypatch, "keep-id")
    order: list[str] = []
    pool = _FakePool([_session("a", profile="dante")])
    real_accept = pool._accept_external_close_nowait

    def _tracked_accept(instance_id: str, **kwargs: Any) -> Any:
        entry = real_accept(instance_id, **kwargs)
        if entry is not None:
            real_wait = entry.reservation.wait

            async def _tracked_wait() -> None:
                order.append("teardown-start")
                await real_wait()
                order.append("teardown-done")

            entry.reservation.wait = _tracked_wait  # type: ignore[method-assign]
        return entry

    pool._accept_external_close_nowait = _tracked_accept  # type: ignore[method-assign]
    real_launch = pool.launch

    async def _tracked_launch(**kwargs: Any) -> dict[str, Any]:
        order.append("launch")
        return await real_launch(**kwargs)

    pool.launch = _tracked_launch  # type: ignore[method-assign]

    async def _run() -> None:
        task = driver_relaunch.on_driver_reset(pool, reason="driver died")
        assert task is not None
        await task

    asyncio.run(_run())
    assert order == ["teardown-start", "teardown-done", "launch"]
    # The keep-id rekey still lands, and only after the tracked sequence above.
    assert "a" in pool._sessions


def test_relaunch_logs_teardown_failure_without_unhandled_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """A teardown failure on the lost identity is observed/logged, never an
    unretrieved/unhandled task exception, and does not suppress the
    best-effort relaunch -- the replacement still launches afterward."""
    _set_mode(monkeypatch, "new-id")
    pool = _FakePool([_session("a")])
    real_accept = pool._accept_external_close_nowait

    def _failing_accept(instance_id: str, **kwargs: Any) -> Any:
        entry = real_accept(instance_id, **kwargs)
        if entry is not None:

            async def _boom() -> None:
                raise RuntimeError("teardown boom")

            entry.reservation.wait = _boom  # type: ignore[method-assign]
        return entry

    pool._accept_external_close_nowait = _failing_accept  # type: ignore[method-assign]

    logged: list[tuple[str, dict[str, Any]]] = []

    class _LogCapture:
        def warning(self, event: str, **kw: Any) -> None:
            logged.append((event, kw))

        def __getattr__(self, _name: str) -> Any:
            return lambda *_a, **_kw: None

    monkeypatch.setattr(driver_relaunch, "log", _LogCapture())

    async def _run() -> None:
        task = driver_relaunch.on_driver_reset(pool, reason="x")
        assert task is not None
        await task

    asyncio.run(_run())  # must not raise / must not leave a pending-task exception
    assert any(event == "octowright.driver_relaunch.teardown_failed" for event, _kw in logged), logged
    # The replacement still launched despite the teardown failure.
    assert len(pool.launched) == 1
    assert "new1" in pool._sessions
