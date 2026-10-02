# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Where the dashboard's compiled bundle lives, and how to build it.

At the package root, stdlib only, so ``octowright doctor`` can check for the
bundle without importing the HTTP stack (``octowright.http`` is ~800 modules).
The bundle is gitignored build output: a wheel ships it (``release.yml``
builds it first), while every editable install and every fresh worktree of a
source checkout starts without it.
"""

from __future__ import annotations

from pathlib import Path

#: The directory ``npm run build`` writes and the daemon serves at ``/``.
FRONTEND_DIR = Path(__file__).parent / "server" / "frontend"

#: How to build the bundle, from a source checkout's root.
BUILD_COMMAND = "npm ci && npm run build --workspace=packages/octowright-frontend"
