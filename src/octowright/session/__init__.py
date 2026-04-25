# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""BrowserSession + per-feature helper modules.

Public API: ``BrowserSession`` and ``DEFAULT_PREVIEW_CHARS`` are re-exported
from this package so existing imports (`from octowright.session import
BrowserSession`) continue to work after the split.
"""

from __future__ import annotations

from .core import DEFAULT_PREVIEW_CHARS, BrowserSession

__all__ = ["DEFAULT_PREVIEW_CHARS", "BrowserSession"]
