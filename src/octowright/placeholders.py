# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The one ``{{name}}`` placeholder grammar, for macros and scenario templates alike.

What ``macros.substitution.substitute`` expands, what the macro linters and
``macros.privacy`` read argument names with, what an exported macro CLI
substitutes (rendered as the string), and what a scenario template's args
replace. A package-root leaf, like ``console_levels``, so ``scenario_templates``
shares it without importing the macro stack: templates had re-implemented it
with a different character class, and two grammars for one syntax drift.
"""

from __future__ import annotations

import re

PLACEHOLDER_PATTERN = r"\{\{([^}]+)\}\}"
PLACEHOLDER_RE = re.compile(PLACEHOLDER_PATTERN)
