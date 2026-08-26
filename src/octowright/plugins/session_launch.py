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
from typing import Any, Protocol

from provide.telemetry import get_logger

from octowright._paths import reject_unsafe_path
from octowright.plugins.artifacts import ArtifactHandle, reserve_artifact
from octowright.plugins.contract import LaunchResult, SessionRecord
from octowright.plugins.errors import SessionIdInUseError
from octowright.plugins.identity import validate_instance_id
from octowright.recorder import Recorder, new_log_path

_LOG = get_logger("octowright.plugins.launch")

#: A recording holding nothing but this is an orphan of a failed launch and is
#: deleted. Anything the plugin recorded — even one row — is kept: a real if
#: orphaned recording beats destroying diagnostic data. Content, not size, is
#: the test, because core writes ``session_start`` before the plugin runs, so
#: the file is never zero bytes by the time a launch can fail.
_OPENING_ONLY: frozenset[str] = frozenset({"session_start"})


class IdInUse(Protocol):
    """Probe for an ``instance_id`` already held by a registered pool.

    ``exclude_kind`` skips one kind's own pool. Core enforces cross-pool id
    uniqueness at commit, and the launching plugin's pool is not "another"
    pool: a plugin that registers its session before committing — the natural
    order — would otherwise be refused by its own bookkeeping.
    """

    def __call__(self, instance_id: str, *, exclude_kind: str | None = None) -> bool: ...


@dataclass
class SessionLaunch:
    """One in-flight launch. Yielded by :meth:`PluginContext.begin_session`."""

    recorder: Recorder
    log_path: Path
    instance_id: str
    kind: str
    _id_in_use: Callable[[str], bool]
    _committed: bool = False

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
        result = LaunchResult(
            instance_id=self.instance_id,
            kind=self.kind,
            label=record.label,
            profile=record.profile,
            log_path=str(self.log_path),
        )
        # The record's own free-form map is the ONLY route a plugin has to put
        # a kind-specific field in its launch result: core builds the result,
        # so without this a plugin would have to mutate the TypedDict core just
        # handed back — reaching around the transaction that exists to own it.
        # Terminal's `connector_type` (pty/ssh/telnet) is the first real case:
        # an agent that launched `kind="ssh"` and reads it back to confirm got
        # nothing, silently, once the plugin stopped building the dict itself.
        # Copied, not aliased, so a later mutation of the live session's map
        # cannot rewrite a result a caller already holds.
        extra = getattr(record, "extra", None)
        if extra:
            result["extra"] = dict(extra)
        return result


@dataclass
class PluginContext:
    """What a plugin is handed at ``create_pool``."""

    kind: str
    recordings_dir: Path
    id_in_use: IdInUse
    log: Any = field(default_factory=lambda: _LOG)

    def redaction_mode(self) -> str:
        """The resolved ``OCTOWRIGHT_REDACT_INPUTS`` policy.

        Plugins are handed the resolved policy and never read the environment
        themselves — the same reasoning as ``redact_headers_for_report``
        flooring at ``passwords`` rather than trusting a caller.
        """
        raw = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "").strip().lower()
        return raw if raw in {"off", "passwords", "all"} else "passwords"

    def artifact(self, session: Any, name: str, suffix: str) -> ArtifactHandle:
        """Reserve a contained side-artifact path for ``session``.

        The plugin writes to the returned ``.path`` and then calls
        ``.commit(mime_type=...)``. It never composes a path itself.
        """
        return reserve_artifact(
            recorder=session.recorder,
            instance_id=session.instance_id,
            recordings_dir=self.recordings_dir,
            artifact_id=name,
            suffix=suffix,
        )

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
        # This is the single point where core composes a recording filename from
        # a plugin-supplied id, so it is where the id's syntax is settled.
        # ``new_log_path`` builds ``{stamp}-{kind}-{instance_id}[-{label}]`` and
        # readers recover the id as ``stem.split("-")[2]``; a hyphen here shifts
        # every later field, so the id would parse back as the wrong token.
        validate_instance_id(instance_id)
        log_path = new_log_path(self.recordings_dir, instance_id, label, self.kind)
        # ``new_log_path`` sanitizes only ``label``; ``instance_id`` is
        # plugin-supplied and reaches the filename raw, so a traversing id
        # resolves outside the recordings root. Checked BEFORE the recorder is
        # built, because ``Recorder.__init__`` is what materializes the
        # directory (``mkdir(parents=True)``) — validating afterwards would
        # already have created the escape.
        reject_unsafe_path(log_path, self.recordings_dir, label="plugin recording")
        recorder = Recorder(log_path)
        # The opening row is inside the guard: an OSError (or an exhausted
        # control budget) while writing it would otherwise leave an open handle
        # and an orphan file with nothing to clean them up.
        try:
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
                _id_in_use=lambda candidate: self.id_in_use(candidate, exclude_kind=self.kind),
            )
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
        # An empty file is included: nothing was recorded at all, which is the
        # shape a failure during the opening-row write leaves behind.
        if actions <= _OPENING_ONLY:
            log_path.unlink()
