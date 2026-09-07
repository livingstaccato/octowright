# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Where the currently-running test's id is parked.

Its own module because two processes need the same path and neither should
hardcode it: ``tests/conftest.py`` writes the file, and
``scripts/watch_test_timeline.py`` reads it from outside the pytest process.
Importing ``conftest`` to get one constant would execute the whole plugin.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Git-ignored; rewritten in place rather than appended to, so it always holds
# exactly one line.
CURRENT_TEST_BREADCRUMB = REPO_ROOT / ".pytest-current-test"
