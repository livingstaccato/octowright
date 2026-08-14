# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Low-level AST pattern recognition for the operation-gate architecture
scanner: import resolution, decorator/context-manager classification, and
parameter-taint seeding. Split out of ``check_operation_gate_architecture.py``
(kept under the repository's LOC-per-file convention).

These functions operate on bare AST nodes plus an import-alias map -- no
dependency on the scanner's own traversal state (``_FuncCtx``/``_FileScanner``,
defined in ``_operation_gate_scanner.py``).
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from scripts._operation_gate_constants import (
    AMBIGUOUS_NAME_LIBRARY_MODULES,
    AMBIGUOUS_SEED_NAMES,
    CLOSE_METHOD,
    DECORATOR_NAME,
    FORWARDER_FUNCTION,
    GATE_METHOD,
    GENERIC_TYPING_MODULES,
    SEED_PARAM_NAMES,
    SESSION_TYPE_NAMES,
)

_FunctionLike = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda


def is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def is_protocol_class(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name) and target.id == "Protocol":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "Protocol":
            return True
    return False


def build_import_map(tree: ast.Module) -> dict[str, str]:
    import_map: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                import_map[local] = node.module
        elif isinstance(node, ast.Import):
            # ``import httpx`` / ``import httpx as hx`` -- needed so a
            # module-qualified annotation (``httpx.Response``) resolves via
            # its leftmost Name the same way an ``ImportFrom`` alias does;
            # without this, only ``from httpx import Response`` was ever
            # recognized, and real production code uses the bare form.
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                import_map[local] = alias.name
    return import_map


def has_ambiguous_library_import(tree: ast.Module) -> bool:
    """True if the file imports a library (httpx, starlette) whose own
    ``request``/``response``/``websocket``-named objects are common enough
    that the same conventional names in THIS scanner's seed vocabulary are
    more likely to be false positives than real Playwright handles."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in AMBIGUOUS_NAME_LIBRARY_MODULES:
                    return True
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".")[0] in AMBIGUOUS_NAME_LIBRARY_MODULES
        ):
            return True
    return False


def is_playwright_annotation(annotation: ast.expr, import_map: Mapping[str, str]) -> bool:
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            module = import_map.get(node.id)
            if module is not None and module.startswith("playwright"):
                return True
    return False


def annotation_signal(annotation: ast.expr, import_map: Mapping[str, str]) -> bool | None:
    """True: resolves to a Playwright import. False: resolves to a concrete,
    non-generic import from somewhere else (e.g. Starlette's ``WebSocket``) --
    an explicit type strong enough to override the conventional-name
    heuristic below. None: unresolvable (``Any``, unannotated, a bare local
    class) -- defer to the naming convention."""
    if is_playwright_annotation(annotation, import_map):
        return True
    saw_other_concrete_type = False
    for node in ast.walk(annotation):
        if isinstance(node, ast.Name):
            module = import_map.get(node.id)
            if module is not None and module not in GENERIC_TYPING_MODULES:
                saw_other_concrete_type = True
    return False if saw_other_concrete_type else None


def all_function_args(node: _FunctionLike) -> list[ast.arg]:
    args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        args.append(node.args.vararg)
    if node.args.kwarg is not None:
        args.append(node.args.kwarg)
    return args


def seed_param_taint(node: _FunctionLike, import_map: Mapping[str, str], *, suppress_ambiguous: bool) -> set[str]:
    # ``ast.Lambda`` has no ``.annotation`` on its args (illegal syntax), so
    # ``annotation_signal`` is always None there and every lambda param falls
    # straight to the name-based branch below -- correct, since a lambda
    # literally cannot carry the explicit-type override.
    tainted: set[str] = set()
    for arg in all_function_args(node):
        signal = annotation_signal(arg.annotation, import_map) if arg.annotation is not None else None
        if signal is True:
            tainted.add(arg.arg)
            continue
        if signal is False:
            # An explicit, concrete non-Playwright type (e.g. Starlette's
            # WebSocket) overrides the conventional-name heuristic below --
            # this is what keeps ``websocket: WebSocket`` route handlers from
            # being confused with Playwright's own ``websocket`` callback
            # parameter, which shares the name but is typed loosely (``Any``).
            continue
        if arg.arg not in SEED_PARAM_NAMES:
            continue
        if suppress_ambiguous and arg.arg in AMBIGUOUS_SEED_NAMES:
            # No annotation resolved either way (signal is None) AND the file
            # shows independent evidence (an httpx/starlette import) that an
            # unannotated "request"/"response"/"websocket" here is more
            # likely that library's object than Playwright's.
            continue
        tainted.add(arg.arg)
    return tainted


def seed_session_param_names(node: _FunctionLike) -> set[str]:
    names: set[str] = set()
    for arg in all_function_args(node):
        if arg.arg.endswith("session"):
            names.add(arg.arg)
            continue
        if arg.annotation is not None:
            for sub in ast.walk(arg.annotation):
                if isinstance(sub, ast.Name) and sub.id in SESSION_TYPE_NAMES:
                    names.add(arg.arg)
                    break
    return names


def has_literal_gate_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name != DECORATOR_NAME:
            continue
        if decorator.args and isinstance(decorator.args[0], ast.Constant) and isinstance(decorator.args[0].value, str):
            return True
    return False


def leftmost_name(node: ast.expr) -> str | None:
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def is_literal_str(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def call_name(func: ast.expr) -> str | None:
    # ``self.operation(...)`` (Attribute) and a locally-extracted bound method
    # -- ``operation = self.operation; ... operation(...)`` (bare Name) -- are
    # both legitimate call shapes for the SAME gate method; gated_operation's
    # own inner wrapper uses the latter. Either shape resolves to the same name.
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def classify_withitem(item: ast.withitem) -> str:
    call = item.context_expr
    if not isinstance(call, ast.Call):
        return "none"
    name = call_name(call.func)
    if name == GATE_METHOD:
        if call.args and is_literal_str(call.args[0]):
            return "gate-literal"
        return "gate-dynamic"
    if name == CLOSE_METHOD:
        return "close-gate"
    if name == FORWARDER_FUNCTION:
        if len(call.args) >= 3 and is_literal_str(call.args[2]):
            return "gate-literal"
        return "gate-dynamic"
    return "none"
