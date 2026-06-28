# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import importlib

import pytest

import octowright.session.screencast_config as cfg


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    for k in (
        "OCTOWRIGHT_LIVE_SCREENCAST_FPS",
        "OCTOWRIGHT_LIVE_SCREENCAST_QUALITY",
        "OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE",
    ):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(cfg)
    yield


def test_defaults():
    assert cfg.screencast_fps() == 10
    assert cfg.screencast_quality() == 70
    assert cfg.fullscreen_mode() == "native"
    assert cfg.screencast_config_block() == {
        "fps": 10,
        "quality": 70,
        "fullscreen_mode": "native",
    }


def test_fps_override_and_clamp(monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FPS", "30")
    assert cfg.screencast_fps() == 30
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FPS", "0")
    assert cfg.screencast_fps() == 1
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FPS", "garbage")
    assert cfg.screencast_fps() == 10


def test_quality_clamp(monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_QUALITY", "500")
    assert cfg.screencast_quality() == 100
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_QUALITY", "0")
    assert cfg.screencast_quality() == 1
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_QUALITY", "garbage")
    assert cfg.screencast_quality() == 70


def test_fullscreen_mode_validation(monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE", "panel")
    assert cfg.fullscreen_mode() == "panel"
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE", "weird")
    assert cfg.fullscreen_mode() == "native"


def test_config_block_uses_env_values(monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FPS", "30")
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_QUALITY", "44")
    monkeypatch.setenv("OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE", "panel")

    assert cfg.screencast_config_block() == {
        "fps": 30,
        "quality": 44,
        "fullscreen_mode": "panel",
    }
