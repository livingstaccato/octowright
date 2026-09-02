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
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from provide.telemetry import get_logger

from octowright.plugins.contract import CloseResult, LaunchResult
from octowright.plugins.session_launch import PluginContext
from octowright_terminal.engine import TerminalEngine
from octowright_terminal.errors import ProtectedTerminalCloseError
from octowright_terminal.session import TerminalSession

log = get_logger(__name__)


class TerminalPool:
    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._sessions: dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()

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
                on_stopped=lambda: self._evict_stopped(instance_id),
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
            self._sessions[instance_id] = session
        # A connector can die inside start() -- before the line above -- so the
        # stop notification would have fired with nothing yet to evict. Re-check
        # after registering and evict here instead. Ordering makes this safe in
        # both directions: if the death happens after registration the callback
        # does the eviction, and `_evict_stopped` is idempotent, so a race that
        # runs both is harmless.
        if engine.stopped:
            self._evict_stopped(instance_id)
        return result

    def _evict_stopped(self, instance_id: str) -> None:
        """Drop a terminal whose connector has ended from the registry.

        Sync and lock-free on purpose: this is called from an asyncio
        done-callback (``engine._on_poll_done`` -> ``_record_stop``), which
        cannot await. It mirrors core's ``_accept_external_close_nowait`` seam
        -- the pool's ``asyncio.Lock`` serializes launch/close sequences rather
        than protecting dict atomicity, and a single ``pop`` is atomic under the
        GIL, so taking the lock would buy nothing and could not be done from
        here anyway.

        Identity is checked rather than assumed: ``pop`` alone would let a
        late callback for a dead terminal evict a live session that reused the
        id. Idempotent, so the launch-race path and the callback path can both
        run.

        Without this a terminal whose connector died stayed in ``_sessions``
        and kept appearing in ``list_sessions`` (and so in the dashboard and
        ``terminal_list``) as though it were live, until somebody closed it by
        hand.
        """
        session = self._sessions.get(instance_id)
        if session is None:
            return
        if self._sessions.pop(instance_id, None) is not session:
            return
        log.info(
            "terminal.evicted_on_stop",
            instance_id=instance_id,
            connector_type=session.connector_type,
        )

    def get(self, instance_id: str) -> TerminalSession:
        if instance_id not in self._sessions:
            raise KeyError(f"no terminal session {instance_id!r}")
        return self._sessions[instance_id]

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
            raise KeyError(f"no terminal session {instance_id!r}")
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
        if failures:
            detail = "; ".join(f"{instance_id}: {type(exc).__name__}: {exc}" for instance_id, exc in failures)
            raise RuntimeError(f"failed to close {len(failures)} terminal session(s): {detail}") from failures[0][1]
