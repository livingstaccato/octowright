# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-level constants shared between :mod:`session.core` and the mixin
modules under :mod:`session`.

This sub-module exists purely to break the import cycle that would otherwise
form if a mixin (e.g. ``session.core_ops_mixin``) needed to import a constant
from ``session.core`` while ``session.core`` already imports the mixin to
build its dataclass. Keeping the constant in a leaf module that neither side
of the cycle depends on keeps both imports cheap and side-effect free.
"""

from __future__ import annotations

# Maximum characters returned in inline HTML/text previews from session
# diagnostic surfaces (e.g. ``diagnostic_bundle``'s ``html_preview``). Re-
# exported via :mod:`octowright.session` and ``octowright.session.core``.
DEFAULT_PREVIEW_CHARS = 4000

# Console levels that explain a failure, as reported verbatim by the engine.
# Firefox spells console.warn's level "warn" where Chromium says "warning",
# and casing is not guaranteed, so match case-insensitively across both.
# Canonical: ``capture_summaries``, ``server.browser.inspect_console`` and
# ``session.core_ops_mixin`` all classified console levels independently and
# had already drifted -- the diagnostic-tail selector missed Firefox warnings
# because of it.
DIAGNOSTIC_CONSOLE_LEVELS = frozenset({"error", "warning", "warn"})


def is_diagnostic_console_message(message: object) -> bool:
    """Whether a console entry is one that explains a failure.

    Tolerates a non-dict entry: callers run this while another failure is
    being reported, so it must never be the thing that raises.
    """
    if not isinstance(message, dict):
        return False
    return str(message.get("level", "")).lower() in DIAGNOSTIC_CONSOLE_LEVELS
