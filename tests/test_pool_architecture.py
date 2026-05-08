# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from pathlib import Path


def test_duplicate_browser_pool_runtime_is_removed() -> None:
    assert not Path("src/octowright/browser_pool/runtime.py").exists()


def test_browser_pool_implementation_is_consolidated_under_package() -> None:
    assert Path("src/octowright/browser_pool/pool.py").exists()
    assert not Path("src/octowright/pool.py").exists()
    assert not Path("src/octowright/pool_support.py").exists()
    assert not Path("src/octowright/pool_roster.py").exists()
