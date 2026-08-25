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

from octowright.plugins.contract import CloseResult, LaunchResult
from octowright.plugins.session_launch import PluginContext
from octowright_terminal.engine import TerminalEngine
from octowright_terminal.errors import ProtectedTerminalCloseError
from octowright_terminal.session import TerminalSession


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
            engine = TerminalEngine(launch.instance_id, label, kind, connector_config, launch.recorder)
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
            result = launch.commit(session)
        async with self._lock:
            self._sessions[instance_id] = session
        return result

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
