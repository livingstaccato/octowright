#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pre-commit guard that every git-tracked plugin manifest's ``version``
matches the project's ``VERSION`` file.

All three plugin manifests are checked in so directory-based installs
(``claude marketplace add . / codex plugin install . / agy plugin
install /path/to/octowright``) work from a fresh checkout. The on-disk
copies are static defaults — the install-time pipeline still uses the
``{version}``-substituting templates under
``src/octowright/skills/manifests/`` to write a fresh manifest into the
user's plugin store, but the repo-root copies have no substitution and
silently drift unless bumped when ``VERSION`` bumps.

This check catches the drift in CI / pre-commit so the next directory
install against a checkout doesn't report a stale version.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PLUGIN_MANIFESTS = (
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
    ".antigravity-plugin/plugin.json",
)


def main() -> int:
    version_file = REPO_ROOT / "VERSION"
    if not version_file.exists():
        print(f"error: {version_file} missing", file=sys.stderr)
        return 1

    expected = version_file.read_text().strip()
    if not expected:
        print(f"error: {version_file} is empty", file=sys.stderr)
        return 1

    failures: list[str] = []
    for rel in PLUGIN_MANIFESTS:
        path = REPO_ROOT / rel
        if not path.exists():
            failures.append(f"{rel}: missing")
            continue
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            failures.append(f"{rel}: invalid JSON: {exc}")
            continue
        actual = data.get("version")
        if actual != expected:
            failures.append(f"{rel}: version={actual!r} but VERSION={expected!r}")

    if failures:
        print("plugin manifest version mismatch:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print(
            f"\nbump each plugin.json 'version' field to match VERSION ({expected!r}) and re-commit.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
