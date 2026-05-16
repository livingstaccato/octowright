# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Build the GitHub Actions test-matrix line for the ``select-test-matrix`` job.

Reads ``TARGET_OS`` and ``TARGET_ARCH`` from the environment (defaulting to
``"all"``) and prints a single ``matrix=<json>`` line on stdout for the
calling step to append to ``$GITHUB_OUTPUT``.

Lives in ``ci/`` rather than inline in the workflow because CLAUDE.md
prohibits ``run:`` blocks longer than three lines.
"""

from __future__ import annotations

import json
import os
import sys

# Source of truth for which os/arch combinations the test job runs on. Update
# here (not in ci.yml) when adding or retiring a runner image.
_TARGETS: tuple[dict[str, str], ...] = (
    {"os": "linux", "arch": "amd64", "runner": "ubuntu-24.04"},
    {"os": "linux", "arch": "arm64", "runner": "ubuntu-24.04-arm"},
    {"os": "macos", "arch": "amd64", "runner": "macos-15-intel"},
    {"os": "macos", "arch": "arm64", "runner": "macos-15"},
    {"os": "windows", "arch": "amd64", "runner": "windows-2025"},
    {"os": "windows", "arch": "arm64", "runner": "windows-11-arm"},
)


def build_matrix(target_os: str, target_arch: str) -> dict[str, list[dict[str, str]]]:
    include = [
        target for target in _TARGETS if target_os in {"all", target["os"]} and target_arch in {"all", target["arch"]}
    ]
    return {"include": include}


def main() -> int:
    target_os = os.environ.get("TARGET_OS") or "all"
    target_arch = os.environ.get("TARGET_ARCH") or "all"
    payload = build_matrix(target_os, target_arch)
    print(f"matrix={json.dumps(payload, separators=(',', ':'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
