# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path


def test_duplicate_browser_pool_runtime_is_removed() -> None:
    assert not Path("src/octowright/browser_pool/runtime.py").exists()
