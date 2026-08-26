# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import glob
import tarfile
import zipfile

REQUIRED_PACKAGE_FILES = {
    "octowright/skills/octowright/SKILL.md",
    "octowright/skills/octowright/skill.json",
    "octowright/skills/manifests/claude-plugin.json",
    "octowright/skills/manifests/codex-plugin.json",
    # Built SPA — the wheel and sdist must include the dashboard the HTTP app
    # serves at "/", or packaged installs ship a server with no UI.
    "octowright/server/frontend/index.html",
    "octowright/server/frontend/index.js",
    "octowright/server/frontend/session.html",
    "octowright/server/frontend/session.js",
    "octowright/server/frontend/dashboard-media-sw.js",
    # Main dashboard stylesheet (renamed from the old format.css during the
    # frontend rework). There is deliberately no terminal entry here: the
    # xterm view left core's bundle when terminal became a session-kind
    # plugin, and its renderer (styling inlined) now ships in the separate
    # octowright-terminal distribution -- see AGENTS.md "Terminal Sessions
    # (plugin)". A stale session-terminal.css entry here failed this gate on
    # every build after that removal.
    "octowright/server/frontend/styles.css",
}


def main() -> None:
    wheels = glob.glob("dist/*.whl")
    if not wheels:
        raise SystemExit("missing wheel artifact")

    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    missing = sorted(REQUIRED_PACKAGE_FILES - names)
    if missing:
        raise SystemExit(f"wheel missing required files: {missing}")

    sdists = glob.glob("dist/*.tar.gz")
    if not sdists:
        raise SystemExit("missing sdist artifact")
    with tarfile.open(sdists[0], "r:gz") as tf:
        sdist_names = {
            normalised for member in tf.getmembers() if (normalised := _normalise_sdist_member(member.name)) is not None
        }
    sdist_missing = sorted(REQUIRED_PACKAGE_FILES - sdist_names)
    if sdist_missing:
        raise SystemExit(f"sdist missing required files: {sdist_missing}")


def _normalise_sdist_member(name: str) -> str | None:
    """Strip the ``octowright-<version>/`` root and optional ``src/`` prefix so
    members compare against ``REQUIRED_PACKAGE_FILES``. Returns ``None`` for
    archive entries that don't fit that layout (e.g., the root directory
    itself, PAX headers, stray symlinks) so they don't silently map to ``""``
    and mask a real missing file."""
    parts = name.split("/")
    if len(parts) < 2 or not parts[1]:
        return None
    without_root = "/".join(parts[1:])
    if without_root.startswith("src/"):
        return without_root.removeprefix("src/")
    return without_root


if __name__ == "__main__":
    main()
