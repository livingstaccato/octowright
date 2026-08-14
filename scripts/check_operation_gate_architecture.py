# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Static architecture gate: every Playwright access must run under a named,
literal operation boundary -- a ``@gated_operation("name")`` decorator, a
literal ``async with <session>.operation("name"):``, the reserved
``close_operation(...)`` teardown body, or the ``browser_operation(pool,
instance_id, "name")`` complete-workflow boundary -- or be explicitly
classified into one of four narrow, reasoned bypass categories.

Deliberately NOT call-graph analysis: ``operation_name`` must always be a
source-code string literal, which is what lets this scanner prove coverage
per-function from syntax alone. See ``session/operation_gate.py`` and
``server/browser/_operation.py`` for the runtime side of this contract.

The scanner itself is split across sibling modules (kept under the
repository's LOC-per-file convention): ``_operation_gate_constants.py``
(attribute/name vocabularies), ``_operation_gate_ast.py`` (AST pattern
recognition), ``_operation_gate_scanner.py`` (the per-file traversal engine),
and ``_operation_gate_inventory.py`` (the BYPASSES / OPERATION_NAME_FORWARDERS
data). This module wires them together behind ``scan_paths`` and the CLI.
"""

from __future__ import annotations

import ast
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

# Sibling modules are imported as ``scripts.X`` below, which requires the
# repo root (parent of this directory) on sys.path. pytest already puts it
# there (tests/__init__.py makes pytest walk up to the first
# non-package directory), but a direct ``python scripts/check_....py``
# invocation -- the CLI form the Makefile actually uses -- only puts THIS
# file's own directory on sys.path, so the package-qualified imports below
# would otherwise fail with "No module named 'scripts'".
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._operation_gate_constants import APPROVED_BYPASS_CLASSES, EXCLUDED_DIR_NAMES  # noqa: E402
from scripts._operation_gate_constants import PLAYWRIGHT_CHAIN_ATTRS as PLAYWRIGHT_CHAIN_ATTRS  # noqa: E402
from scripts._operation_gate_constants import PLAYWRIGHT_ROOT_ATTRS as PLAYWRIGHT_ROOT_ATTRS  # noqa: E402
from scripts._operation_gate_inventory import BYPASSES, OPERATION_NAME_FORWARDERS  # noqa: E402
from scripts._operation_gate_scanner import FileScanner, Violation, _FuncRecord  # noqa: E402

__all__ = [
    "APPROVED_BYPASS_CLASSES",
    "BYPASSES",
    "OPERATION_NAME_FORWARDERS",
    "PLAYWRIGHT_CHAIN_ATTRS",
    "PLAYWRIGHT_ROOT_ATTRS",
    "BypassInventoryError",
    "Violation",
    "main",
    "scan_paths",
]

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "octowright"

# Sanity floor for the CLI's own scan of SRC (currently ~200 files, excluding
# the optional terminal/ extra). Well below that so ordinary tree growth or
# shrinkage never trips it, but high enough that a near-empty scan -- e.g. an
# ancestor directory collision with EXCLUDED_DIR_NAMES, or SRC not existing
# -- fails loudly instead of a false "OK" from having scanned nothing.
_MIN_EXPECTED_SRC_FILES = 50


class BypassInventoryError(ValueError):
    """A bypass or forwarder inventory entry is stale, malformed, or unproven."""


def _expand_files(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            for candidate in sorted(path.rglob("*.py")):
                # Exclusion must be checked against the path RELATIVE TO THE
                # SCANNED ROOT, not the absolute path -- checking absolute
                # parts means an ancestor directory that happens to be named
                # "terminal" (e.g. a checkout under ~/src/terminal/octowright)
                # silently excludes every file in the entire scan, and the
                # CLI would report a false "OK" having scanned nothing.
                rel_parts = candidate.relative_to(path).parts
                if not any(part in EXCLUDED_DIR_NAMES for part in rel_parts):
                    files.append(candidate)
        else:
            files.append(path)
    return sorted(set(files))


def _resolve_root(files: Sequence[Path]) -> Path:
    bases = [str(f.resolve().parent) for f in files]
    if not bases:
        return Path.cwd()
    return Path(os.path.commonpath(bases))


def _relative_key(file: Path, root: Path) -> str:
    return str(file.resolve().relative_to(root.resolve())).replace(os.sep, "/")


def scan_paths(
    paths: Sequence[Path],
    *,
    bypasses: Mapping[str, tuple[str, str]],
    forwarders: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> list[Violation]:
    if forwarders is None:
        forwarders = OPERATION_NAME_FORWARDERS

    files = _expand_files(paths)
    effective_root = root if root is not None else _resolve_root(files)

    file_paths: dict[str, Path] = {}
    all_records: dict[str, _FuncRecord] = {}
    scanned_relative_files: set[str] = set()
    for file in files:
        rel = _relative_key(file, effective_root)
        scanned_relative_files.add(rel)
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        scanner = FileScanner(rel, tree)
        for key, record in scanner.records.items():
            all_records[key] = record
            file_paths[key] = file

    def _path_for(key: str) -> Path:
        rel = key.split(":", 1)[0]
        return file_paths.get(key, effective_root / rel)

    def _function_for(key: str) -> str:
        return key.split(":", 1)[1]

    def _file_in_scope(key: str) -> bool:
        # A caller (typically a unit test) may scan only a narrow slice of the
        # tree while still relying on the *default* module-level BYPASSES /
        # OPERATION_NAME_FORWARDERS constants. An entry whose file was never
        # part of this scan is inapplicable, not stale -- only entries whose
        # file WAS scanned are held to the "must match a real hit" ratchet.
        return key.split(":", 1)[0] in scanned_relative_files

    # Validate the forwarder inventory (self-referential config, fail fast).
    for key, reason in forwarders.items():
        if not _file_in_scope(key):
            continue
        if not reason or not reason.strip():
            raise BypassInventoryError(f"operation-name forwarder {key!r} has an empty reason")
        forwarder_record = all_records.get(key)
        if forwarder_record is None:
            raise BypassInventoryError(f"operation-name forwarder {key!r} was not found among scanned functions")
        if len(forwarder_record.dynamic_sites) != 1:
            raise BypassInventoryError(
                f"operation-name forwarder {key!r} must contain exactly one dynamic forwarding "
                f"context, found {len(forwarder_record.dynamic_sites)}"
            )
        if forwarder_record.raw_hit_lines:
            raise BypassInventoryError(f"operation-name forwarder {key!r} must contain no detected Playwright access")

    # Validate the bypass inventory (ratchet: every entry must excuse a real hit).
    for key, entry in bypasses.items():
        if not _file_in_scope(key):
            continue
        bypass_class, reason = entry
        if bypass_class not in APPROVED_BYPASS_CLASSES:
            raise BypassInventoryError(f"bypass {key!r} has an unknown class {bypass_class!r}")
        if not reason or not reason.strip():
            raise BypassInventoryError(f"bypass {key!r} has an empty reason")
        bypass_record = all_records.get(key)
        if bypass_record is None:
            raise BypassInventoryError(f"bypass {key!r} does not match any scanned function; entry not found")
        if not bypass_record.raw_hit_lines:
            raise BypassInventoryError(f"bypass {key!r} has no detected Playwright access; stale entry")

    violations: list[Violation] = []

    for key, found_record in all_records.items():
        for line in found_record.dynamic_sites:
            if key in forwarders:
                continue
            violations.append(
                Violation(
                    path=_path_for(key),
                    function=_function_for(key),
                    line=line,
                    detail="dynamic operation name used outside the forwarder allowlist",
                )
            )
        if key in bypasses:
            continue
        for line in found_record.raw_hit_lines:
            violations.append(
                Violation(
                    path=_path_for(key),
                    function=_function_for(key),
                    line=line,
                    detail="ungated Playwright access",
                )
            )

    violations.sort(key=lambda v: (str(v.path), v.line, v.function))
    return violations


def main() -> int:
    files = _expand_files([SRC])
    if len(files) < _MIN_EXPECTED_SRC_FILES:
        print(
            f"Refusing to report a result: only {len(files)} file(s) were scanned under "
            f"{SRC} (expected at least {_MIN_EXPECTED_SRC_FILES}). This usually means SRC "
            "does not exist, or an ancestor directory name collided with the terminal/ "
            "exclusion list and silently excluded the whole tree.",
            file=sys.stderr,
        )
        return 1
    violations = scan_paths(files, bypasses=BYPASSES)
    if violations:
        print("Ungated or unclassified Playwright access found:")
        for item in violations:
            rel = item.path.relative_to(ROOT) if item.path.is_absolute() else item.path
            print(f"  - {rel}:{item.line} in {item.function} ({item.detail})")
        return 1
    print("OK: all detected Playwright access is gated or narrowly classified")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BypassInventoryError as exc:
        print(f"Invalid bypass/forwarder inventory: {exc}", file=sys.stderr)
        sys.exit(1)
