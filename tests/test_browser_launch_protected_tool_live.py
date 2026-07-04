# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""browser_launch defaults protected to None (pool decides) → headed protected."""

from __future__ import annotations

import inspect

from octowright.server.browser import lifecycle


def test_browser_launch_protected_default_is_none():
    sig = inspect.signature(lifecycle.browser_launch)
    assert sig.parameters["protected"].default is None


def test_browser_quick_launch_protected_default_is_none():
    sig = inspect.signature(lifecycle.browser_quick_launch)
    assert sig.parameters["protected"].default is None
