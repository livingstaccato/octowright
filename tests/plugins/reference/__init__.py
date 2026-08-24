# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A deliberately partial reference session-kind plugin.

It exists so every seam of the plugin API has a consumer inside core CI
without core depending on a third-party package. Partial on purpose: it
declares fewer capabilities than browsers do, so the skip paths are exercised
rather than only the happy path.
"""

from __future__ import annotations
