# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A download lost with its browser says so.

In the field the only trace of a Chromium browser-process crash was
``download_save_error TargetClosedError('Download.save_as: Target page, context
or browser has been closed')`` followed 2 ms later by ``close ... reason:
external`` -- indistinguishable from a user closing the window mid-download.
The rejection arrives a beat BEFORE the close signal that judges the exit, so
the save path waits (bounded) for that verdict instead of guessing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool import incidents
from tests.test_downloads import _drain, _make_session


class TargetClosedError(Exception):
    """Same class name as Playwright's; the save path matches it by name."""


class _DyingDownload:
    url = "blob:https://example.test/abc"
    suggested_filename = "x.json"

    def __init__(self, exc: Exception) -> None:
        self.save_as = AsyncMock(side_effect=exc)


def _rows(session: Any, action: str) -> list[dict[str, Any]]:
    lines = Path(session.log_path).read_text().splitlines()
    return [r for r in (json.loads(line) for line in lines if line.strip()) if r["action"] == action]


def _target_closed() -> TargetClosedError:
    return TargetClosedError("Download.save_as: Target page, context or browser has been closed")


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_download_lost_to_a_process_crash_says_the_browser_crashed(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    incident = incidents.record(incidents.CATEGORY_BROWSER_PROCESS_CRASH, instance_id="x", lost_downloads=0)

    session._handle_download(_DyingDownload(_target_closed()))
    await asyncio.sleep(0.02)  # the rejection lands before the close signal ...
    session._process_crash_incident = incident
    session._exit_verdict = "crashed"
    session._exit_verdict_event.set()  # ... which then judges the exit
    await _drain(session)

    (row,) = _rows(session, "download_save_error")
    assert row["cause"] == "browser_crashed"
    assert "TargetClosedError" in row["error"]
    assert incident["lost_downloads"] == 1


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_download_lost_to_a_window_close_says_so(tmp_path: Path) -> None:
    session = _make_session(tmp_path)
    session._exit_verdict = "closed"
    session._exit_verdict_event.set()

    session._handle_download(_DyingDownload(_target_closed()))
    await _drain(session)

    (row,) = _rows(session, "download_save_error")
    assert row["cause"] == "browser_closed"


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_an_unrelated_save_failure_does_not_wait_or_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.session import downloads

    monkeypatch.setattr(downloads, "EXIT_VERDICT_WAIT_SECONDS", 30.0)
    session = _make_session(tmp_path)

    session._handle_download(_DyingDownload(OSError("disk full")))
    await asyncio.wait_for(_drain(session), timeout=2.0)

    (row,) = _rows(session, "download_save_error")
    assert "cause" not in row


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_no_verdict_in_time_still_records_the_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that closed under a live browser is never evicted: no verdict comes."""
    from octowright.session import downloads

    monkeypatch.setattr(downloads, "EXIT_VERDICT_WAIT_SECONDS", 0.05)
    session = _make_session(tmp_path)

    session._handle_download(_DyingDownload(_target_closed()))
    await _drain(session)

    (row,) = _rows(session, "download_save_error")
    assert "cause" not in row


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_a_cancelled_wait_still_records_the_error(tmp_path: Path) -> None:
    """Teardown cancels background stragglers; the error row must survive that."""
    session = _make_session(tmp_path)

    session._handle_download(_DyingDownload(_target_closed()))
    await asyncio.sleep(0.02)
    for task in list(session._bg_tasks):
        task.cancel()
    await asyncio.gather(*list(session._bg_tasks), return_exceptions=True)

    assert len(_rows(session, "download_save_error")) == 1
