# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Package version constants.

The single source of truth is the top-level ``VERSION`` file (matching the
rest of the provide-io ecosystem). At install time, hatchling reads it and
embeds the value into the package metadata; at import time we prefer that
metadata, falling back to reading the file directly for editable installs
or in-tree usage where metadata may not yet exist.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path


def _read_version() -> str:
    try:
        return _pkg_version("octowright")
    except PackageNotFoundError:
        pass
    # Repo-tree fallback: src/octowright/version.py -> repo root is parents[2].
    version_file = Path(__file__).resolve().parents[2] / "VERSION"
    if version_file.is_file():
        return version_file.read_text(encoding="utf-8").strip()
    return "0.0.0+unknown"


VERSION = _read_version()
__version__ = VERSION
