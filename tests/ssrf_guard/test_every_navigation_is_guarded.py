# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every navigation octowright itself starts goes through ``ssrf_guard.guarded_navigation``.

The begin/goto/raise triple was copied into five call sites and missing from
the sixth (the new-tab redirect's ``goto``). A navigation that skips the
helper reads a refused later hop as a success on chromium and waits out its
timeout on firefox and webkit, so this scans for any that does. Code that is
only a string -- an exported script, the doctor's child-interpreter probe --
is not a call and is not seen.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "octowright"
NAVIGATIONS = frozenset({"goto", "go_back", "go_forward", "reload"})


def _is_guard(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "guarded_navigation")
        or (isinstance(node.func, ast.Name) and node.func.id == "guarded_navigation")
    )


def unguarded_navigations(root: Path) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        guarded = {id(arg) for node in ast.walk(tree) if _is_guard(node) for arg in node.args}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in NAVIGATIONS
                and id(node) not in guarded
            ):
                found.append(f"{path.relative_to(root)}:{node.lineno}")
    return found


def test_no_navigation_bypasses_the_guard() -> None:
    assert unguarded_navigations(SRC) == []
