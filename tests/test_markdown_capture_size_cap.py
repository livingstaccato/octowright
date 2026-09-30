# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Automatic markdown capture refuses a page whose HTML is over a size cap.

Every page load schedules ``capture_markdown``, which pulled ``content()`` with
a time limit but no size limit and converted it in the leader: a remote page
that builds a huge DOM and fires ``load`` cost the daemon -- the process every
session shares -- the serialised DOM, the conversion's copies and the write,
with nobody having asked to read it. The page's own size is checked first, in
the page, so an oversized DOM is never serialised into the leader at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session import markdown_render
from octowright.session.core import BrowserSession


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _session(tmp_path: Path, html: str, measured: Any) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://app.test/"
    page.content = AsyncMock(return_value=html)
    page.evaluate = AsyncMock(return_value=measured)
    return BrowserSession(
        instance_id="mdcap",
        kind="chromium",
        label="t",
        url="https://app.test",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


@pytest.mark.anyio
async def test_an_oversized_page_is_never_serialised_into_the_leader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(markdown_render, "MARKDOWN_CAPTURE_MAX_HTML_CHARS", 1000)
    session = _session(tmp_path, "<p>x</p>", measured=5000)

    assert await session.capture_markdown(force=True) is None

    session.page.content.assert_not_awaited()
    assert "too large" in repr(session._last_markdown_capture_error)
    session.recorder.record.assert_any_call(
        "markdown_cache_error", error=repr(session._last_markdown_capture_error), url="https://app.test/"
    )


@pytest.mark.anyio
async def test_html_that_grew_past_the_cap_after_measuring_is_not_converted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The measurement is a preflight, not a guarantee: the page can grow in
    between, so the HTML actually returned is checked before conversion."""
    monkeypatch.setattr(markdown_render, "MARKDOWN_CAPTURE_MAX_HTML_CHARS", 1000)
    session = _session(tmp_path, "<p>" + "x" * 2000 + "</p>", measured=10)
    convert = AsyncMock()
    monkeypatch.setattr(type(session), "_extract_markdown", convert)

    assert await session.capture_markdown(force=True) is None
    convert.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("measured", [10, None, "nonsense"])
async def test_a_small_or_unmeasurable_page_is_still_captured(tmp_path: Path, measured: Any) -> None:
    session = _session(tmp_path, "<h1>Hello</h1>", measured=measured)

    path = await session.capture_markdown(force=True)

    assert path is not None and "Hello" in path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_a_failed_measurement_falls_back_to_the_post_read_check(tmp_path: Path) -> None:
    session = _session(tmp_path, "<h1>Hello</h1>", measured=None)
    session.page.evaluate = AsyncMock(side_effect=RuntimeError("Execution context was destroyed"))

    path = await session.capture_markdown(force=True)

    assert path is not None
