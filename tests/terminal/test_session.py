# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from octowright.recorder import Recorder
from octowright.terminal.engine import TerminalEngine
from octowright.terminal.session import TerminalSession

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_session_close_stops_engine_and_recorder(tmp_path: Path) -> None:
    log_path = tmp_path / "t.jsonl"
    recorder = Recorder(log_path)
    engine = TerminalEngine("s-1", "cat", "pty", {"command": "/bin/cat"}, recorder)
    session = TerminalSession(
        instance_id="s-1",
        kind="terminal",
        connector_type="pty",
        label="cat",
        profile=None,
        recorder=recorder,
        log_path=log_path,
        engine=engine,
    )
    await engine.start()
    assert session.kind == "terminal"
    assert session.url is None
    await session.close()
    # recorder closed -> file handle closed.
    assert recorder._fh.closed
