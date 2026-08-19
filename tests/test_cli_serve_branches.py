# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.cli.serve helpers.

Targets the small coordination helpers that the existing integration-style
test_serve_promotion + test_serve_stdio_eof don't pin individually:

- _log_first_done: every (done/cancelled/error/pending) state combination
- _install_leader_signal_handlers: discoverable gating + add_signal_handler
  fallback to signal.signal when the loop refuses (Windows / non-main thread)
- _uninstall_leader_signal_handlers: remove + restore, both paths swallow errors
- _cancel_and_collect_tasks: cancels still-running, awaits all, swallows exceptions
- _run_leader_phases: first phase ends → log; second phase only when mcp ended
  + discoverable + watch alive
- _ensure_leader_or_inline: existing alive → return; spawn timeout → inline fallback;
  spawn ready → return spawned
- _bridge_to_leader: success path vs exception path both echo their outcome
- _respawn_if_leader_gone: leader healthy on recheck → no spawn; gone → spawn
"""

from __future__ import annotations

import asyncio
import signal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import process_reaper as _reaper
from octowright.cli import _leader_runtime as _lr
from octowright.cli import serve as _serve


def _patch_sn_daemon(
    monkeypatch: pytest.MonkeyPatch,
    *,
    read_lock: Any = None,
    is_stale: Any = None,
    probe_http_alive: Any = None,
    spawn_daemon: Any = None,
    wait_for_daemon: Any = None,
) -> None:
    """Patch attributes on the REAL singleton + daemonize modules.

    `from octowright import singleton as _sn` resolves through the package
    attribute, not sys.modules. setitem(sys.modules, ...) silently misses;
    setattr on the real module is what actually intercepts the call.
    """
    import octowright.daemonize as _daemonize_mod
    import octowright.singleton as _sn_mod

    if read_lock is not None:
        monkeypatch.setattr(_sn_mod, "read_lock", read_lock)
    if is_stale is not None:
        monkeypatch.setattr(_sn_mod, "is_stale", is_stale)
    if probe_http_alive is not None:
        monkeypatch.setattr(_sn_mod, "probe_http_alive", probe_http_alive)
    if spawn_daemon is not None:
        monkeypatch.setattr(_daemonize_mod, "spawn_daemon", spawn_daemon)
    if wait_for_daemon is not None:
        monkeypatch.setattr(_daemonize_mod, "wait_for_daemon", wait_for_daemon)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── _log_first_done ─────────────────────────────────────────────────────────


def _done_task(*, exception: BaseException | None = None, cancelled: bool = False) -> Any:
    """Build a fake task in a specific terminal state."""
    task = MagicMock()
    task.done.return_value = True
    task.cancelled.return_value = cancelled
    task.exception.return_value = exception
    return task


def _pending_task() -> Any:
    """Build a fake task that hasn't completed yet."""
    task = MagicMock()
    task.done.return_value = False
    return task


