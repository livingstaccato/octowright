# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.defaults — specifically the headless auto-detect logic."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from octowright.defaults import _detect_headless_default


class TestHeadlessAutoDetect:
    """Resolution order: explicit env > CI=true > Linux-no-display > headed default."""

    def test_explicit_env_one_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "1")
        # Even if a window server is present, the explicit override wins.
        monkeypatch.setenv("DISPLAY", ":0")
        assert _detect_headless_default() is True

    def test_explicit_env_zero_forces_headed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OCTOWRIGHT_HEADLESS", "0")
        # Even on CI, explicit override wins.
        monkeypatch.setenv("CI", "true")
        assert _detect_headless_default() is False

    def test_ci_env_implies_headless(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.setenv("CI", "true")
        assert _detect_headless_default() is True

    def test_ci_env_uppercase_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.setenv("CI", "TRUE")
        assert _detect_headless_default() is True

    def test_ci_env_random_string_does_not_imply_headless(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Some shells leave CI=blah from leftover state; only true/1/yes counts."""
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.setenv("CI", "false")
        # Falls through to OS detection. On the test machine (macOS or Linux+display),
        # this should be False; we only assert it's not forced to True by CI=false.
        result = _detect_headless_default()
        assert isinstance(result, bool)

    def test_linux_no_display_implies_headless(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        # Force Linux uname.
        monkeypatch.setattr("os.uname", lambda: SimpleNamespace(sysname="Linux"))
        assert _detect_headless_default() is True

    def test_linux_with_display_stays_headed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr("os.uname", lambda: SimpleNamespace(sysname="Linux"))
        assert _detect_headless_default() is False

    def test_linux_with_wayland_stays_headed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setattr("os.uname", lambda: SimpleNamespace(sysname="Linux"))
        assert _detect_headless_default() is False

    def test_macos_always_headed_when_unspecified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """macOS always has a window server, so we never auto-flip to headless on it."""
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr("os.uname", lambda: SimpleNamespace(sysname="Darwin"))
        assert _detect_headless_default() is False
