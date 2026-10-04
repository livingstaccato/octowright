# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A ``try`` that suppresses a failing step writes no classified value anywhere.

``do_try`` and ``do_try_each`` recorded and logged the step that failed. The
step they hold is the EXPANDED one -- ``{{password}}`` already substituted --
and the error is whatever the dispatch raised, which a selector or value built
from an argument carries verbatim (a Playwright timeout names its locator). The
log line goes to the daemon log with no scrub at all; the JSONL row is
scrubbed by the session ledger, but only of values the ledger admitted and
only in the spelling it admitted. So the step is recorded as the macro wrote
it, and the error by its type alone once the session holds a classified value.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.recorder import Recorder
from tests.test_macro_credential_fill_origin import SECRET, _session

pytestmark = pytest.mark.anyio

OWN = "https://app.example.test/"


def _spellings() -> tuple[str, ...]:
    return (SECRET, SECRET.upper(), SECRET.lower(), SECRET.swapcase())


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, actions: list[dict[str, Any]]) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    return await execution.run_macro(session, "outer", {"password": SECRET, "email": "me@example.test"})


def _session_with_jsonl(tmp_path: Path) -> tuple[Any, Path]:
    session = _session(tmp_path, launch=OWN, current=OWN)
    log_path = tmp_path / "rec.jsonl"
    session.recorder = Recorder(log_path)
    return session, log_path


def _assert_absent(*surfaces: str) -> None:
    for text in surfaces:
        for spelling in _spellings():
            assert spelling not in text


def _failing_fill(session: Any) -> None:
    # A real engine's error names what it was asked to do; an upper-cased
    # echo is the variant an exact-match scrub misses.
    session.fill.side_effect = TimeoutError(f"fill({SECRET!r}) timed out; FILL {SECRET.upper()}")


async def test_try_suppression_records_and_logs_no_classified_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session, log_path = _session_with_jsonl(tmp_path)
    _failing_fill(session)
    step = {"action": "fill", "selector": "#pw", "value": "{{password}}"}
    result = await _run(monkeypatch, session, [{"action": "try", "actions": [step]}])
    session.recorder.close()
    assert result["skipped"] >= 1
    jsonl = log_path.read_text()
    assert "try_suppressed" in jsonl
    _assert_absent(jsonl, caplog.text, repr(result))
    # The step as the macro wrote it (its value redacted by structure, as the
    # failure payload's `failed_action` is), and what kind of failure it was.
    row = next(json.loads(line) for line in jsonl.splitlines() if '"try_suppressed"' in line)
    assert row["failed_action"]["action"] == "fill"
    assert row["failed_action"]["selector"] == "#pw"
    assert row["error"] == "TimeoutError"
    assert "TimeoutError" in caplog.text


async def test_try_each_branch_failure_records_and_logs_no_classified_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session, log_path = _session_with_jsonl(tmp_path)
    _failing_fill(session)
    branches = [[{"action": "fill", "selector": "#pw", "value": "{{password}}"}], [{"action": "wait", "ms": 0}]]
    await _run(monkeypatch, session, [{"action": "try_each", "branches": branches}])
    session.recorder.close()
    jsonl = log_path.read_text()
    assert "try_each_branch_failed" in jsonl
    _assert_absent(jsonl, caplog.text)
    assert "TimeoutError" in jsonl


async def test_try_each_all_branches_failed_payload_carries_no_classified_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session, log_path = _session_with_jsonl(tmp_path)
    _failing_fill(session)
    branches = [[{"action": "fill", "selector": "#pw", "value": "{{password}}"}]]
    with pytest.raises(RuntimeError) as caught:
        await _run(monkeypatch, session, [{"action": "try_each", "branches": branches}])
    session.recorder.close()
    _assert_absent(log_path.read_text(), caplog.text, str(caught.value))


async def test_a_step_with_no_classified_value_keeps_its_full_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.macros import execution

    session, log_path = _session_with_jsonl(tmp_path)
    session.fill.side_effect = TimeoutError("no #banner")
    actions = [{"action": "try", "actions": [{"action": "fill", "selector": "#banner", "value": "x"}]}]
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    await execution.run_macro(session, "plain", {})
    session.recorder.close()
    row = next(json.loads(line) for line in log_path.read_text().splitlines() if '"try_suppressed"' in line)
    assert row["error"] == "TimeoutError('no #banner')"


async def test_a_called_macros_suppressed_step_is_recorded_as_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session, log_path = _session_with_jsonl(tmp_path)
    session.click = AsyncMock(side_effect=TimeoutError(f"waiting for text={SECRET.upper()}"))
    macros = {
        "outer": [{"action": "macro_call", "name": "inner", "args": {"who": "{{password}}"}}],
        "inner": [{"action": "try", "actions": [{"action": "click", "selector": "text={{who}}"}]}],
    }
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": macros[name]})
    await execution.run_macro(session, "outer", {"password": SECRET})
    session.recorder.close()
    jsonl = log_path.read_text()
    row = next(json.loads(line) for line in jsonl.splitlines() if '"try_suppressed"' in line)
    assert row["failed_action"]["selector"] == "text={{who}}"
    _assert_absent(jsonl, caplog.text)
