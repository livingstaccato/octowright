# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TerminalPool: lifecycle + registry for terminal sessions.

Mirrors BrowserPool's surface (launch/get/maybe_get/iter_sessions/list_sessions/
close/close_all) so the dashboard and scenario layers treat terminal and browser
sessions uniformly. Conforms to ``octowright.plugins.contract.SessionPool``:
``launch`` opens a core-owned launch transaction (``ctx.begin_session``) rather
than building its own ``Recorder``, so the 0600/0700 recording guarantees, the
byte ceiling, and cross-pool instance-id uniqueness are structural rather than
an obligation this pool has to remember.
"""

from __future__ import annotations

import asyncio
import weakref
from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

from provide.telemetry import get_logger

from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.plugins.contract import CloseResult, LaunchResult
from octowright.plugins.session_launch import PluginContext
from octowright_terminal.engine import TerminalEngine
from octowright_terminal.errors import ProtectedTerminalCloseError
from octowright_terminal.session import TerminalSession

log = get_logger(__name__)

#: How many evicted terminals to remember the cause of. Bounded because the
#: only consumer is the lookup error for an id a caller still holds, and an
#: agent's handle goes stale within a few tool calls -- this is a diagnostic
#: courtesy, not a history.
_EVICTED_LEDGER_MAX = 64


class TerminalPool:
    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        # Teardowns scheduled by `_evict_stopped`, held so the event loop does
        # not garbage-collect a task nobody awaits, and so `drain_evictions`
        # has something to wait on.
        self._evict_tasks: set[asyncio.Task[None]] = set()
        self._evicted: OrderedDict[str, tuple[str, str]] = OrderedDict()

    async def launch(
        self,
        *,
        kind: str = "pty",
        connector_config: dict[str, Any],
        label: str | None = None,
        profile: str | None = None,
        protected: bool = False,
    ) -> LaunchResult:
        instance_id = uuid4().hex[:12]
        # Failures (the SSH connector rejecting a missing known_hosts in its
        # ctor, or connector.start() failing) surface inside the transaction,
        # so ctx.begin_session discards the opening-row-only recording on our
        # behalf -- this pool no longer owns that rollback.
        async with self._ctx.begin_session(instance_id=instance_id, label=label, profile=profile) as launch:
            engine = TerminalEngine(
                launch.instance_id,
                label,
                kind,
                connector_config,
                launch.recorder,
                on_stopped=self._stop_notifier(launch.instance_id),
            )
            session = TerminalSession(
                instance_id=launch.instance_id,
                kind=launch.kind,
                connector_type=kind,
                label=label,
                profile=profile,
                recorder=launch.recorder,
                log_path=launch.log_path,
                engine=engine,
                protected=protected,
                # Mirrored into the record's free-form map so core's launch
                # transaction carries it out in LaunchResult["extra"] -- the
                # only route a plugin has to add a field to a result core
                # builds. `terminal_launch` flattens it back to the top level.
                extra={"connector_type": kind},
            )
            await engine.start()
            try:
                result = launch.commit(session)
            except BaseException:
                # engine.start() has forked the PTY / opened the SSH connection
                # and started a poll task; commit() can still refuse (a
                # duplicate instance_id) or be cancelled. The transaction
                # discards the recording, but it has no handle on this
                # connector and the session is never registered anywhere that
                # could close it -- so without this the process keeps a live
                # child and a running task for a launch that failed. The
                # transaction cannot own this; the pool that started it must.
                await engine.stop()
                raise
        async with self._lock:
            self._sessions[session.instance_id] = session
        # A connector can die inside start() -- before the line above -- so the
        # stop notification would have fired with nothing yet to evict. Re-check
        # after registering and evict here instead. Ordering makes this safe in
        # both directions: if the death happens after registration the callback
        # does the eviction, and `_evict_stopped` is idempotent, so a race that
        # runs both is harmless.
        if engine.stopped:
            self._evict_stopped(session.instance_id, engine, "eof")
        return result

    def _stop_notifier(self, instance_id: str) -> Callable[[TerminalEngine, str], None]:
        """Build the engine's stop callback without giving the engine the pool.

        The engine outlives this call only as long as the pool holds it, but the
        callback is stored ON the engine, so a strong ``self`` here would make
        pool -> _sessions -> session -> engine -> callback -> pool a cycle that
        only the collector can break. A weak reference keeps the ownership
        one-directional; a notification arriving after the pool is gone has
        nothing to evict and is correctly a no-op.
        """
        poolref = weakref.ref(self)

        def _notify(engine: TerminalEngine, reason: str) -> None:
            pool = poolref()
            if pool is not None:
                pool._evict_stopped(instance_id, engine, reason)

        return _notify

    def _evict_stopped(self, instance_id: str, engine: TerminalEngine, reason: str) -> None:
        """Drop and tear down a terminal whose connector has ended.

        Sync and lock-free on purpose: this is called from an asyncio
        done-callback (``engine._on_poll_done`` -> ``_record_stop``), which
        cannot await. It mirrors core's ``_accept_external_close_nowait`` seam
        -- the pool's ``asyncio.Lock`` serializes launch/close sequences rather
        than protecting dict atomicity, and a single ``pop`` is atomic under the
        GIL, so taking the lock would buy nothing and could not be done from
        here anyway.

        ``reason`` separates the two ways a terminal ends. ``pool.close`` awaits
        ``session.close()`` BEFORE popping the registry entry, so a deliberate
        close reaches this callback with the session still registered; without
        the reason every ordinary close logged an eviction and would now be torn
        down twice. A ``"closed"`` stop therefore returns immediately and lets
        ``close``/``close_all`` finish their own sequence.

        Identity is checked against the engine the callback belongs to, not
        against a value re-read from the same dict: ``session = get(id)`` then
        ``pop(id) is not session`` has no await between the two reads, so it
        compares a value with itself and can never fail -- the stale-callback
        case it was written for went undetected. The check also runs BEFORE the
        removal, so a rejected notification cannot leave a hole it then has to
        put back.

        Teardown is SCHEDULED rather than merely dropped. Removing the entry
        discards the only reference to the session (core keeps no parallel
        table), so an eviction that does not close leaks the connector's
        transport or PTY child and the recorder's file handle, and
        ``close_all`` can no longer reach it at shutdown -- a worse bug than the
        stale ``list_sessions`` entry this seam exists to fix.
        """
        if reason == "closed":
            return
        session = self._sessions.get(instance_id)
        if session is None or session.engine is not engine:
            return
        del self._sessions[instance_id]
        self._remember_eviction(instance_id, session.connector_type, reason)
        log.warning(
            "terminal.evicted_on_stop",
            instance_id=instance_id,
            connector_type=session.connector_type,
            reason=reason,
        )
        publish_dashboard_invalidation_nowait("sessions")
        self._schedule_teardown(session)

    def _remember_eviction(self, instance_id: str, connector_type: str, reason: str) -> None:
        self._evicted[instance_id] = (connector_type, reason)
        while len(self._evicted) > _EVICTED_LEDGER_MAX:
            self._evicted.popitem(last=False)

    def _schedule_teardown(self, session: TerminalSession) -> None:
        try:
            task = asyncio.create_task(self._teardown_evicted(session))
        except RuntimeError:
            # No running loop: nothing can be awaited from here, and the caller
            # is already off the normal path (a pool driven outside an event
            # loop). Say so rather than dropping the session silently.
            log.warning("terminal.evicted_teardown.unscheduled", instance_id=session.instance_id)
            return
        self._evict_tasks.add(task)
        task.add_done_callback(self._evict_tasks.discard)

    async def _teardown_evicted(self, session: TerminalSession) -> None:
        try:
            await session.close()
        except Exception as exc:
            # The connector is already dead; this close is best-effort cleanup
            # of whatever it still held. Failing loudly here would only surface
            # as an unretrievable task exception.
            log.warning(
                "terminal.evicted_teardown.failed",
                instance_id=session.instance_id,
                error=repr(exc),
            )

    async def drain_evictions(self) -> None:
        """Await teardowns that ``_evict_stopped`` scheduled.

        ``close_all`` calls it so shutdown does not race a connector that died
        moments earlier; tests call it so they can assert on teardown without
        sleeping for a guessed interval.
        """
        while self._evict_tasks:
            await asyncio.gather(*tuple(self._evict_tasks), return_exceptions=True)

    def get(self, instance_id: str) -> TerminalSession:
        session = self._sessions.get(instance_id)
        if session is None:
            raise KeyError(self._missing_session_message(instance_id))
        return session

    def _missing_session_message(self, instance_id: str) -> str:
        """Explain a lookup miss, naming the connector death when we saw one.

        Once a dead terminal is evicted every ``terminal_*`` tool fails here, so
        this is the last place that can say WHY. Without it an agent is told
        "no terminal session 'abc'" -- the same answer it gets for an id that
        never existed -- about a terminal it just watched work, and
        ``send_input``'s "input was NOT delivered" guard is unreachable because
        the session is gone before the engine can raise it.
        """
        evicted = self._evicted.get(instance_id)
        if evicted is None:
            return f"no terminal session {instance_id!r}"
        connector_type, reason = evicted
        return (
            f"terminal session {instance_id!r} is gone: its {connector_type} connector "
            f"ended ({reason}) and the session was evicted. Launch a new terminal."
        )

    def maybe_get(self, instance_id: str) -> TerminalSession | None:
        return self._sessions.get(instance_id)

    def iter_sessions(self) -> Iterator[TerminalSession]:
        return iter(tuple(self._sessions.values()))

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "instance_id": s.instance_id,
                "kind": s.kind,
                "connector_type": s.connector_type,
                "label": s.label,
                "profile": s.profile,
                "url": s.url,
                "log_path": str(s.log_path),
                "har_path": None,
                "protected": s.protected,
            }
            for s in tuple(self._sessions.values())
        ]

    async def close(self, instance_id: str, *, force: bool = False) -> CloseResult:
        session = self.maybe_get(instance_id)
        if session is None:
            raise KeyError(self._missing_session_message(instance_id))
        if session.protected and not force:
            raise ProtectedTerminalCloseError(f"terminal {instance_id!r} is protected; pass force=True to close it")
        await session.close()
        async with self._lock:
            self._sessions.pop(instance_id, None)
        return CloseResult(instance_id=instance_id, kind="terminal", closed=True)

    async def close_all(self, *, force: bool = False) -> None:
        failures: list[tuple[str, Exception]] = []
        for instance_id in list(self._sessions):
            session = self._sessions.get(instance_id)
            if session is None:
                continue
            if session.protected and not force:
                continue
            try:
                await session.close()
            except Exception as exc:
                failures.append((instance_id, exc))
                continue
            async with self._lock:
                self._sessions.pop(instance_id, None)
        # A connector that died moments before shutdown has its teardown in
        # flight rather than in `_sessions`; without this, close_all returns
        # while that child/transport is still being released.
        await self.drain_evictions()
        if failures:
            detail = "; ".join(f"{instance_id}: {type(exc).__name__}: {exc}" for instance_id, exc in failures)
            raise RuntimeError(f"failed to close {len(failures)} terminal session(s): {detail}") from failures[0][1]
