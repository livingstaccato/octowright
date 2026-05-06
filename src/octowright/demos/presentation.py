# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

VALID_PRESENTATION_MODES = {
    "single-clean",
    "sync-multi",
}


def validate_presentation_mode(mode: str) -> None:
    if mode not in VALID_PRESENTATION_MODES:
        choices = ", ".join(sorted(VALID_PRESENTATION_MODES))
        raise ValueError(f"presentation.mode must be one of: {choices}")
