# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal telemetry is noop-safe by default.

Span/metric emission itself is covered by provide.telemetry's own suite (see
``tests/test_tracing.py``); here we only verify that wrapping the engine
lifecycle in spans + counters drives a full session without error when
telemetry is off (the default) and that the instruments are real recorders.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from octowright_terminal import engine as engine_mod
from octowright_terminal.engine import TerminalEngine

from octowright.recorder import Recorder

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


def test_terminal_counters_are_instruments() -> None:
    # The two counters named in the plan must exist and accept .add(...) with a
    # connector_type label without raising on the noop (telemetry-off) path.
    engine_mod._TERMINAL_LAUNCHED.add(1, attributes={"connector_type": "pty"})
    engine_mod._TERMINAL_CLOSED.add(1, attributes={"connector_type": "pty"})


async def test_engine_lifecycle_telemetry_noop_safe(tmp_path: Path) -> None:
    log_path = tmp_path / "t.jsonl"
    recorder = Recorder(log_path)
    engine = TerminalEngine("tel-1", "cat", "pty", {"command": "/bin/cat"}, recorder)
    # launch span + LAUNCHED counter
    await engine.start()
    try:
        # send_input span
        await engine.send_input("telemetry-ping\n")
        matched = await engine.wait_for(text="telemetry-ping", timeout=5.0)
        assert matched
    finally:
        # close span + CLOSED counter
        await engine.stop()
        recorder.close()

    actions = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    names = [a["action"] for a in actions]
    assert names[0] == "terminal_start"
    assert "terminal_input" in names
    assert names[-1] == "terminal_stop"
    # The wrapping must not double- or drop-record the stop event.
    assert names.count("terminal_stop") == 1
    await asyncio.sleep(0)  # let any pending callbacks settle on the loop
