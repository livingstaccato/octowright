# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A refused headed-default close teaches the caller how to proceed."""

from __future__ import annotations

from octowright.browser_pool.lifecycle import _protected_close_message  # pure helper (Step 3)


def test_headed_default_message_mentions_force_and_relaunch():
    msg = _protected_close_message("browser-1", "headed_default")
    assert "force=True" in msg
    assert "protected=False" in msg
    assert "headed" in msg.lower()


def test_explicit_message_is_generic():
    msg = _protected_close_message("browser-1", "explicit")
    assert "force=True" in msg
