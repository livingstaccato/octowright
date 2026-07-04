# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The protect-by-default resolver (`browser_pool/options.resolve_protected`)."""

from __future__ import annotations

import pytest

from octowright import defaults
from octowright.browser_pool.options import resolve_protected


@pytest.mark.parametrize(
    ("explicit", "headed", "ephemeral", "protect_all", "protect_headed", "exp_protected", "exp_reason"),
    [
        (True, False, False, False, False, True, "explicit"),
        (False, True, False, True, True, False, "explicit"),
        (None, True, False, True, False, True, "all_default"),
        (None, True, False, False, True, True, "headed_default"),
        (None, True, True, False, True, False, "unprotected"),  # ephemeral headed opts out
        (None, False, False, False, True, False, "unprotected"),  # headless never
        (None, True, False, False, False, False, "unprotected"),  # protect_headed off
    ],
)
def test_resolve_protected_matrix(
    monkeypatch, explicit, headed, ephemeral, protect_all, protect_headed, exp_protected, exp_reason
):
    monkeypatch.setattr(defaults, "PROTECT_BROWSERS_DEFAULT", protect_all)
    monkeypatch.setattr(defaults, "PROTECT_HEADED_DEFAULT", protect_headed)
    assert resolve_protected(explicit, headed=headed, ephemeral=ephemeral) == (exp_protected, exp_reason)
