# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from octowright.recorder import Recorder
from octowright.terminal.engine import TerminalEngine

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


def _read_actions(log_path: Path) -> list[dict]:
    return [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


async def test_engine_records_start_output_and_stop(tmp_path: Path) -> None:
    log_path = tmp_path / "t.jsonl"
    recorder = Recorder(log_path)
    engine = TerminalEngine("eng-1", "echo", "pty", {"command": "/bin/echo", "args": ["hello-engine"]}, recorder)
    await engine.start()
    # Wait for the echo output to be captured.
    for _ in range(60):
        captured = "".join(a.get("data", "") for a in _read_actions(log_path) if a["action"] == "terminal_output")
        if "hello-engine" in captured:
            break
        await asyncio.sleep(0.05)
    await engine.stop()
    recorder.close()

    actions = _read_actions(log_path)
    names = [a["action"] for a in actions]
    assert names[0] == "terminal_start"
    assert "terminal_output" in names
    output = "".join(a.get("data", "") for a in actions if a["action"] == "terminal_output")
    assert "hello-engine" in output
    # Exactly one terminal_stop (double-stop guard), recorded last. The reason is
    # "eof" when the connector reports disconnect (Linux: PTY read raises EIO) or
    # "closed" via explicit stop() (macOS: a post-exit PTY master read returns b"").
    assert names.count("terminal_stop") == 1
    assert actions[-1]["action"] == "terminal_stop"
    assert next(a for a in actions if a["action"] == "terminal_stop")["reason"] in {"eof", "closed"}


async def test_engine_send_input_and_snapshot(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "t.jsonl")
    engine = TerminalEngine("eng-2", "cat", "pty", {"command": "/bin/cat"}, recorder)
    await engine.start()
    try:
        await engine.send_input("marco\n")
        matched = await engine.wait_for(text="marco", timeout=5.0)
        assert matched
        snap = await engine.snapshot()
        assert "marco" in snap["screen"]
    finally:
        await engine.stop()
        recorder.close()

    actions = _read_actions(tmp_path / "t.jsonl")
    inputs = [a for a in actions if a["action"] == "terminal_input"]
    assert inputs and inputs[0]["keys"] == "marco\n"


async def test_engine_masks_password_source_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright import defaults

    monkeypatch.setattr(defaults, "INPUT_REDACTION_MODE", "passwords")
    recorder = Recorder(tmp_path / "t.jsonl")
    engine = TerminalEngine("eng-3", "cat", "pty", {"command": "/bin/cat"}, recorder)
    await engine.start()
    try:
        await engine.send_input("s3cret\n", password=True)
    finally:
        await engine.stop()
        recorder.close()

    actions = _read_actions(tmp_path / "t.jsonl")
    masked = next(a for a in actions if a["action"] == "terminal_input")
    assert masked["keys"] == "***"
    assert masked["byte_count"] == len(b"s3cret\n")


async def test_engine_send_input_on_dead_terminal_is_noop(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path / "t.jsonl")
    engine = TerminalEngine("eng-4", "cat", "pty", {"command": "/bin/cat"}, recorder)
    await engine.start()
    await engine.stop()  # connector now disconnected
    # Sending to a dead terminal records no phantom input (and logs a warning).
    await engine.send_input("ignored\n")
    recorder.close()

    actions = _read_actions(tmp_path / "t.jsonl")
    assert not any(a["action"] == "terminal_input" for a in actions)
