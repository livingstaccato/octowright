# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Derive the durable-write sinks of the macro privacy surface (#248, Part 0).

Every place macro execution, artifacts and the failure diagnostics write
something that outlives the call -- a file, a recording row, a screenshot, a
bundle -- is a place a classified value could land. Such a place is reviewed
once, for what it scrubs, and then frozen in ``tests/fixtures/privacy_sinks.json``;
``tests/test_privacy_sinks_gate.py`` fails on any sink added or removed
without updating the fixture, in both directions, so a new write cannot ship
unreviewed and a stale row cannot hide one.

A row is ``(module, enclosing function, callee, shape)`` -- no line numbers,
so moving code does not churn the fixture, and a second call of the same kind
in the same function is the same row. ``shape`` is how the callee is reached:
``call`` for a plain name, ``.<receiver>`` for a method on a named receiver
(its last attribute, ``self._recorder.record`` -> ``._recorder``), ``.<expr>``
for any other receiver, and ``open:<mode>`` for ``open``/``Path.open`` with a
writing mode. ALL-CAPS receivers are metrics (``_MACRO_RUN.add``), not writes.

Run with ``--write`` to regenerate the fixture after reviewing the change.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
FIXTURE = ROOT / "tests" / "fixtures" / "privacy_sinks.json"

#: What is scanned: macro execution and storage, the artifact store and the
#: exported CLI's renderer, and the session producer a failed run's
#: diagnostics come from.
SCANNED = (
    "octowright/macros",
    "octowright/artifacts",
    "octowright/session/core_ops_mixin.py",
)

#: Calls that write durably, directly or by delegating to a writer above.
SINK_CALLEES = frozenset(
    {
        # files
        "write_text",
        "write_bytes",
        "atomic_write_text",
        "atomic_write_bytes",
        "atomic_write_via_writer",
        "_json_write",
        "dump",
        "copy",
        "copy2",
        "copyfile",
        "move",
        "save_as",
        # recordings and pixels
        "record",
        "record_control",
        "screenshot",
        "diagnostic_bundle",
        # the artifact and macro writers, so who reaches them is frozen too
        "write_artifact_manifest",
        "write_run_bundle",
        "refresh_run_summary",
        "macro_artifact_verify",
        "write_macro_cli",
        "write_macro",
        "save_macro",
    }
)

_WRITE_MODES = frozenset("wax")


def _files() -> Iterator[Path]:
    for entry in SCANNED:
        path = SRC / entry
        if path.is_file():
            yield path
        else:
            yield from sorted(path.rglob("*.py"))


def _module(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts)


def _open_mode(node: ast.Call) -> str | None:
    """The mode of an ``open(...)``/``path.open(...)`` call, when it is a literal."""
    is_builtin = isinstance(node.func, ast.Name)
    positional = 1 if is_builtin else 0
    candidates = [kw.value for kw in node.keywords if kw.arg == "mode"]
    if len(node.args) > positional:
        candidates.append(node.args[positional])
    for value in candidates:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return "r" if not candidates else "?"


def _shape(node: ast.Call, callee: str) -> str | None:
    if callee == "open":
        mode = _open_mode(node)
        if mode is None or not (_WRITE_MODES & set(mode) or mode == "?"):
            return None
        return f"open:{mode}"
    func = node.func
    if isinstance(func, ast.Name):
        return "call"
    assert isinstance(func, ast.Attribute)
    receiver = func.value
    if isinstance(receiver, ast.Attribute):
        name = receiver.attr
    elif isinstance(receiver, ast.Name):
        name = receiver.id
    else:
        return ".<expr>"
    if name.lstrip("_").isupper():
        return None  # a metric instrument, not a write
    return f".{name}"


class _Scanner(ast.NodeVisitor):
    def __init__(self, module: str) -> None:
        self.module = module
        self.scope: list[str] = []
        self.rows: set[tuple[str, str, str, str]] = set()

    def _scoped(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scoped(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        callee = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
        if callee is not None and (callee in SINK_CALLEES or callee == "open"):
            shape = _shape(node, callee)
            if shape is not None:
                self.rows.add((self.module, ".".join(self.scope) or "<module>", callee, shape))
        self.generic_visit(node)


def derive() -> list[dict[str, str]]:
    rows: set[tuple[str, str, str, str]] = set()
    for path in _files():
        scanner = _Scanner(_module(path))
        scanner.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        rows |= scanner.rows
    return [
        {"module": module, "function": function, "callee": callee, "shape": shape}
        for module, function, callee, shape in sorted(rows)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help=f"rewrite {FIXTURE.relative_to(ROOT)}")
    args = parser.parse_args(argv)
    rows = derive()
    if args.write:
        FIXTURE.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(rows)} sinks to {FIXTURE.relative_to(ROOT)}")
        return 0
    json.dump(rows, sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
