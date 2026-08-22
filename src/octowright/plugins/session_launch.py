# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Core-owned launch transaction.

A plugin cannot open a recording. It asks for a transaction, records into the
recorder the transaction hands it, and commits. That is what makes the disk
guarantees — 0600 under a 0700 parent, containment, the byte ceiling, the
failed-launch rule — structural rather than a documented obligation on the
plugin author.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.contract import LaunchResult, SessionRecord
from octowright.plugins.errors import SessionIdInUseError
from octowright.recorder import Recorder, new_log_path

_LOG = get_logger("octowright.plugins.launch")

#: A recording holding nothing but this is an orphan of a failed launch and is
#: deleted. Anything the plugin recorded — even one row — is kept: a real if
#: orphaned recording beats destroying diagnostic data. Content, not size, is
#: the test, because core writes ``session_start`` before the plugin runs, so
#: the file is never zero bytes by the time a launch can fail.
_OPENING_ONLY: frozenset[str] = frozenset({"session_start"})


@dataclass
class SessionLaunch:
    """One in-flight launch. Yielded by :meth:`PluginContext.begin_session`."""

    recorder: Recorder
    log_path: Path
    instance_id: str
    kind: str
    _id_in_use: Callable[[str], bool]
    _committed: bool = False
    _result: LaunchResult | None = None

    def commit(self, record: SessionRecord) -> LaunchResult:
        """Validate and finalize. The plugin's own pool holds ``record``.

        Core keeps no parallel session table, so this does not register the
        record anywhere — it checks that the record is the one this
        transaction issued, enforces cross-pool id uniqueness, and marks the
        transaction successful.
        """
        if (
            record.instance_id != self.instance_id
            or record.kind != self.kind
            or record.recorder is not self.recorder
            or Path(record.log_path) != self.log_path
        ):
            raise ValueError(
                f"committed record for {record.instance_id!r} does not match the transaction "
                f"({self.instance_id!r}/{self.kind!r})"
            )
        if self._id_in_use(self.instance_id):
            raise SessionIdInUseError(f"instance_id {self.instance_id!r} is already held by another registered pool")
        self._committed = True
        self._result = LaunchResult(
            instance_id=self.instance_id,
            kind=self.kind,
            label=record.label,
            profile=record.profile,
            log_path=str(self.log_path),
        )
        return self._result


@dataclass
class PluginContext:
    """What a plugin is handed at ``create_pool``."""

    kind: str
    recordings_dir: Path
    id_in_use: Callable[[str], bool]
    log: Any = field(default_factory=lambda: _LOG)

    def redaction_mode(self) -> str:
        """The resolved ``OCTOWRIGHT_REDACT_INPUTS`` policy.

        Plugins are handed the resolved policy and never read the environment
        themselves — the same reasoning as ``redact_headers_for_report``
        flooring at ``passwords`` rather than trusting a caller.
        """
        raw = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "").strip().lower()
        return raw if raw in {"off", "passwords", "all"} else "passwords"

    @contextlib.asynccontextmanager
    async def begin_session(
        self,
        *,
        instance_id: str,
        label: str | None,
        profile: str | None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[SessionLaunch]:
        """Open a recording, write the opening row, and guard the launch.

        Takes no ``kind``: the context already holds the validated descriptor
        kind, so accepting one would let a plugin stamp a recording with a
        kind core never approved.
        """
        log_path = new_log_path(self.recordings_dir, instance_id, label, self.kind)
        recorder = Recorder(log_path)
        recorder.record_control(
            "session_start",
            kind=self.kind,
            label=label,
            profile=profile,
            **(extra or {}),
        )
        launch = SessionLaunch(
            recorder=recorder,
            log_path=log_path,
            instance_id=instance_id,
            kind=self.kind,
            _id_in_use=self.id_in_use,
        )
        try:
            yield launch
        except BaseException:
            _discard_failed_launch(recorder, log_path)
            raise
        if not launch._committed:
            # A block that returns without committing did not produce a
            # session; treat it exactly as a raised launch.
            _discard_failed_launch(recorder, log_path)


def _discard_failed_launch(recorder: Recorder, log_path: Path) -> None:
    """Close the recorder and drop the file if it holds only core's opening row."""
    recorder.close()
    with contextlib.suppress(OSError):
        if not log_path.exists():
            return
        actions = set()
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                actions.add(json.loads(raw).get("action"))
            except json.JSONDecodeError:
                # An unparsable line is data we did not write; keep the file.
                return
        if actions and actions <= _OPENING_ONLY:
            log_path.unlink()
