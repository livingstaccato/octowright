# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""TerminalSession: the per-terminal record the pool, tools, and dashboard see.

Deliberately a parallel dataclass to BrowserSession (not a subclass) — it carries
only the minimal shape the rest of Octowright depends on: instance_id, kind,
label, profile, url(None), recorder, log_path, protected, plus the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from octowright.recorder import Recorder
from octowright_terminal.engine import TerminalEngine


@dataclass
class TerminalSession:
    instance_id: str
    kind: str  # always "terminal" (browser sessions use the engine name)
    connector_type: str  # "pty" | "ssh"
    label: str | None
    profile: str | None
    recorder: Recorder
    log_path: Path
    engine: TerminalEngine
    protected: bool = False
    url: str | None = None  # always None; present so dashboard summaries are uniform
    #: Required by the SessionRecord Protocol: the free-form map a kind uses for
    #: settings core does not model. `connector_type` stays a real field because
    #: this package's own tools and dashboard detail read it directly, and it is
    #: ALSO mirrored in here by the pool -- that is the one route core's launch
    #: transaction offers for putting a kind-specific field in a `LaunchResult`
    #: (see `SessionLaunch.commit`), and without it `terminal_launch` reported
    #: no connector type at all.
    extra: dict[str, Any] = field(default_factory=dict)

    async def close(self) -> None:
        # Protected-close refusal is enforced by TerminalPool.close (which holds
        # the force gate); this performs the actual teardown unconditionally.
        await self.engine.stop()
        self.recorder.close()
