# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A redacted screenshot retries Chrome's "Unable to capture screenshot".

Chrome answers ``Page.captureScreenshot`` that way when it had no frame to copy
yet -- seen once on a Linux CI runner, between two proofs that passed. Change
counting stays on across the retries, so the proof after the capture still
covers every change since counting began. A page that is hidden or has no size
can never produce a frame, so it is not asked again.
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
    def __init__(self, failures: list[Exception], *, can_produce_frame: object = True) -> None:
        self.failures = failures
        self.can_produce_frame = can_produce_frame
        self.sends = 0
        self.probes = 0

    async def send(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "Runtime.evaluate":
            assert params["expression"] == safe_screenshot._CAN_PRODUCE_FRAME
            self.probes += 1
            if isinstance(self.can_produce_frame, Exception):
                raise self.can_produce_frame
            return {"result": {"type": "boolean", "value": self.can_produce_frame}}
        assert method == "Page.captureScreenshot"
        self.sends += 1
        if self.failures:
            raise self.failures.pop(0)
        return {"data": base64.b64encode(PNG).decode()}


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_screenshot, "_CAPTURE_RETRY_SECONDS", 0.0)


def test_the_attempt_count_is_the_one_these_tests_assume() -> None:
    assert safe_screenshot._CAPTURE_ATTEMPTS == _ATTEMPTS


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


@pytest.mark.asyncio
@pytest.mark.parametrize("can_produce_frame", [False, None])
async def test_a_page_that_can_never_produce_a_frame_is_not_asked_again(
    tmp_path: Path, can_produce_frame: object
) -> None:
    """Hidden, zero-size, or an answer that is not a plain true: asking again would fail the same way."""
    cdp = _CDP([PlaywrightError(NO_FRAME)], can_produce_frame=can_produce_frame)

    with pytest.raises(PlaywrightError, match="Unable to capture screenshot"):
        await safe_screenshot._capture(cdp, tmp_path / "shot.png")

    assert (cdp.sends, cdp.probes) == (1, 1)


@pytest.mark.asyncio
async def test_a_frame_probe_that_fails_raises_the_capture_error(tmp_path: Path) -> None:
    cdp = _CDP([PlaywrightError(NO_FRAME)], can_produce_frame=PlaywrightError("Execution context was destroyed"))

    with pytest.raises(PlaywrightError, match="Unable to capture screenshot"):
        await safe_screenshot._capture(cdp, tmp_path / "shot.png")

    assert cdp.sends == 1
