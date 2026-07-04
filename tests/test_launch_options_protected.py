# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""LaunchOptions carries an unset `protected` sentinel through from_mapping."""

from __future__ import annotations

from octowright.browser_pool.options import LaunchOptions


def test_protected_absent_is_none_sentinel():
    opts = LaunchOptions.from_mapping({"kind": "chromium"})
    assert opts.protected is None
    assert opts.protected_reason == "explicit"


def test_protected_explicit_true_carries_through():
    opts = LaunchOptions.from_mapping({"kind": "chromium", "protected": True})
    assert opts.protected is True


def test_protected_explicit_false_carries_through():
    opts = LaunchOptions.from_mapping({"kind": "chromium", "protected": False})
    assert opts.protected is False