class TestLogFirstDone:
    def test_logs_ok_when_mcp_done_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Done + not cancelled + no exception → 'ok'."""
        captured: dict[str, Any] = {}

        def fake_info(event: str, **kwargs: Any) -> None:
            captured["event"] = event
            captured.update(kwargs)

        monkeypatch.setattr(_lr, "_log", MagicMock(info=fake_info))
        _lr._log_first_done("evt", _done_task(), None, [])
        assert captured["event"] == "evt"
        assert "mcp=ok" in captured["finished"]

    def test_logs_cancelled_when_mcp_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cancelled task → 'cancelled' tag, never 'error'."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done("evt", _done_task(cancelled=True), None, [])
        assert "mcp=cancelled" in captured["finished"]

    def test_logs_error_when_mcp_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Done with exception → 'error'."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done("evt", _done_task(exception=RuntimeError("boom")), None, [])
        assert "mcp=error" in captured["finished"]

    def test_pending_mcp_listed_in_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not-done mcp → goes into pending bucket, not finished."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done("evt", _pending_task(), None, [])
        assert "mcp" in captured["pending"]
        assert captured["finished"] == []

    def test_none_watch_task_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """watch_task=None → not included in either bucket."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done("evt", _done_task(), None, [])
        assert all("watchdog" not in entry for entry in captured["finished"])
        assert "watchdog" not in captured["pending"]

    def test_watch_task_done_labeled_watchdog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A done watch_task lands as 'watchdog=ok'."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done("evt", _pending_task(), _done_task(), [])
        assert "watchdog=ok" in captured["finished"]

    def test_sidecars_indexed_by_position(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sidecar labels include index 'sidecar[0]', 'sidecar[1]'."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done(
            "evt",
            _pending_task(),
            None,
            [_done_task(), _pending_task(), _done_task(cancelled=True)],
        )
        assert "sidecar[0]=ok" in captured["finished"]
        assert "sidecar[1]" in captured["pending"]
        assert "sidecar[2]=cancelled" in captured["finished"]

    def test_event_name_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whatever event string the caller passes is what gets logged."""
        captured: dict[str, Any] = {}
        monkeypatch.setattr(_lr, "_log", MagicMock(info=lambda event, **kw: captured.update({"e": event, **kw})))
        _lr._log_first_done("octowright.leader.first_phase_ended", _done_task(), None, [])
        assert captured["e"] == "octowright.leader.first_phase_ended"


# ─── _install_leader_signal_handlers ─────────────────────────────────────────


class TestInstallSignalHandlers:
    def test_not_discoverable_returns_empty(self) -> None:
        """discoverable=False → both lists empty, no signals touched."""
        loop = MagicMock()
        mcp_task = MagicMock()
        installed_signals, installed_handlers = _serve._install_leader_signal_handlers(
            loop, mcp_task, discoverable=False
        )
        assert installed_signals == []
        assert installed_handlers == []
        loop.add_signal_handler.assert_not_called()

    def test_discoverable_installs_sigterm(self) -> None:
        """discoverable=True calls loop.add_signal_handler(SIGTERM, ...)."""
        loop = MagicMock()
        mcp_task = MagicMock()
        installed_signals, _handlers = _serve._install_leader_signal_handlers(loop, mcp_task, discoverable=True)
        assert signal.SIGTERM in installed_signals
        # Verify add_signal_handler was called with SIGTERM
        signals_called = [c.args[0] for c in loop.add_signal_handler.call_args_list]
        assert signal.SIGTERM in signals_called

    @pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="SIGHUP not present on this platform")
    def test_discoverable_installs_sighup_when_available(self) -> None:
        """SIGHUP is conditionally installed only when the platform exposes it."""
        loop = MagicMock()
        mcp_task = MagicMock()
        installed_signals, _ = _serve._install_leader_signal_handlers(loop, mcp_task, discoverable=True)
        assert signal.SIGHUP in installed_signals

    def test_falls_back_to_signal_signal_on_notimplementederror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Loop without add_signal_handler → use signal.signal fallback."""
        loop = MagicMock()
        loop.add_signal_handler.side_effect = NotImplementedError
        mcp_task = MagicMock()

        previous = MagicMock()
        signal_signal_calls: list[Any] = []

        def fake_getsignal(sig: Any) -> Any:
            return previous

        def fake_signal(sig: Any, handler: Any) -> Any:
            signal_signal_calls.append((sig, handler))
            return previous

        monkeypatch.setattr(signal, "getsignal", fake_getsignal)
        monkeypatch.setattr(signal, "signal", fake_signal)

        installed_signals, installed_handlers = _serve._install_leader_signal_handlers(
            loop, mcp_task, discoverable=True
        )
        # add_signal_handler raised, so installed_signals stays empty,
        # installed_handlers is populated for fallback restoration.
        assert installed_signals == []
        assert len(installed_handlers) >= 1
        # Each entry is (sig, previous_handler).
        for _sig, prev in installed_handlers:
            assert prev is previous

    def test_falls_back_on_value_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ValueError from add_signal_handler also triggers fallback."""
        loop = MagicMock()
        loop.add_signal_handler.side_effect = ValueError("not main thread")
        mcp_task = MagicMock()
        monkeypatch.setattr(signal, "getsignal", lambda _sig: None)
        monkeypatch.setattr(signal, "signal", lambda _sig, _h: None)
        installed_signals, installed_handlers = _serve._install_leader_signal_handlers(
            loop, mcp_task, discoverable=True
        )
        assert installed_signals == []
        # Fallback was attempted for at least SIGTERM.
        assert len(installed_handlers) >= 1

    def test_swallows_fallback_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If signal.signal also raises, swallow silently — best-effort."""
        loop = MagicMock()
        loop.add_signal_handler.side_effect = NotImplementedError
        mcp_task = MagicMock()
        monkeypatch.setattr(signal, "getsignal", lambda _sig: None)

        def explode(_sig: Any, _h: Any) -> Any:
            raise OSError("nope")

        monkeypatch.setattr(signal, "signal", explode)
        # Must not raise.
        installed_signals, installed_handlers = _serve._install_leader_signal_handlers(
            loop, mcp_task, discoverable=True
        )
        assert installed_signals == []
        assert installed_handlers == []

    def test_add_signal_handler_failure_is_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """add_signal_handler raising NotImplementedError → log.warning emitted.

        Silent swallow would hide SIGTERM/SIGINT setup failures from
        operators on platforms where the loop refuses to register, so the
        daemon wouldn't shut down cleanly with no visible signal.
        """
        loop = MagicMock()
        loop.add_signal_handler.side_effect = NotImplementedError("loop refuses")
        mcp_task = MagicMock()
        # Make the signal.signal fallback succeed so we isolate the loop-path log.
        monkeypatch.setattr(signal, "getsignal", lambda _sig: None)
        monkeypatch.setattr(signal, "signal", lambda _sig, _h: None)

        warnings_captured: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            _serve,
            "_log",
            MagicMock(warning=lambda event, **kw: warnings_captured.append((event, kw))),
        )

        _serve._install_leader_signal_handlers(loop, mcp_task, discoverable=True)

        loop_failed = [
            (event, kw) for event, kw in warnings_captured if event == "octowright.serve.signal_handler_register_failed"
        ]
        assert loop_failed, f"expected loop-register warning, got: {warnings_captured!r}"
        # At least one warning should mention the NotImplementedError repr.
        assert any("NotImplementedError" in (kw.get("error") or "") for _e, kw in loop_failed)

    def test_signal_signal_fallback_failure_is_logged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """signal.signal fallback raising OSError → second log.warning emitted.

        Without this log line the fallback path also swallowed silently,
        leaving an operator with no signal install AND no error trail.
        """
        loop = MagicMock()
        loop.add_signal_handler.side_effect = NotImplementedError
        mcp_task = MagicMock()
        monkeypatch.setattr(signal, "getsignal", lambda _sig: None)

        def explode(_sig: Any, _h: Any) -> Any:
            raise OSError("nope")

        monkeypatch.setattr(signal, "signal", explode)

        warnings_captured: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            _serve,
            "_log",
            MagicMock(warning=lambda event, **kw: warnings_captured.append((event, kw))),
        )

        _serve._install_leader_signal_handlers(loop, mcp_task, discoverable=True)

        # Two warnings expected per signal we tried: the loop add failure and
        # the fallback signal.signal failure. At minimum we want the fallback
        # event name to appear so the swallow is no longer silent.
        events = [event for event, _kw in warnings_captured]
        assert "octowright.serve.signal_handler_fallback_failed" in events, (
            f"expected fallback-failed warning, got: {warnings_captured!r}"
        )


