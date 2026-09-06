# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Launch-path input guards must raise ``InvalidRequestError``, not ``ValueError``.

``BrowserPool.launch`` and ``_metrics.launch_span`` both classify by
``isinstance(exc, InvalidRequestError)``: a refused request is kept out of
``engine_health`` and off ``octowright_browser_launch_failed_total``, because
recording it there is byte-identical to reporting a broken engine (issue #214).

Nothing about that is inherited. A new check written with the project's
formerly conventional ``raise ValueError(...)`` inside one of these modules
would be classified as machinery failure and silently recreate the bug, and a
hand-maintained list of guards in a test is documentation rather than
enforcement. This scan is the enforcement, and its reach is exactly the modules
named below -- a guard added in some *other* module is a maintenance
requirement this cannot see, which is why ``AGENTS.md`` says so rather than
promising the classification is automatic.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "octowright"

#: Launch-reachable input checks, as ``(path, function or None)``. ``None``
#: means the whole module: every ``ValueError``-shaped raise in it is, by
#: construction, a rejection of caller input.
#:
#: ``core_page_mixin`` is entered at FUNCTION granularity, and that is the
#: entry the scan exists for -- ``_reject_unsafe_url`` is the guard that
#: produced issue #214 (`browser_launch(url="file:///etc/passwd")`), and it
#: lives in a large mixin that also raises ordinary ``ValueError`` for checks
#: nowhere near a launch (``key_mode``, viewport arguments). A module-granular
#: entry would therefore have to either exclude the guard the whole change is
#: about, or fail on unrelated lines.
#:
#: ``launch_execution`` and ``launch_pipeline`` currently contain no raises at
#: all. They are listed deliberately, as the forward half: they are on the
#: launch path, so a new check added there is exactly the regression this
#: scans for, and an entry that fires today is not a precondition for an entry
#: being worth having.
GUARD_TARGETS: tuple[tuple[str, str | None], ...] = (
    ("_paths.py", None),
    ("ssrf.py", None),
    ("url_patterns.py", None),
    ("http_headers.py", None),
    ("session/core_page_mixin.py", "_reject_unsafe_url"),
    ("browser_pool/options.py", None),
    ("browser_pool/launch_helpers.py", None),
    ("browser_pool/launch_execution.py", None),
    ("browser_pool/launch_pipeline.py", None),
)


def _raises_bare_value_error(tree: ast.AST) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        exc = node.exc
        name = exc.func if isinstance(exc, ast.Call) else exc
        if isinstance(name, ast.Name) and name.id == "ValueError":
            lines.append(node.lineno)
    return lines


def _scope(tree: ast.Module, function: str | None) -> ast.AST | None:
    """The subtree to scan: the whole module, or one function's body."""
    if function is None:
        return tree
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            return node
    return None


def test_no_launch_guard_raises_a_bare_value_error() -> None:
    offenders: list[str] = []
    for relative, function in GUARD_TARGETS:
        path = SRC / relative
        assert path.is_file(), f"{relative} moved -- update GUARD_TARGETS or this scan silently covers nothing"
        scope = _scope(ast.parse(path.read_text(), filename=str(path)), function)
        assert scope is not None, (
            f"{relative}::{function} not found -- a renamed guard must be re-pointed here, "
            "or the scan silently stops covering it"
        )
        for lineno in _raises_bare_value_error(scope):
            offenders.append(f"src/octowright/{relative}:{lineno}")

    assert not offenders, (
        "These raise ValueError on a launch-reachable input check. BrowserPool.launch "
        "classifies by type, so a bare ValueError is recorded as an engine fault and "
        "counted as a launch failure -- reintroducing issue #214. Raise "
        "octowright.request_errors.InvalidRequestError (a ValueError subclass, so "
        "existing callers are unaffected):\n  " + "\n  ".join(offenders)
    )


def test_the_function_scope_excludes_the_rest_of_its_module() -> None:
    """The narrow entry must be narrow in BOTH directions.

    ``core_page_mixin`` holds ``ValueError`` raises that are correct as they
    stand (``key_mode``, viewport arguments) and are not launch-reachable. If
    ``_scope`` silently fell back to the module, this file would fail on them
    and the obvious repair would be to drop the entry -- losing coverage of
    the one guard issue #214 came from.
    """
    module = ast.parse((SRC / "session/core_page_mixin.py").read_text())
    assert _raises_bare_value_error(module), "module has unrelated ValueError raises; premise of this test"

    scope = _scope(module, "_reject_unsafe_url")
    assert scope is not None
    assert _raises_bare_value_error(scope) == []


def test_the_scan_would_catch_the_regression_it_was_written_for() -> None:
    """Guard the guard: a scan that matches nothing passes for the wrong reason."""
    assert _raises_bare_value_error(ast.parse('raise ValueError("nope")')) == [1]
    assert _raises_bare_value_error(ast.parse("raise ValueError")) == [1]
    assert _raises_bare_value_error(ast.parse('raise InvalidRequestError("ok")')) == []
    assert _raises_bare_value_error(ast.parse("raise")) == []
