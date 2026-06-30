# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.server.meta import octowright_status


@pytest.fixture(autouse=True)
def _clear_screencast_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "OCTOWRIGHT_LIVE_SCREENCAST_FPS",
        "OCTOWRIGHT_LIVE_SCREENCAST_QUALITY",
        "OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE",
    ):
        monkeypatch.delenv(key, raising=False)


def test_status_has_screencast_block() -> None:
    status = octowright_status()
    sc = status["screencast"]
    assert sc["fps"] == 10
    assert sc["quality"] == 70
    assert sc["fullscreen_mode"] == "native"


def test_status_screencast_block_uses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FPS", "30")
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_QUALITY", "44")
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE", "panel")

    assert octowright_status()["screencast"] == {
        "fps": 30,
        "quality": 44,
        "fullscreen_mode": "panel",
    }
