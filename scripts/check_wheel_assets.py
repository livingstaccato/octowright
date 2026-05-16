# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import glob
import zipfile


def main() -> None:
    wheels = glob.glob("dist/*.whl")
    if not wheels:
        raise SystemExit("missing wheel artifact")

    required = {
        "octowright/skills/using-octowright/SKILL.md",
        "octowright/skills/using-octowright/skill.json",
        "octowright/skills/manifests/claude-plugin.json",
        "octowright/skills/manifests/codex-plugin.json",
        # Built SPA — the wheel must include the dashboard the HTTP app
        # serves at "/", or packaged installs ship a server with no UI.
        "octowright/server/frontend/index.html",
        "octowright/server/frontend/index.js",
        "octowright/server/frontend/session.html",
        "octowright/server/frontend/session.js",
        "octowright/server/frontend/format.css",
    }
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    missing = sorted(required - names)
    if missing:
        raise SystemExit(f"wheel missing required files: {missing}")


if __name__ == "__main__":
    main()
