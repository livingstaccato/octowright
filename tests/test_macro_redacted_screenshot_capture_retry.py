# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redacted screenshot retries Chrome's "Unable to capture screenshot".

Chrome answers ``Page.captureScreenshot`` that way when it had no frame to copy
yet -- seen once on a Linux CI runner, between two proofs that passed. Nothing
on the page changed, so asking again is safe: the proof after the capture
still covers every change since counting began.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from playwright.async_api import Error as PlaywrightError

from octowright.macros import safe_screenshot

PNG = b"\x89PNG-capture-retry"
NO_FRAME = "Protocol error (Page.captureScreenshot): Unable to capture screenshot"
_ATTEMPTS = 3


class _CDP:
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.sends = 0

    async def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert method == "Page.captureScreenshot"
        self.sends += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"data": base64.b64encode(PNG).decode()}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_screenshot, "_CAPTURE_RETRY_SECONDS", 0.0, raising=False)


@pytest.mark.asyncio
async def test_a_capture_with_no_frame_yet_is_asked_again(tmp_path: Path) -> None:
    cdp = _CDP([PlaywrightError(NO_FRAME), PlaywrightError(NO_FRAME)])
    target = tmp_path / "shot.png"

    await safe_screenshot._capture(cdp, target)

    assert cdp.sends == 3
    assert target.read_bytes() == PNG


@pytest.mark.asyncio
async def test_a_capture_that_never_gets_a_frame_fails_with_no_file(tmp_path: Path) -> None:
    cdp = _CDP([PlaywrightError(NO_FRAME) for _ in range(_ATTEMPTS)])
    target = tmp_path / "shot.png"

    with pytest.raises(PlaywrightError, match="Unable to capture screenshot"):
        await safe_screenshot._capture(cdp, target)

    assert cdp.sends == _ATTEMPTS
    assert not target.exists()


@pytest.mark.asyncio
async def test_any_other_capture_failure_is_not_retried(tmp_path: Path) -> None:
    cdp = _CDP([PlaywrightError("Target page, context or browser has been closed")])

    with pytest.raises(PlaywrightError, match="has been closed"):
        await safe_screenshot._capture(cdp, tmp_path / "shot.png")

    assert cdp.sends == 1