# ─── _uninstall_leader_signal_handlers ───────────────────────────────────────


class TestUninstallSignalHandlers:
    def test_removes_each_signal_from_loop(self) -> None:
        """Each entry in installed_signals → loop.remove_signal_handler call."""
        loop = MagicMock()
        sigs = [signal.SIGTERM]
        if hasattr(signal, "SIGHUP"):
            sigs.append(signal.SIGHUP)
        _serve._uninstall_leader_signal_handlers(loop, sigs, [])
        called_args = [c.args[0] for c in loop.remove_signal_handler.call_args_list]
        for s in sigs:
            assert s in called_args

    def test_swallows_remove_signal_handler_error(self) -> None:
        """remove_signal_handler raising NotImplementedError/ValueError → swallow."""
        loop = MagicMock()
        loop.remove_signal_handler.side_effect = NotImplementedError
        # Must not raise.
        _serve._uninstall_leader_signal_handlers(loop, [signal.SIGTERM], [])

    def test_restores_previous_signal_signal_handler(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Each (sig, previous) tuple → signal.signal(sig, previous)."""
        captured: list[Any] = []

        def fake_signal(sig: Any, handler: Any) -> Any:
            captured.append((sig, handler))
            return None

        monkeypatch.setattr(signal, "signal", fake_signal)
        prev_handler = MagicMock()
        _serve._uninstall_leader_signal_handlers(MagicMock(), [], [(signal.SIGTERM, prev_handler)])
        assert captured == [(signal.SIGTERM, prev_handler)]

    def test_swallows_signal_signal_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """signal.signal raising during restore → swallow."""

        def explode(_sig: Any, _h: Any) -> Any:
            raise OSError("nope")

        monkeypatch.setattr(signal, "signal", explode)
        # Must not raise.
        _serve._uninstall_leader_signal_handlers(MagicMock(), [], [(signal.SIGTERM, None)])


# ─── _cancel_and_collect_tasks ───────────────────────────────────────────────


class TestCancelAndCollectTasks:
    @pytest.mark.anyio
    async def test_cancels_still_running_tasks(self) -> None:
        """A task not yet done is cancelled."""

        async def hangs() -> None:
            await asyncio.sleep(60)

        task = asyncio.create_task(hangs())
        # Give it a tick to start.
        await asyncio.sleep(0)
        await _serve._cancel_and_collect_tasks([], None, task)
        assert task.cancelled() or task.done()

    @pytest.mark.anyio
    async def test_does_not_cancel_already_done(self) -> None:
        """Already-completed task is not cancel()-ed again."""

        async def quick() -> int:
            return 42

        task = asyncio.create_task(quick())
        await task  # ensure done
        # Sentinel: replace cancel with a tracker.
        task.cancel = MagicMock()  # type: ignore[method-assign]
        await _serve._cancel_and_collect_tasks([], None, task)
        task.cancel.assert_not_called()

    @pytest.mark.anyio
    async def test_swallows_task_exceptions(self) -> None:
        """A task that raises is awaited and the exception is swallowed."""

        async def boom() -> None:
            raise RuntimeError("explode")

        task = asyncio.create_task(boom())
        # Must not raise.
        await _serve._cancel_and_collect_tasks([], None, task)

    @pytest.mark.anyio
    async def test_swallows_already_failed_task_exception(self) -> None:
        """A task that already finished with a non-cancel exception is awaited
        and its exception swallowed (the generic except branch, distinct from
        the CancelledError path a not-yet-started task takes)."""

        async def boom() -> None:
            raise RuntimeError("explode")

        task = asyncio.create_task(boom())
        await asyncio.sleep(0.01)  # let it run to completion with the exception stored
        assert task.done() and task.exception() is not None
        # done → not cancelled; await re-raises RuntimeError → swallowed + logged.
        await _serve._cancel_and_collect_tasks([], None, task)

    @pytest.mark.anyio
    async def test_skips_none_watch_task(self) -> None:
        """watch_task=None doesn't crash — None entries are skipped."""

        async def noop() -> None:
            pass

        mcp = asyncio.create_task(noop())
        await mcp
        # Must not raise.
        await _serve._cancel_and_collect_tasks([], None, mcp)

    @pytest.mark.anyio
    async def test_iterates_all_sidecars(self) -> None:
        """Every sidecar gets cancel()-ed if running, then awaited."""

        async def hangs() -> None:
            await asyncio.sleep(60)

        s1 = asyncio.create_task(hangs())
        s2 = asyncio.create_task(hangs())
        await asyncio.sleep(0)

        async def quick() -> None:
            pass

        mcp = asyncio.create_task(quick())
        await mcp
        await _serve._cancel_and_collect_tasks([s1, s2], None, mcp)
        assert s1.cancelled() or s1.done()
        assert s2.cancelled() or s2.done()


# ─── _run_leader_phases ──────────────────────────────────────────────────────


class TestRunLeaderPhases:
    @pytest.mark.anyio
    async def test_first_phase_ends_when_mcp_completes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When mcp finishes first and is non-discoverable, second phase is skipped."""

        async def quick() -> None:
            pass

        mcp = asyncio.create_task(quick())
        # Wait for it to finish so wait() returns immediately.
        await asyncio.sleep(0)

        events_logged: list[str] = []
        monkeypatch.setattr(_lr, "_log_first_done", lambda evt, *a, **kw: events_logged.append(evt))
        await _lr._run_leader_phases({mcp}, mcp, None, [], discoverable=False)
        # Only the first-phase log line should fire.
        assert events_logged == ["octowright.leader.first_phase_ended"]

    @pytest.mark.anyio
    async def test_second_phase_runs_when_mcp_done_and_discoverable_and_watch_alive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """mcp done + discoverable + watch alive → second-phase wait + second log."""

        async def quick() -> None:
            pass

        mcp = asyncio.create_task(quick())
        await mcp  # mcp.done() is True

        # watch_task that finishes "later" — we actually let it complete to keep
        # the test quick.
        async def slow_watch() -> None:
            await asyncio.sleep(0.01)

        watch = asyncio.create_task(slow_watch())

        events_logged: list[str] = []
        monkeypatch.setattr(_lr, "_log_first_done", lambda evt, *a, **kw: events_logged.append(evt))

        await _lr._run_leader_phases({mcp, watch}, mcp, watch, [], discoverable=True)
        assert events_logged == [
            "octowright.leader.first_phase_ended",
            "octowright.leader.second_phase_ended",
        ]

    @pytest.mark.anyio
    async def test_no_second_phase_when_not_discoverable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """discoverable=False → no second-phase wait even if mcp ended cleanly."""

        async def quick() -> None:
            pass

        mcp = asyncio.create_task(quick())
        await mcp

        async def slow_watch() -> None:
            await asyncio.sleep(60)

        watch = asyncio.create_task(slow_watch())
        events_logged: list[str] = []
        monkeypatch.setattr(_lr, "_log_first_done", lambda evt, *a, **kw: events_logged.append(evt))
        await _lr._run_leader_phases({mcp, watch}, mcp, watch, [], discoverable=False)
        assert events_logged == ["octowright.leader.first_phase_ended"]
        # Cleanup the slow task.
        watch.cancel()

    @pytest.mark.anyio
    async def test_no_second_phase_when_watch_task_already_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the watchdog ended too, the second phase doesn't run (we're shutting down)."""

        async def quick() -> None:
            pass

        mcp = asyncio.create_task(quick())
        watch = asyncio.create_task(quick())
        await mcp
        await watch

        events_logged: list[str] = []
        monkeypatch.setattr(_lr, "_log_first_done", lambda evt, *a, **kw: events_logged.append(evt))
        await _lr._run_leader_phases({mcp, watch}, mcp, watch, [], discoverable=True)
        assert events_logged == ["octowright.leader.first_phase_ended"]


# ─── _ensure_leader_or_inline ────────────────────────────────────────────────


class TestEnsureLeaderOrInline:
    @pytest.mark.anyio
    async def test_existing_alive_leader_returned_directly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If lockfile exists + pid alive + http alive → return that LeaderInfo."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _info: False,
            probe_http_alive=AsyncMock(return_value=True),
            spawn_daemon=MagicMock(side_effect=AssertionError("should not be called")),
            wait_for_daemon=AsyncMock(),
        )
        result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)
        assert result is info

    @pytest.mark.anyio
    async def test_no_leader_spawns_daemon_and_returns_spawned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing lockfile → spawn daemon → wait_for_daemon returns info → return it."""
        spawned = SimpleNamespace(pid=99, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        spawn_daemon = MagicMock()
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: None,
            is_stale=lambda _i: False,
            probe_http_alive=AsyncMock(return_value=False),
            spawn_daemon=spawn_daemon,
            wait_for_daemon=AsyncMock(return_value=spawned),
        )
        result = await _serve._ensure_leader_or_inline({}, http_host="127.0.0.1", http_port=8765, idle_grace=None)
        assert result is spawned
        spawn_daemon.assert_called_once_with(http_host="127.0.0.1", http_port=8765, idle_grace=None, keep_alive=False)

    @pytest.mark.anyio
    async def test_spawn_timeout_falls_back_to_inline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """wait_for_daemon → None → call _run_leader inline and return None."""
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: None,
            is_stale=lambda _i: False,
            probe_http_alive=AsyncMock(return_value=False),
            spawn_daemon=MagicMock(),
            wait_for_daemon=AsyncMock(return_value=None),
        )

        run_leader_calls: list[dict[str, Any]] = []

        async def fake_run_leader(**kwargs: Any) -> None:
            run_leader_calls.append(kwargs)

        monkeypatch.setattr(_serve, "_run_leader", fake_run_leader)
        result = await _serve._ensure_leader_or_inline(
            {"http_host": None, "http_port": None, "no_http": False, "keep_alive": False, "idle_grace": None},
            http_host=None,
            http_port=None,
            idle_grace=None,
        )
        assert result is None
        assert len(run_leader_calls) == 1
        assert run_leader_calls[0]["no_singleton"] is False

    @pytest.mark.anyio
    async def test_stale_pid_triggers_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lock exists but is_stale=True → treat as no leader → spawn."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        spawned = SimpleNamespace(mcp_url="new")
        spawn_daemon = MagicMock()
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _info: True,
            probe_http_alive=AsyncMock(),
            spawn_daemon=spawn_daemon,
            wait_for_daemon=AsyncMock(return_value=spawned),
        )
        result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)
        assert result is spawned
        spawn_daemon.assert_called_once()

    @pytest.mark.anyio
    async def test_alive_pid_dead_http_triggers_spawn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lock alive but probe_http_alive=False → spawn replacement."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        spawned = SimpleNamespace(mcp_url="new")
        spawn_daemon = MagicMock()
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _info: False,
            probe_http_alive=AsyncMock(return_value=False),
            spawn_daemon=spawn_daemon,
            wait_for_daemon=AsyncMock(return_value=spawned),
        )
        result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)
        assert result is spawned
        spawn_daemon.assert_called_once()


# ─── _bridge_to_leader ───────────────────────────────────────────────────────


class TestBridgeToLeader:
    @pytest.mark.anyio
    async def test_clean_close_logs_and_returns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Successful follower run echoes 'leader bridge closed; checking daemon'."""

        async def fake_follower(_url: str) -> None:
            return None

        monkeypatch.setattr(_serve, "_run_follower", fake_follower)

        captured: list[str] = []

        def fake_echo(text: str, err: bool = False) -> None:
            captured.append(text)

        monkeypatch.setattr(_serve.click, "echo", fake_echo)
        await _serve._bridge_to_leader(SimpleNamespace(mcp_url="http://127.0.0.1:8765/mcp/"))
        assert any("bridge closed" in line for line in captured)
        assert not any("bridge ended" in line for line in captured)

    @pytest.mark.anyio
    async def test_exception_path_logs_error_and_swallows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Follower raise → echo 'leader bridge ended (exc)' but no re-raise."""

        async def fake_follower(_url: str) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(_serve, "_run_follower", fake_follower)
        captured: list[str] = []
        monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))
        # Must not raise.
        await _serve._bridge_to_leader(SimpleNamespace(mcp_url="http://127.0.0.1:8765/mcp/"))
        assert any("bridge ended" in line and "boom" in line for line in captured)


# ─── _respawn_if_leader_gone ─────────────────────────────────────────────────


class TestRespawnIfLeaderGone:
    @pytest.mark.anyio
    async def test_no_spawn_when_leader_still_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recheck shows alive leader → echo + return without spawn."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        spawn_daemon = MagicMock(side_effect=AssertionError("should not spawn"))
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _i: False,
            probe_http_alive=AsyncMock(return_value=True),
            spawn_daemon=spawn_daemon,
            wait_for_daemon=AsyncMock(),
        )
        captured: list[str] = []
        monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))
        await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)
        spawn_daemon.assert_not_called()
        assert any("still healthy" in line for line in captured)

    @pytest.mark.anyio
    async def test_spawns_when_leader_gone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lockfile gone or stale → spawn replacement daemon."""
        spawn_daemon = MagicMock()
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: None,
            is_stale=lambda _i: True,
            probe_http_alive=AsyncMock(return_value=False),
            spawn_daemon=spawn_daemon,
            wait_for_daemon=AsyncMock(),
        )
        captured: list[str] = []
        monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))
        await _serve._respawn_if_leader_gone(http_host="0.0.0.0", http_port=9000, idle_grace=300.0)
        spawn_daemon.assert_called_once_with(http_host="0.0.0.0", http_port=9000, idle_grace=300.0, keep_alive=False)
        assert any("spawning replacement" in line for line in captured)

    @pytest.mark.anyio
    async def test_spawns_when_pid_alive_but_http_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """is_stale=False but probe_http_alive=False → still considered gone."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        spawn_daemon = MagicMock()
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _i: False,
            probe_http_alive=AsyncMock(return_value=False),
            spawn_daemon=spawn_daemon,
            wait_for_daemon=AsyncMock(),
        )
        captured: list[str] = []
        monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))
        await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)
        spawn_daemon.assert_called_once()


# ─── Probe-then-recheck race-window correctness ─────────────────────────────
#
# The lock is released across probe_http_alive() so concurrent followers don't
# serialise on a 2-second HTTP call. The double-check under the lock prevents
# the race where two followers both observe "no live leader" outside the lock
# and both spawn daemons. We assert by tracking lock state during the probe.


class TestEnsureLeaderProbeOutsideLock:
    @pytest.mark.anyio
    async def test_live_leader_does_not_acquire_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: live leader → return immediately, election lock never entered."""
        import contextlib as _ctxlib

        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        lock_entered: list[bool] = []

        @_ctxlib.asynccontextmanager
        async def tracking_lock(*_a: Any, **_kw: Any) -> Any:
            lock_entered.append(True)
            yield

        import octowright.singleton as _sn_mod

        monkeypatch.setattr(_sn_mod, "async_election_lock", tracking_lock)
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _i: False,
            probe_http_alive=AsyncMock(return_value=True),
            spawn_daemon=MagicMock(side_effect=AssertionError("must not spawn")),
            wait_for_daemon=AsyncMock(),
        )
        result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)
        assert result is info
        # The lock was NEVER acquired — probe happened outside.
        assert lock_entered == []

    @pytest.mark.anyio
    async def test_probe_happens_before_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Order: probe → (no leader) → lock → recheck → spawn."""
        import contextlib as _ctxlib

        order: list[str] = []

        @_ctxlib.asynccontextmanager
        async def tracking_lock(*_a: Any, **_kw: Any) -> Any:
            order.append("lock-enter")
            yield
            order.append("lock-exit")

        import octowright.singleton as _sn_mod

        monkeypatch.setattr(_sn_mod, "async_election_lock", tracking_lock)

        def fake_read_lock() -> Any:
            order.append("read")
            return None

        async def fake_probe(_info: Any, timeout: float = 2.0) -> bool:
            order.append("probe")
            return False

        spawn = MagicMock(side_effect=lambda **_kw: order.append("spawn"))
        _patch_sn_daemon(
            monkeypatch,
            read_lock=fake_read_lock,
            is_stale=lambda _i: False,
            probe_http_alive=fake_probe,
            spawn_daemon=spawn,
            wait_for_daemon=AsyncMock(return_value=SimpleNamespace(mcp_url="x")),
        )
        await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)
        # First read happens BEFORE the lock is taken — confirming probe-outside-lock.
        assert order[0] == "read"
        assert order.index("lock-enter") > 0
        # Spawn happens inside the lock, before exit.
        assert order.index("spawn") < order.index("lock-exit")

    @pytest.mark.anyio
    async def test_race_window_recheck_finds_now_live_leader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outside probe: no leader. Inside lock: another process spawned one.

        This is exactly the race the recheck-under-lock pattern exists to fix.
        Without the recheck, both followers would spawn duplicate daemons.
        """
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        # First read (outside lock): no leader. Second read (under lock): a leader exists.
        reads = [None, info]

        def fake_read_lock() -> Any:
            return reads.pop(0)

        # Probe: first call (outside lock) only fires when read returned non-None,
        # so it's the *recheck* call that gets True. We track all calls.
        probe_calls: list[Any] = []

        async def fake_probe(probed_info: Any, timeout: float = 2.0) -> bool:
            probe_calls.append(probed_info)
            return probed_info is info  # only the recheck (info) returns alive

        spawn = MagicMock(side_effect=AssertionError("must not spawn — recheck saw live leader"))
        _patch_sn_daemon(
            monkeypatch,
            read_lock=fake_read_lock,
            is_stale=lambda _i: False,
            probe_http_alive=fake_probe,
            spawn_daemon=spawn,
            wait_for_daemon=AsyncMock(),
        )
        result = await _serve._ensure_leader_or_inline({}, http_host=None, http_port=None, idle_grace=None)
        assert result is info
        spawn.assert_not_called()


