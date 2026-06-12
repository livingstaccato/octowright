# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Idle-watchdog grace resolution: disabled by default, opt-in via env/flags.

Covers the two pure helpers that decide whether (and with what grace) the idle
watchdog arms:

- ``defaults._parse_idle_grace`` — parses ``OCTOWRIGHT_IDLE_GRACE``.
- ``serve._resolve_watchdog_grace`` — folds ``--keep-alive`` / ``--idle-grace``
  / the env default into the effective grace (or None to disable).
"""

from __future__ import annotations

import pytest

from octowright.defaults import _parse_idle_grace
from octowright.idle_watchdog import _resolve_watchdog_grace


class TestParseIdleGrace:
    def test_unset_is_disabled(self) -> None:
        assert _parse_idle_grace(None) is None

    @pytest.mark.parametrize("raw", ["", "  ", "0", "0.0", "off", "never", "none", "disabled", "OFF", " Never "])
    def test_sentinels_and_blanks_disable(self, raw: str) -> None:
        assert _parse_idle_grace(raw) is None

    @pytest.mark.parametrize("raw", ["-1", "-300", "-0.5"])
    def test_non_positive_disables(self, raw: str) -> None:
        assert _parse_idle_grace(raw) is None

    @pytest.mark.parametrize("raw", ["abc", "30s", "5m", "nan_value"])
    def test_unparseable_disables(self, raw: str) -> None:
        assert _parse_idle_grace(raw) is None

    def test_positive_int_string_enables(self) -> None:
        assert _parse_idle_grace("300") == 300.0

    def test_positive_float_string_enables(self) -> None:
        assert _parse_idle_grace("120.5") == 120.5

    def test_surrounding_whitespace_tolerated(self) -> None:
        assert _parse_idle_grace("  90  ") == 90.0


class TestResolveWatchdogGrace:
    def test_keep_alive_forces_disabled_even_with_grace(self) -> None:
        assert _resolve_watchdog_grace(keep_alive=True, idle_grace=300.0, env_default=300.0) is None

    def test_default_nothing_set_is_disabled(self) -> None:
        assert _resolve_watchdog_grace(keep_alive=False, idle_grace=None, env_default=None) is None

    def test_explicit_idle_grace_wins_over_env(self) -> None:
        assert _resolve_watchdog_grace(keep_alive=False, idle_grace=42.0, env_default=300.0) == 42.0

    def test_env_default_used_when_no_explicit_grace(self) -> None:
        assert _resolve_watchdog_grace(keep_alive=False, idle_grace=None, env_default=120.0) == 120.0

    def test_non_positive_explicit_grace_disables(self) -> None:
        assert _resolve_watchdog_grace(keep_alive=False, idle_grace=0.0, env_default=300.0) is None
        assert _resolve_watchdog_grace(keep_alive=False, idle_grace=-5.0, env_default=300.0) is None
