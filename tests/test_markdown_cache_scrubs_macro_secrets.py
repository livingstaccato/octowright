# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The page-text (markdown) cache never persists a macro's credential values.

The recorder has scrubbed them through the session privacy ledger since the
ledger existed; the markdown cache is a second durable write of page content
and bypassed it, so a page that rendered the password -- exactly what
``expect_no_text`` exists to catch -- put it on disk in cleartext.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.privacy import REDACTED, install_sensitive_recorder
from octowright.session.core import BrowserSession

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.content = AsyncMock(return_value=f"<html><body><h1>Your password is {SECRET}</h1></body></html>")
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


@pytest.mark.anyio
async def test_a_macro_credential_is_scrubbed_from_the_markdown_cache(session: BrowserSession) -> None:
    install_sensitive_recorder(session, [SECRET])
    path = await session.capture_markdown(force=True)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert REDACTED in text


@pytest.mark.anyio
async def test_a_value_admitted_later_is_scrubbed_from_the_next_capture(session: BrowserSession) -> None:
    """The scrubber reads the ledger live, like the recorder wrapper does."""
    ledger = install_sensitive_recorder(session, [])
    ledger.add([SECRET])
    path = await session.capture_markdown(force=True)
    assert path is not None and SECRET not in path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_without_a_macro_run_the_cache_is_unchanged(session: BrowserSession) -> None:
    path = await session.capture_markdown(force=True)
    assert path is not None and SECRET in path.read_text(encoding="utf-8")