class TestRespawnProbeOutsideLock:
    @pytest.mark.anyio
    async def test_live_leader_does_not_acquire_lock(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outside probe alive → return immediately, never acquire the lock."""
        import contextlib as _ctxlib

        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        lock_entered: list[bool] = []

        @_ctxlib.asynccontextmanager
        async def tracking_lock(*_a: Any, **_kw: Any) -> Any:
            lock_entered.append(True)
            yield

        import octowright.singleton as _sn_mod

        monkeypatch.setattr(_sn_mod, "async_election_lock", tracking_lock)
        _patch_sn_daemon(
            monkeypatch,
            read_lock=lambda: info,
            is_stale=lambda _i: False,
            probe_http_alive=AsyncMock(return_value=True),
            spawn_daemon=MagicMock(side_effect=AssertionError("must not spawn")),
            wait_for_daemon=AsyncMock(),
        )
        captured: list[str] = []
        monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))
        await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)
        assert lock_entered == []
        assert any("still healthy" in line for line in captured)

    @pytest.mark.anyio
    async def test_race_window_recheck_finds_now_live_leader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Outside: no leader. Under lock: someone else already respawned one."""
        info = SimpleNamespace(pid=42, http_host="127.0.0.1", http_port=8765, mcp_url="http://127.0.0.1:8765/mcp/")
        reads = [None, info]

        def fake_read_lock() -> Any:
            return reads.pop(0)

        async def fake_probe(probed_info: Any, timeout: float = 2.0) -> bool:
            return probed_info is info

        spawn = MagicMock(side_effect=AssertionError("must not spawn — recheck saw live leader"))
        _patch_sn_daemon(
            monkeypatch,
            read_lock=fake_read_lock,
            is_stale=lambda _i: False,
            probe_http_alive=fake_probe,
            spawn_daemon=spawn,
            wait_for_daemon=AsyncMock(),
        )
        captured: list[str] = []
        monkeypatch.setattr(_serve.click, "echo", lambda text, err=False: captured.append(text))
        await _serve._respawn_if_leader_gone(http_host=None, http_port=None, idle_grace=None)
        spawn.assert_not_called()
        assert any("still healthy" in line for line in captured)


# ─── _shutdown_browser_pool_on_shutdown ──────────────────────────────────────


class TestShutdownBrowserPoolOnShutdown:
    """Leader shutdown reaped browser PROCESSES but never tore the pool down, so
    the shared Playwright driver (a node process) kept running and every
    session tmpdir stayed on disk after the daemon exited.

    Lives in process_reaper (next to the browser-process reaper it complements)
    because cli/serve.py is at its 550-LOC ceiling."""

    @pytest.mark.anyio
    async def test_shuts_the_pool_down(self) -> None:
        pool = MagicMock()
        pool.shutdown = AsyncMock()
        await _reaper.shutdown_browser_pool_on_shutdown(pool, log=MagicMock())
        pool.shutdown.assert_awaited_once_with()

    @pytest.mark.anyio
    async def test_none_pool_is_noop(self) -> None:
        await _reaper.shutdown_browser_pool_on_shutdown(None, log=MagicMock())

    @pytest.mark.anyio
    async def test_swallows_shutdown_error(self) -> None:
        # Best-effort: the browsers were already reaped, so a failure here must
        # not block daemon exit (or strand the lockfile removal that follows).
        pool = MagicMock()
        pool.shutdown = AsyncMock(side_effect=RuntimeError("driver already gone"))
        log = MagicMock()
        await _reaper.shutdown_browser_pool_on_shutdown(pool, log=log)  # must not raise
        log.debug.assert_called_once()


# ─── _close_terminal_pool_on_shutdown ────────────────────────────────────────


class TestCloseTerminalPoolOnShutdown:
    """Leader shutdown reaps browsers but must also close the optional terminal
    pool — otherwise PTY/SSH terminal sessions survive the daemon exit."""

    @pytest.mark.anyio
    async def test_none_pool_is_noop(self) -> None:
        await _serve._close_terminal_pool_on_shutdown(None, log=MagicMock())

    @pytest.mark.anyio
    async def test_closes_present_pool_forced(self) -> None:
        tpool = MagicMock()
        tpool.close_all = AsyncMock()
        await _serve._close_terminal_pool_on_shutdown(tpool, log=MagicMock())
        tpool.close_all.assert_awaited_once_with(force=True)

    @pytest.mark.anyio
    async def test_swallows_close_error(self) -> None:
        tpool = MagicMock()
        tpool.close_all = AsyncMock(side_effect=RuntimeError("boom"))
        log = MagicMock()
        await _serve._close_terminal_pool_on_shutdown(tpool, log=log)  # must not raise
        log.debug.assert_called_once()
