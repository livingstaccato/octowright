#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every injected browser asset must be reached by the type-check config.

``ci/js-typecheck/tsconfig.json`` selects its inputs with a glob. A glob that
stops matching does not fail -- ``tsc`` reports success having checked nothing,
so the gate goes green while covering zero files. That is the same failure
shape as a platform-gated test that silently skips, and it is worth a guard
precisely because the passing output is identical either way (biome exhibited
it against this very directory: "Checked 0 files ... No files were processed").

Pure Python and no node, so it runs inside ``make lint`` alongside the other
doc/config guards rather than only in the frontend CI job.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TSCONFIG = REPO_ROOT / "ci" / "js-typecheck" / "tsconfig.json"
ASSETS_DIR = REPO_ROOT / "src" / "octowright" / "browser_pool" / "_assets"

# tsconfig.json permits comments and this one uses a "//" key for its rationale;
# strip line comments before parsing rather than depending on a JSON5 library.
_LINE_COMMENT = re.compile(r"^\s*//.*$", re.MULTILINE)


def _load_includes() -> list[str]:
    raw = _LINE_COMMENT.sub("", TSCONFIG.read_text(encoding="utf-8"))
    config = json.loads(raw)
    includes = config.get("include")
    if not isinstance(includes, list) or not includes:
        raise SystemExit(f"{TSCONFIG} declares no 'include' list")
    if config.get("compilerOptions", {}).get("noEmit") is not True:
        # The config reads sources that live in the shipped package; emitting
        # would write compiled output next to them.
        raise SystemExit(f"{TSCONFIG} must set compilerOptions.noEmit=true")
    return [str(item) for item in includes]


def main() -> int:
    if not ASSETS_DIR.is_dir():
        raise SystemExit(f"asset directory not found: {ASSETS_DIR}")

    assets = sorted(ASSETS_DIR.glob("*.js"))
    if not assets:
        raise SystemExit(f"no .js assets found in {ASSETS_DIR} -- did they move?")

    base = TSCONFIG.parent
    covered: set[Path] = set()
    for pattern in _load_includes():
        for match in base.glob(pattern):
            covered.add(match.resolve())

    missed = [asset for asset in assets if asset.resolve() not in covered]
    if missed:
        print("These injected assets are NOT type-checked:", file=sys.stderr)
        for asset in missed:
            print(f"    {asset.relative_to(REPO_ROOT)}", file=sys.stderr)
        print(
            f"\nAdd them to the 'include' globs in {TSCONFIG.relative_to(REPO_ROOT)}.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: all {len(assets)} injected browser assets are covered by the type-check config")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
