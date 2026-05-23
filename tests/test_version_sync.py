# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import tomllib
from pathlib import Path

from octowright.version import VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]


def _version_file_value() -> str:
    return (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


def test_runtime_version_matches_version_file() -> None:
    """``octowright.version.VERSION`` must reflect the top-level VERSION file
    (single source of truth across the provide-io ecosystem)."""
    assert _version_file_value() == VERSION


def test_pyproject_declares_dynamic_version_from_version_file() -> None:
    """``pyproject.toml`` must defer to the VERSION file rather than hardcoding
    the version, so a single edit ripples through wheel + sdist + runtime."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in data["project"].get("dynamic", []), "project.version should be dynamic"
    hatch_version = data["tool"]["hatch"]["version"]
    assert hatch_version["source"] == "regex"
    assert hatch_version["path"] == "VERSION"
