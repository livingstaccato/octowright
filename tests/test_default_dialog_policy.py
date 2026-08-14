# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Regression tests for the default dialog policy.

A fresh `BrowserSession` must default to "dismiss" so that an unexpected
`alert()`/`confirm()`/`prompt()` raised mid-macro does not block forever
waiting for a handler that was never registered.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octowright.recorder import Recorder
from octowright.session.core import BrowserSession


def _make_session(tmp_path: Path) -> BrowserSession:
    """Construct a BrowserSession backed by stubs (no real Playwright resources)."""
    page: Any = SimpleNamespace(url="about:blank")
    context: Any = SimpleNamespace()
    log_path = tmp_path / "session.jsonl"
    recorder = Recorder(log_path)
    return BrowserSession(
        instance_id="inst",
        kind="chromium",
        label=None,
        url="about:blank",
        browser=None,
        context=context,
        page=page,
        recorder=recorder,
        log_path=log_path,
    )


def test_default_dialog_policy_is_dismiss(tmp_path: Path) -> None:
    """Fresh sessions must default to dismissing dialogs (safe default)."""
    session = _make_session(tmp_path)
    assert session._dialog_policy == "dismiss"
    assert session._dialog_prompt_text is None


@pytest.mark.anyio
async def test_set_dialog_policy_accept_overrides_default(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    result = await session.set_dialog_policy("accept", "yes please")
    assert result == {"ok": True, "policy": "accept", "prompt_text": "yes please"}
    assert session._dialog_policy == "accept"
    assert session._dialog_prompt_text == "yes please"


@pytest.mark.anyio
async def test_set_dialog_policy_manual_overrides_default(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    await session.set_dialog_policy("manual")
    assert session._dialog_policy == "manual"
    assert session._dialog_prompt_text is None


@pytest.mark.anyio
async def test_set_dialog_policy_rejects_unknown_policy(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    with pytest.raises(ValueError, match=r"accept\|dismiss\|manual"):
        await session.set_dialog_policy("ignore")


@pytest.mark.anyio
async def test_handle_dialog_dismisses_by_default(tmp_path: Path) -> None:
    """The dialog handler must invoke dialog.dismiss() under the default policy."""
    session = _make_session(tmp_path)

    dismissed = asyncio.Event()
    accepted = asyncio.Event()

    class FakeDialog:
        type = "confirm"
        message = "are you sure?"

        async def dismiss(self) -> None:
            dismissed.set()

        async def accept(self, *_args: Any, **_kwargs: Any) -> None:
            accepted.set()

    session._handle_dialog(FakeDialog())
    # The handler schedules an asyncio task; give it a chance to run.
    await asyncio.wait_for(dismissed.wait(), timeout=1.0)
    assert dismissed.is_set()
    assert not accepted.is_set()


@pytest.mark.anyio
async def test_handle_dialog_accepts_when_policy_set(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    await session.set_dialog_policy("accept", "hello")

    accepted_with: list[Any] = []
    dismissed = asyncio.Event()

    class FakeDialog:
        type = "prompt"
        message = "name?"

        async def accept(self, prompt_text: str = "") -> None:
            accepted_with.append(prompt_text)

        async def dismiss(self) -> None:
            dismissed.set()

    session._handle_dialog(FakeDialog())
    for _ in range(20):
        if accepted_with:
            break
        await asyncio.sleep(0.01)
    assert accepted_with == ["hello"]
    assert not dismissed.is_set()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
