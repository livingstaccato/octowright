# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pytest configuration shared across the suite.

The `octowright_demos` package lives under `tools/` (not under `src/octowright/`)
so demo-generation tooling never ships in the wheel. Tests that import it need
`tools/` on sys.path; this conftest does that once for the whole suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
