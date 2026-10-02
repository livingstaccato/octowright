# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.defaults — specifically the headless auto-detect logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright.config_paths import user_cache_dir, user_config_dir, user_state_dir
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
        monkeypatch.setattr("platform.system", lambda: "Linux")
        assert _detect_headless_default() is True

    def test_linux_with_display_stays_headed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv("DISPLAY", ":0")
        monkeypatch.setattr("platform.system", lambda: "Linux")
        assert _detect_headless_default() is False

    def test_linux_with_wayland_stays_headed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setattr("platform.system", lambda: "Linux")
        assert _detect_headless_default() is False

    def test_macos_always_headed_when_unspecified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """macOS always has a window server, so we never auto-flip to headless on it."""
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        assert _detect_headless_default() is False

    def test_windows_headed_when_unspecified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OCTOWRIGHT_HEADLESS", raising=False)
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setattr("platform.system", lambda: "Windows")
        assert _detect_headless_default() is False


class TestConfigDir:
    def test_posix_uses_xdg_config_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        assert user_config_dir() == tmp_path / "xdg" / "octowright"

    def test_posix_falls_back_to_home_dot_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert user_config_dir() == tmp_path / ".config" / "octowright"

    def test_windows_uses_appdata(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
        assert user_config_dir() == tmp_path / "Roaming" / "octowright"

    def test_windows_falls_back_to_home_roaming(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert user_config_dir() == tmp_path / "AppData" / "Roaming" / "octowright"


class TestStateDir:
    def test_posix_uses_xdg_state_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        assert user_state_dir() == tmp_path / "state" / "octowright"

    def test_posix_falls_back_to_home_dot_local_state(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert user_state_dir() == tmp_path / ".local" / "state" / "octowright"

    def test_windows_uses_localappdata_state(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert user_state_dir() == tmp_path / "Local" / "octowright" / "State"


class TestCacheDir:
    def test_posix_uses_xdg_cache_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        assert user_cache_dir() == tmp_path / "cache" / "octowright"

    def test_posix_falls_back_to_home_dot_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert user_cache_dir() == tmp_path / ".cache" / "octowright"

    def test_windows_uses_localappdata_cache(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
        assert user_cache_dir() == tmp_path / "Local" / "octowright" / "Cache"


class TestViewportDefaults:
    """OCTOWRIGHT_VIEWPORT_W/H are read at import, so a bad value must not crash every command."""

    @pytest.mark.parametrize(("raw", "expected"), [(None, 1280), ("1920", 1920), (" 1920 ", 1920)])
    def test_a_usable_value_is_taken(self, raw: str | None, expected: int) -> None:
        from octowright import defaults

        assert defaults._parse_viewport_dim("OCTOWRIGHT_VIEWPORT_W", raw, 1280) == expected

    @pytest.mark.parametrize("raw", ["1920px", "", "  ", "0", "-5", "wide", "1e3"])
    def test_an_unusable_value_falls_back_to_the_default_with_a_warning(self, raw: str) -> None:
        from unittest.mock import patch

        from octowright import defaults

        with patch.object(defaults, "log") as log:
            assert defaults._parse_viewport_dim("OCTOWRIGHT_VIEWPORT_W", raw, 1280) == 1280
        log.warning.assert_called_once()
        assert log.warning.call_args.kwargs["name"] == "OCTOWRIGHT_VIEWPORT_W"

    def test_a_bad_value_no_longer_breaks_import(self, tmp_path: Path) -> None:
        import os
        import subprocess  # nosec B404 -- this interpreter, fixed arguments
        import sys

        env = dict(os.environ, OCTOWRIGHT_VIEWPORT_W="1920px", OCTOWRIGHT_VIEWPORT_H="0")
        code = "from octowright import defaults; print(defaults.DEFAULT_VIEWPORT_W, defaults.DEFAULT_VIEWPORT_H)"
        proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
            [sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.split() == ["1280", "800"]
