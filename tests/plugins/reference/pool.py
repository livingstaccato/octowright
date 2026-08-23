# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from octowright.plugins.contract import CloseResult, LaunchResult, SessionPool
from octowright.plugins.errors import ProtectedSessionCloseError
from octowright.plugins.session_launch import PluginContext
from octowright.recorder import Recorder

KIND = "refkind"


@dataclass
class ReferenceSession:
    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    url: str | None
    recorder: Recorder
    log_path: Path
    protected: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class ReferencePool:
    """A pool with no external dependency — it records and nothing else."""

    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._sessions: dict[str, ReferenceSession] = {}

    async def launch(
        self,
        *,
        label: str | None = None,
        profile: str | None = None,
        protected: bool = False,
        fail: bool = False,
        **_: Any,
    ) -> LaunchResult:
        instance_id = uuid4().hex[:12]
        async with self._ctx.begin_session(instance_id=instance_id, label=label, profile=profile) as launch:
            if fail:
                # Exercised by the failed-launch test: nothing recorded, so the
                # opening-row-only recording must be discarded.
                raise RuntimeError("reference launch asked to fail")
            launch.recorder.record("ref_ready", note="reference session up")
            session = ReferenceSession(
                instance_id=instance_id,
                kind=KIND,
                label=label,
                profile=profile,
                url=None,
                recorder=launch.recorder,
                log_path=launch.log_path,
                protected=protected,
            )
            result = launch.commit(session)
        self._sessions[instance_id] = session
        return result

    def get(self, instance_id: str) -> ReferenceSession:
        if instance_id not in self._sessions:
            raise KeyError(f"no refkind session {instance_id!r}")
        return self._sessions[instance_id]

    def write_transcript(self, instance_id: str, body: str) -> str:
        """Write and commit a Tier-2 transcript artifact.

        The reference plugin's whole job is to exercise a seam from a
        consumer's side, so this deliberately uses the ordinary reserve →
        write → commit sequence rather than a shortcut: it never composes a
        path, and the artifact is invisible until commit.
        """
        session = self.get(instance_id)
        handle = self._ctx.artifact(session, "transcript", ".txt")
        handle.path.write_text(body, encoding="utf-8")
        handle.commit(mime_type="text/plain")
        return handle.artifact_id

    def maybe_get(self, instance_id: str) -> ReferenceSession | None:
        return self._sessions.get(instance_id)

    def iter_sessions(self) -> Iterator[ReferenceSession]:
        return iter(list(self._sessions.values()))

    async def close(self, instance_id: str, *, force: bool = False) -> CloseResult:
        session = self.maybe_get(instance_id)
        if session is None:
            raise KeyError(f"no refkind session {instance_id!r}")
        if session.protected and not force:
            raise ProtectedSessionCloseError(f"refkind {instance_id!r} is protected; pass force=True to close it")
        session.recorder.close()
        del self._sessions[instance_id]
        return CloseResult(instance_id=instance_id, kind=KIND, closed=True)

    async def close_all(self, *, force: bool = False) -> None:
        failures: list[tuple[str, Exception]] = []
        for instance_id in list(self._sessions):
            try:
                await self.close(instance_id, force=force)
            except Exception as exc:  # continue past one failure
                failures.append((instance_id, exc))
        if failures:
            raise ExceptionGroup("refkind close_all had failures", [exc for _, exc in failures])


def _assert_structural_conformance(pool: ReferencePool) -> SessionPool:
    """Static pin: ``ReferencePool`` must satisfy the ``SessionPool`` Protocol.

    A type-checker rejects this return if a method's *signature* drifts, which
    the name-based coverage test in ``test_reference_plugin.py`` cannot see —
    and ``activate`` types the pool as ``Any``, so nothing else in the tree
    checks it. Never called; it exists to be type-checked.
    """
    return pool
