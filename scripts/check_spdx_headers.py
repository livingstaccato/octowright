#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Validate canonical SPDX headers on Python and Go source files.

Beyond checking header *presence*, this script also parses each file's
`SPDX-License-Identifier:` line and rejects any value that is not in the
ALLOWED_LICENSE_IDENTIFIERS allowlist. This catches typos such as
`Apache-2-0` that would otherwise silently pass a presence-only check.
"""
# REUSE-IgnoreEnd

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from spdx_headers import EXCLUDED_DIRS as _BASE_EXCLUDED_DIRS  # noqa: E402
from spdx_headers import has_go_canonical_header as _has_go_spdx_header  # noqa: E402

_EXCLUDED_DIRS = _BASE_EXCLUDED_DIRS | {"vendor"}

# Allowlist of SPDX license identifiers that are valid for this repository.
# Keep this list small and explicit — adding a new identifier is a policy
# decision, not an accident to fix by widening the allowlist.
ALLOWED_LICENSE_IDENTIFIERS: frozenset[str] = frozenset({"Apache-2.0"})

# REUSE-IgnoreStart
# Matches e.g. `# SPDX-License-Identifier: Apache-2.0` or `// SPDX-License-Identifier: Apache-2.0`.
_SPDX_LICENSE_LINE = re.compile(r"SPDX-License-Identifier:\s*(\S+)")
# REUSE-IgnoreEnd


def _find_go_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*.go"):
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        files.append(path)
    return sorted(files)


# REUSE-IgnoreStart
def extract_license_identifier(text: str) -> str | None:
    """Return the SPDX license identifier from the first matching header line.

    Returns None if no `SPDX-License-Identifier:` line is present.
    """
    match = _SPDX_LICENSE_LINE.search(text)
    return match.group(1) if match else None


# REUSE-IgnoreEnd


def validate_license_identifier(text: str) -> tuple[bool, str | None]:
    """Validate the SPDX license identifier in `text`.

    Returns (is_valid, identifier_or_none). When the identifier is missing
    entirely, returns (False, None). When present but not in the allowlist,
    returns (False, <the bad value>). When present and valid, (True, value).
    """
    identifier = extract_license_identifier(text)
    if identifier is None:
        return False, None
    return identifier in ALLOWED_LICENSE_IDENTIFIERS, identifier


def find_noncompliant_files(root: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Scan `root` and return (missing_header_files, invalid_identifier_files).

    `invalid_identifier_files` contains (path, bad_identifier) pairs.
    """
    from spdx_headers import find_python_files, has_canonical_header

    missing: list[Path] = []
    invalid: list[tuple[Path, str]] = []

    def _check(path: Path, has_header: bool) -> None:
        text = path.read_text(encoding="utf-8")
        if not has_header:
            missing.append(path)
            return
        ok, identifier = validate_license_identifier(text)
        if not ok:
            invalid.append((path, identifier or "<missing>"))

    for path in find_python_files(root):
        text = path.read_text(encoding="utf-8")
        _check(path, has_canonical_header(text))
    for path in _find_go_files(root):
        text = path.read_text(encoding="utf-8")
        _check(path, _has_go_spdx_header(text))
    return missing, invalid


def main() -> int:
    parser = argparse.ArgumentParser(description="Check canonical SPDX headers (Python + Go).")
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()

    missing, invalid = find_noncompliant_files(args.root.resolve())
    if missing or invalid:
        if missing:
            print(f"SPDX header check failed: {len(missing)} file(s) missing canonical header.")
            for path in missing:
                print(f"  {path}")
        if invalid:
            allowed = ", ".join(sorted(ALLOWED_LICENSE_IDENTIFIERS))
            print(f"SPDX header check failed: {len(invalid)} file(s) use a disallowed SPDX license identifier.")
            print(f"  allowed identifiers: {allowed}")
            for path, bad in invalid:
                print(f"  {path}: {bad!r}")
        print("Run: uv run python scripts/normalize_spdx_headers.py")
        return 1
    print("SPDX header check passed: all Python and Go files are compliant.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
