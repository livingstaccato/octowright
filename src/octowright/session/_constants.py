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
