# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TerminalPool: lifecycle + registry for terminal sessions.

Mirrors BrowserPool's surface (launch/get/maybe_get/iter_sessions/list_sessions/
close/close_all) so the dashboard and scenario layers treat terminal and browser
sessions uniformly.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from octowright import defaults
from octowright.recorder import Recorder, new_log_path
from octowright.terminal.engine import TerminalEngine
from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.session import TerminalSession


class TerminalPool:
    def __init__(self) -> None:
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
    ) -> dict[str, Any]:
        instance_id = uuid4().hex[:12]
        # kind in the FILENAME is always "terminal" so closed-session discovery
        # (which keys on the kind segment) groups terminals together.
        log_path = new_log_path(defaults.RECORDINGS_DIR, instance_id, label, "terminal")
        recorder = Recorder(log_path)
        engine = TerminalEngine(instance_id, label, kind, connector_config, recorder)
        session = TerminalSession(
            instance_id=instance_id,
            kind="terminal",
            connector_type=kind,
            label=label,
            profile=profile,
            recorder=recorder,
            log_path=log_path,
            engine=engine,
            protected=protected,
        )
        await engine.start()
        async with self._lock:
            self._sessions[instance_id] = session
        return {
            "instance_id": instance_id,
            "kind": "terminal",
            "connector_type": kind,
            "label": label,
            "profile": profile,
            "log_path": str(log_path),
        }

    def get(self, instance_id: str) -> TerminalSession:
        if instance_id not in self._sessions:
            raise KeyError(f"no terminal session {instance_id!r}")
        return self._sessions[instance_id]

    def maybe_get(self, instance_id: str) -> TerminalSession | None:
        return self._sessions.get(instance_id)

    def iter_sessions(self) -> tuple[TerminalSession, ...]:
        return tuple(self._sessions.values())

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

    async def close(self, instance_id: str, *, force: bool = False) -> None:
        session = self.maybe_get(instance_id)
        if session is None:
            raise KeyError(f"no terminal session {instance_id!r}")
        if session.protected and not force:
            raise ProtectedTerminalCloseError(f"terminal {instance_id!r} is protected; pass force=True to close it")
        await session.close()
        async with self._lock:
            self._sessions.pop(instance_id, None)

    async def close_all(self, *, force: bool = False) -> None:
        for instance_id in list(self._sessions):
            session = self._sessions.get(instance_id)
            if session is None:
                continue
            if session.protected and not force:
                continue
            await session.close()
            async with self._lock:
                self._sessions.pop(instance_id, None)
