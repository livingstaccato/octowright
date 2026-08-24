# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Module-scope counter proving a disabled plugin is never imported."""

from __future__ import annotations

IMPORTS = 0
IMPORTS += 1

MARKER = "imported"
