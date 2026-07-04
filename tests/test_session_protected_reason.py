# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""BrowserSession exposes protected_reason (defaults to 'explicit')."""

from __future__ import annotations

from dataclasses import fields

from octowright.session.core import BrowserSession


def test_browser_session_has_protected_reason_field():
    names = {f.name for f in fields(BrowserSession)}
    assert "protected_reason" in names
    reason_field = next(f for f in fields(BrowserSession) if f.name == "protected_reason")
    assert reason_field.default == "explicit"
