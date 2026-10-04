# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-operation destinations for tool-chosen write paths under the recordings root.

Each policy is ``_paths.checked_write_target`` with the suffixes that operation
produces and the subdirectories it may not write into. The script export's own
policy lives beside it in ``export.checked_export_target``.
"""

from __future__ import annotations

from pathlib import Path

from octowright._paths import checked_write_target
from octowright.recording_cleanup import TOOL_WRITE_FORBIDDEN_SUBDIRS

#: What ``session.screenshot`` writes: it picks JPEG for a ``.jpg``/``.jpeg``
#: suffix and PNG for anything else.
SCREENSHOT_SUFFIXES = (".png", ".jpg", ".jpeg")


def checked_screenshot_target(target: Path, root: Path) -> Path:
    """``target`` resolved, if a screenshot may replace what is there.

    Used by ``browser_screenshot`` and ``browser_capture_and_close``.
    """
    return checked_write_target(
        target,
        root,
        label="screenshot path",
        allowed_suffixes=SCREENSHOT_SUFFIXES,
        forbidden_subdirs=TOOL_WRITE_FORBIDDEN_SUBDIRS,
    )


def checked_macro_export_target(target: Path, root: Path) -> Path:
    """``target`` resolved, if ``macro_export_cli`` may write its script there.

    Unlike a session's script export, a macro's CLI script belongs under
    ``artifacts/`` -- its default is ``artifacts/macros/<name>/exports/`` and a
    relative ``out_path`` is taken from ``artifacts/`` -- so only plugins'
    ``session-artifacts/`` is closed to it. The ``.py`` suffix is what keeps it
    off a recording or an ``artifact.json``.
    """
    return checked_write_target(
        target,
        root,
        label="macro export path",
        allowed_suffixes=(".py",),
        forbidden_subdirs=tuple(d for d in TOOL_WRITE_FORBIDDEN_SUBDIRS if d != "artifacts"),
    )
