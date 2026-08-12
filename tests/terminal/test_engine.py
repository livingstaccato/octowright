# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

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
    # Wait for the child to exit and the poll loop to record EOF on its own. The
    # connector's cross-platform EOF branch flips is_connected() when a post-exit
    # master read returns b"" (macOS) just as it does on EIO (Linux), so the loop
    # records terminal_stop(reason="eof") without us calling stop() first.
    for _ in range(100):
        if any(a["action"] == "terminal_stop" for a in _read_actions(log_path)):
            break
        await asyncio.sleep(0.05)
    await engine.stop()  # no-op for recording: the _stop_recorded guard already fired
    recorder.close()

    actions = _read_actions(log_path)
    names = [a["action"] for a in actions]
    assert names[0] == "terminal_start"
    assert "terminal_output" in names
    output = "".join(a.get("data", "") for a in actions if a["action"] == "terminal_output")
    assert "hello-engine" in output
    # Child exited on its own → EOF detected by the poll loop, recorded exactly
    # once (double-stop guard) and last, with reason "eof" cross-platform.
    assert names.count("terminal_stop") == 1
    assert actions[-1]["action"] == "terminal_stop"
    assert next(a for a in actions if a["action"] == "terminal_stop")["reason"] == "eof"


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


async def test_engine_send_input_on_dead_terminal_raises(tmp_path: Path) -> None:
    from octowright.terminal.errors import TerminalDisconnectedError

    recorder = Recorder(tmp_path / "t.jsonl")
    engine = TerminalEngine("eng-4", "cat", "pty", {"command": "/bin/cat"}, recorder)
    await engine.start()
    await engine.stop()  # connector now disconnected
    # Sending to a dead terminal must RAISE (input not delivered), not silently
    # succeed — and it records no phantom terminal_input.
    with pytest.raises(TerminalDisconnectedError):
        await engine.send_input("ignored\n")
    recorder.close()

    actions = _read_actions(tmp_path / "t.jsonl")
    assert not any(a["action"] == "terminal_input" for a in actions)


async def test_poll_done_preserves_original_error_when_stop_record_fails() -> None:
    class FailingRecorder:
        def record(self, *_args: Any, **_kwargs: Any) -> None:
            raise OSError("recording disk failed")

    async def fail_poll() -> None:
        raise RuntimeError("connector poll failed")

    engine = object.__new__(TerminalEngine)
    engine._instance_id = "eng-failed"
    engine._connector_type = "pty"
    engine._recorder = FailingRecorder()
    engine._stop_recorded = False
    engine._poll_error = None

    task = asyncio.create_task(fail_poll())
    with pytest.raises(RuntimeError, match="connector poll failed"):
        await task

    # asyncio invokes this as a done-callback: it must consume recorder errors,
    # retain the causal poll exception, and make the stop transition exactly once.
    engine._on_poll_done(task)
    assert isinstance(engine._poll_error, RuntimeError)
    assert str(engine._poll_error) == "connector poll failed"
    assert engine._stop_recorded is True
