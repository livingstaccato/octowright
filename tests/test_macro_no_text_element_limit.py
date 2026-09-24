# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""How many elements ``expect_no_text`` reads before it refuses to pass.

A scan that reaches the limit and found nothing fails: a security check does not
pass on a page it only partly read. The limit is 20000 by default, set for the
daemon with ``OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT``, and per step with
``element_limit``. The exported CLI resolves it the same way at run time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.lint import lint_macro
from octowright.session.core import BrowserSession
from octowright.session.rendered_text import ELEMENT_LIMIT, resolve_element_limit

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


def test_the_default_is_twenty_thousand() -> None:
    assert ELEMENT_LIMIT == 20000
    assert resolve_element_limit(None, {}) == 20000


def test_the_environment_sets_the_daemon_default() -> None:
    assert resolve_element_limit(None, {"OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT": "100000"}) == 100000


def test_a_step_overrides_the_environment() -> None:
    assert resolve_element_limit(5000, {"OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT": "100000"}) == 5000


@pytest.mark.parametrize("raw", ["", "lots", "0", "-5", "1.5"])
def test_an_unusable_environment_value_keeps_the_default(raw: str) -> None:
    """A typo must not remove the limit, nor set one no page can pass."""
    assert resolve_element_limit(None, {"OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT": raw}) == ELEMENT_LIMIT


@pytest.mark.parametrize("bad", [0, -1, True, "100", 2.5])
def test_an_unusable_step_value_is_refused(bad: Any) -> None:
    with pytest.raises(ValueError, match="element_limit"):
        resolve_element_limit(bad, {})


def _session(tmp_path: Path, frame: MagicMock) -> BrowserSession:
    page = MagicMock()
    page.url = "https://octowright.com/"
    page.frames = [frame]
    session = BrowserSession(
        instance_id="test",
        kind="firefox",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )
    return session


def _frame(truncated: bool = False) -> MagicMock:
    frame = MagicMock()
    frame.evaluate = AsyncMock(return_value={"pieces": ["Welcome"], "matched": 1, "truncated": truncated})
    return frame


@pytest.mark.anyio
async def test_the_step_limit_reaches_the_scan(tmp_path: Path) -> None:
    frame = _frame()
    await _session(tmp_path, frame).expect_no_text(SECRET, element_limit=123)
    assert frame.evaluate.await_args.args[1]["limit"] == 123


@pytest.mark.anyio
async def test_the_environment_limit_reaches_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_NO_TEXT_ELEMENT_LIMIT", "77777")
    frame = _frame()
    await _session(tmp_path, frame).expect_no_text(SECRET)
    assert frame.evaluate.await_args.args[1]["limit"] == 77777


@pytest.mark.anyio
async def test_the_refusal_names_the_limit_that_applied(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="more than 123 elements") as excinfo:
        await _session(tmp_path, _frame(truncated=True)).expect_no_text(SECRET, element_limit=123)
    assert "element_limit" in str(excinfo.value)


@pytest.mark.anyio
async def test_a_step_limit_is_recorded_so_replay_keeps_it(tmp_path: Path) -> None:
    session = _session(tmp_path, _frame())
    await session.expect_no_text(SECRET, element_limit=123)
    assert session.recorder.record.call_args.kwargs["element_limit"] == 123


@pytest.mark.anyio
async def test_no_step_limit_records_none(tmp_path: Path) -> None:
    session = _session(tmp_path, _frame())
    await session.expect_no_text(SECRET)
    assert "element_limit" not in session.recorder.record.call_args.kwargs


def test_a_step_limit_lints() -> None:
    actions = [{"action": "expect_no_text", "text": "{{password}}", "element_limit": 100000}]
    assert [i.message for i in lint_macro({"name": "m", "actions": actions}) if i.severity == "error"] == []
