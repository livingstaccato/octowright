# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-file AST traversal engine for the operation-gate architecture scanner.

Split out of ``check_operation_gate_architecture.py`` (kept under the
repository's LOC-per-file convention). ``_FileScanner`` walks one module,
finding every function/method scope (arbitrarily nested -- each nested
``def`` is its OWN scope, never inheriting an enclosing decorator or
``async with`` boundary) and recording, per scope, the raw (uncovered)
Playwright-access hit lines and any dynamic operation-name forwarding sites.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts._operation_gate_ast import (
    annotation_signal,
    build_import_map,
    classify_withitem,
    has_ambiguous_library_import,
    has_literal_gate_decorator,
    is_protocol_class,
    is_type_checking_test,
    leftmost_name,
    seed_param_taint,
    seed_session_param_names,
)
from scripts._operation_gate_constants import (
    AMBIGUOUS_SEED_NAMES,
    PLAYWRIGHT_CHAIN_ATTRS,
    PLAYWRIGHT_ROOT_ATTRS,
    SEED_BASE_NAMES,
    SEED_PARAM_NAMES,
    TARGET_METHOD,
)


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    function: str
    line: int
    detail: str


@dataclass(slots=True)
class _FuncRecord:
    key: str
    raw_hit_lines: set[int]
    dynamic_sites: list[int]


class FileScanner:
    def __init__(self, rel_path: str, tree: ast.Module) -> None:
        self.rel_path = rel_path
        self.import_map: dict[str, str] = build_import_map(tree)
        self.suppress_ambiguous_names: bool = has_ambiguous_library_import(tree)
        self.records: dict[str, _FuncRecord] = {}
        self._visit_top_level(tree.body)

    def _record(self, key: str) -> _FuncRecord:
        record = self.records.get(key)
        if record is None:
            record = _FuncRecord(key=key, raw_hit_lines=set(), dynamic_sites=[])
            self.records[key] = record
        return record

    def _visit_top_level(self, stmts: Sequence[ast.stmt]) -> None:
        self._walk_scoped(stmts, qual_parts=[])

    def _walk_scoped(self, stmts: Sequence[ast.stmt], qual_parts: list[str]) -> None:
        """Module/class level: only look for nested def/class scopes."""
        for stmt in stmts:
            if isinstance(stmt, ast.ClassDef):
                self._enter_class(stmt, qual_parts)
            elif isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
                self._enter_function(stmt, qual_parts)
            elif isinstance(stmt, ast.If) and is_type_checking_test(stmt.test):
                # Still descend for nested class/def scopes (none realistically appear
                # under TYPE_CHECKING), but never for hit-scanning.
                continue

    def _enter_class(self, node: ast.ClassDef, qual_parts: list[str]) -> None:
        if is_protocol_class(node):
            return
        self._walk_scoped(node.body, [*qual_parts, node.name])

    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, qual_parts: list[str]) -> None:
        qualname = ".".join([*qual_parts, node.name])
        key = f"{self.rel_path}:{qualname}"
        self._record(key)
        gated = has_literal_gate_decorator(node)
        tainted = seed_param_taint(node, self.import_map, suppress_ambiguous=self.suppress_ambiguous_names)
        session_names = set(SEED_BASE_NAMES) | seed_session_param_names(node)
        ctx = _FuncCtx(
            scanner=self,
            key=key,
            qual_parts=[*qual_parts, node.name],
            tainted=tainted,
            session_names=session_names,
        )
        _walk_function_body(node.body, gated=gated, ctx=ctx)


@dataclass(slots=True)
class _FuncCtx:
    scanner: FileScanner
    key: str
    qual_parts: list[str]
    tainted: set[str]
    session_names: set[str]

    def report_hit(self, line: int) -> None:
        self.scanner._record(self.key).raw_hit_lines.add(line)

    def report_dynamic(self, line: int) -> None:
        self.scanner._record(self.key).dynamic_sites.append(line)


def _name_based_taint(name: str, ctx: _FuncCtx) -> bool:
    # See seed_param_taint's identical guard: an unannotated local named
    # request/response/websocket in a file that imports httpx/starlette is
    # more likely that library's object than Playwright's.
    if name not in SEED_PARAM_NAMES:
        return False
    return not (ctx.scanner.suppress_ambiguous_names and name in AMBIGUOUS_SEED_NAMES)


def _taint_assign_target(target: ast.expr, tainted_value: bool, ctx: _FuncCtx) -> None:
    if isinstance(target, ast.Name):
        # A conventionally-named local (``context = await browser_type.new_context()``)
        # is seeded the same way a same-named PARAMETER would be -- the taint
        # source is the identifier's meaning, not whether it arrived as an
        # argument or a local assignment.
        if tainted_value or _name_based_taint(target.id, ctx):
            ctx.tainted.add(target.id)
        else:
            ctx.tainted.discard(target.id)
    elif isinstance(target, ast.Tuple | ast.List):
        for elt in target.elts:
            _taint_assign_target(elt, tainted_value, ctx)
    elif isinstance(target, ast.Starred):
        _taint_assign_target(target.value, tainted_value, ctx)
    # Attribute/Subscript assignment targets (self.page = x, d[k] = x) don't
    # extend local-variable taint tracking; nothing further to do -- but see
    # _scan_target_reads, called alongside this everywhere it's called, for
    # the embedded-read Playwright accesses those target shapes can carry.


def _scan_target_reads(target: ast.expr, ctx: _FuncCtx, *, gated: bool) -> None:
    """A Store-context assignment target can embed genuine Load-context
    reads -- ``cache[session.page.url] = 1`` and ``totals[page.url] += 1``
    both dereference Playwright INSIDE the target expression, which the AST
    marks as a single Store-context node the ordinary expression walk never
    visits. Recurse into exactly the parts that are still reads."""
    if isinstance(target, ast.Attribute):
        _scan_expr(target.value, ctx, gated=gated)
    elif isinstance(target, ast.Subscript):
        _scan_expr(target.value, ctx, gated=gated)
        _scan_expr(target.slice, ctx, gated=gated)
    elif isinstance(target, ast.Tuple | ast.List):
        for elt in target.elts:
            _scan_target_reads(elt, ctx, gated=gated)
    elif isinstance(target, ast.Starred):
        _scan_target_reads(target.value, ctx, gated=gated)
    # Name: no embedded read.


def _taint_annotated_target(target: ast.expr, annotation: ast.expr, tainted_value: bool, ctx: _FuncCtx) -> None:
    if isinstance(target, ast.Name):
        signal = annotation_signal(annotation, ctx.scanner.import_map)
        if signal is True:
            ctx.tainted.add(target.id)
            return
        if signal is False:
            # An explicit, concrete non-Playwright local annotation (e.g.
            # ``response: httpx.Response = ...``) overrides the conventional-
            # name heuristic the same way a PARAMETER annotation already did
            # -- previously only parameters got this override, so an
            # explicitly-typed local still fell through to the blind name
            # match.
            ctx.tainted.discard(target.id)
            return
    _taint_assign_target(target, tainted_value, ctx)


def _scan_expr(node: ast.expr | None, ctx: _FuncCtx, *, gated: bool) -> bool:
    if node is None:
        return False

    if isinstance(node, ast.Name):
        return node.id in ctx.tainted

    if isinstance(node, ast.Attribute):
        base_tainted = _scan_expr(node.value, ctx, gated=gated)
        attr = node.attr
        leftmost = leftmost_name(node.value)
        is_root_seed = leftmost is not None and leftmost in ctx.session_names and attr in PLAYWRIGHT_ROOT_ATTRS
        is_chain_hit = base_tainted and attr in PLAYWRIGHT_CHAIN_ATTRS
        if (is_root_seed or is_chain_hit) and not gated:
            ctx.report_hit(node.lineno)
        return is_root_seed or is_chain_hit or base_tainted

    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == TARGET_METHOD:
            _scan_expr(func.value, ctx, gated=gated)
            if not gated:
                ctx.report_hit(node.lineno)
            for a in node.args:
                _scan_expr(a, ctx, gated=gated)
            for kw in node.keywords:
                _scan_expr(kw.value, ctx, gated=gated)
            return True
        if isinstance(func, ast.Name) and func.id == TARGET_METHOD:
            if not gated:
                ctx.report_hit(node.lineno)
            for a in node.args:
                _scan_expr(a, ctx, gated=gated)
            return True
        if (
            isinstance(func, ast.Name)
            and func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            # ``getattr(context, "expose_binding", None)`` reaches the same
            # live surface as ``context.expose_binding`` but isn't an
            # ast.Attribute node -- resolve the literal name the same way so
            # this can't be used to dodge attribute-based detection.
            base_tainted = _scan_expr(node.args[0], ctx, gated=gated)
            attr = node.args[1].value
            leftmost = leftmost_name(node.args[0])
            is_root_seed = leftmost is not None and leftmost in ctx.session_names and attr in PLAYWRIGHT_ROOT_ATTRS
            is_chain_hit = base_tainted and attr in PLAYWRIGHT_CHAIN_ATTRS
            if (is_root_seed or is_chain_hit) and not gated:
                ctx.report_hit(node.lineno)
            for a in node.args[2:]:
                _scan_expr(a, ctx, gated=gated)
            return is_root_seed or is_chain_hit or base_tainted
        func_tainted = _scan_expr(func, ctx, gated=gated)
        for a in node.args:
            _scan_expr(a, ctx, gated=gated)
        for kw in node.keywords:
            _scan_expr(kw.value, ctx, gated=gated)
        return func_tainted

    if isinstance(node, ast.Subscript):
        base_tainted = _scan_expr(node.value, ctx, gated=gated)
        _scan_expr(node.slice, ctx, gated=gated)
        return base_tainted

    if isinstance(node, ast.Starred | ast.Await):
        return _scan_expr(node.value, ctx, gated=gated)

    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return any([_scan_expr(elt, ctx, gated=gated) for elt in node.elts])

    if isinstance(node, ast.Dict):
        for k in node.keys:
            if k is not None:
                _scan_expr(k, ctx, gated=gated)
        for v in node.values:
            _scan_expr(v, ctx, gated=gated)
        return False

    if isinstance(node, ast.BoolOp):
        return any([_scan_expr(v, ctx, gated=gated) for v in node.values])

    if isinstance(node, ast.BinOp):
        left = _scan_expr(node.left, ctx, gated=gated)
        right = _scan_expr(node.right, ctx, gated=gated)
        return left or right

    if isinstance(node, ast.UnaryOp):
        return _scan_expr(node.operand, ctx, gated=gated)

    if isinstance(node, ast.Compare):
        result = _scan_expr(node.left, ctx, gated=gated)
        for comparator in node.comparators:
            result = _scan_expr(comparator, ctx, gated=gated) or result
        return result

    if isinstance(node, ast.IfExp):
        _scan_expr(node.test, ctx, gated=gated)
        body_t = _scan_expr(node.body, ctx, gated=gated)
        orelse_t = _scan_expr(node.orelse, ctx, gated=gated)
        return body_t or orelse_t

    if isinstance(node, ast.NamedExpr):
        value_tainted = _scan_expr(node.value, ctx, gated=gated)
        _taint_assign_target(node.target, value_tainted, ctx)
        return value_tainted

    if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp):
        for generator in node.generators:
            iter_tainted = _scan_expr(generator.iter, ctx, gated=gated)
            _scan_target_reads(generator.target, ctx, gated=gated)
            _taint_assign_target(generator.target, iter_tainted, ctx)
            for cond in generator.ifs:
                _scan_expr(cond, ctx, gated=gated)
        if isinstance(node, ast.DictComp):
            _scan_expr(node.key, ctx, gated=gated)
            _scan_expr(node.value, ctx, gated=gated)
            return False
        return _scan_expr(node.elt, ctx, gated=gated)

    if isinstance(node, ast.Lambda):
        # Independent scope, same rule as a nested ``def`` (Task 11 scanner
        # rule): a lambda registered as an event handler
        # (page.on("dialog", lambda: ...)) executes long after whatever
        # lexically-enclosing gate was active when it was DEFINED, so it must
        # never inherit that gate's ``gated=True``.
        _enter_lambda(node, ctx=ctx)
        return False

    if isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                _scan_expr(value.value, ctx, gated=gated)
        return False

    if isinstance(node, ast.Constant):
        return False

    tainted_any = False
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.expr):
            tainted_any = _scan_expr(child, ctx, gated=gated) or tainted_any
    return tainted_any


def _walk_function_body(stmts: Sequence[ast.stmt], *, gated: bool, ctx: _FuncCtx) -> None:
    for stmt in stmts:
        _walk_function_stmt(stmt, gated=gated, ctx=ctx)


def _walk_function_stmt(stmt: ast.stmt, *, gated: bool, ctx: _FuncCtx) -> None:
    if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef):
        _enter_nested_function(stmt, ctx=ctx)
        return
    if isinstance(stmt, ast.ClassDef):
        if not is_protocol_class(stmt):
            ctx.scanner._walk_scoped(stmt.body, [*ctx.qual_parts, stmt.name])
        return
    if isinstance(stmt, ast.If):
        if is_type_checking_test(stmt.test):
            _walk_function_body(stmt.orelse, gated=gated, ctx=ctx)
            return
        _scan_expr(stmt.test, ctx, gated=gated)
        _walk_function_body(stmt.body, gated=gated, ctx=ctx)
        _walk_function_body(stmt.orelse, gated=gated, ctx=ctx)
        return
    if isinstance(stmt, ast.For | ast.AsyncFor):
        iter_tainted = _scan_expr(stmt.iter, ctx, gated=gated)
        _scan_target_reads(stmt.target, ctx, gated=gated)
        _taint_assign_target(stmt.target, iter_tainted, ctx)
        _walk_function_body(stmt.body, gated=gated, ctx=ctx)
        _walk_function_body(stmt.orelse, gated=gated, ctx=ctx)
        return
    if isinstance(stmt, ast.While):
        _scan_expr(stmt.test, ctx, gated=gated)
        _walk_function_body(stmt.body, gated=gated, ctx=ctx)
        _walk_function_body(stmt.orelse, gated=gated, ctx=ctx)
        return
    if isinstance(stmt, ast.With | ast.AsyncWith):
        _walk_with(stmt, gated=gated, ctx=ctx)
        return
    if isinstance(stmt, ast.Try | ast.TryStar):
        _walk_function_body(stmt.body, gated=gated, ctx=ctx)
        for handler in stmt.handlers:
            if handler.type is not None:
                _scan_expr(handler.type, ctx, gated=gated)
            _walk_function_body(handler.body, gated=gated, ctx=ctx)
        _walk_function_body(stmt.orelse, gated=gated, ctx=ctx)
        _walk_function_body(stmt.finalbody, gated=gated, ctx=ctx)
        return
    if isinstance(stmt, ast.Assign):
        value_tainted = _scan_expr(stmt.value, ctx, gated=gated)
        for target in stmt.targets:
            _scan_target_reads(target, ctx, gated=gated)
            _taint_assign_target(target, value_tainted, ctx)
        return
    if isinstance(stmt, ast.AnnAssign):
        value_tainted = _scan_expr(stmt.value, ctx, gated=gated) if stmt.value is not None else False
        _scan_target_reads(stmt.target, ctx, gated=gated)
        _taint_annotated_target(stmt.target, stmt.annotation, value_tainted, ctx)
        return
    if isinstance(stmt, ast.AugAssign):
        _scan_expr(stmt.value, ctx, gated=gated)
        _scan_target_reads(stmt.target, ctx, gated=gated)
        return
    if isinstance(stmt, ast.Return | ast.Expr):
        _scan_expr(stmt.value, ctx, gated=gated)
        return
    if isinstance(stmt, ast.Raise):
        _scan_expr(stmt.exc, ctx, gated=gated)
        _scan_expr(stmt.cause, ctx, gated=gated)
        return
    if isinstance(stmt, ast.Assert):
        _scan_expr(stmt.test, ctx, gated=gated)
        _scan_expr(stmt.msg, ctx, gated=gated)
        return
    if isinstance(stmt, ast.Delete):
        for target in stmt.targets:
            _scan_expr(target, ctx, gated=gated)
        return
    if isinstance(stmt, ast.Match):
        _scan_expr(stmt.subject, ctx, gated=gated)
        for case in stmt.cases:
            if case.guard is not None:
                _scan_expr(case.guard, ctx, gated=gated)
            _walk_function_body(case.body, gated=gated, ctx=ctx)
        return
    # Import/Global/Nonlocal/Pass/Break/Continue/etc: nothing to scan.


def _enter_nested_function(node: ast.FunctionDef | ast.AsyncFunctionDef, *, ctx: _FuncCtx) -> None:
    qual_parts = [*ctx.qual_parts, node.name]
    key = f"{ctx.scanner.rel_path}:{'.'.join(qual_parts)}"
    ctx.scanner._record(key)
    nested_gated = has_literal_gate_decorator(node)
    nested_tainted = set(ctx.tainted) | seed_param_taint(
        node, ctx.scanner.import_map, suppress_ambiguous=ctx.scanner.suppress_ambiguous_names
    )
    nested_session_names = set(ctx.session_names) | seed_session_param_names(node)
    nested_ctx = _FuncCtx(
        scanner=ctx.scanner,
        key=key,
        qual_parts=qual_parts,
        tainted=nested_tainted,
        session_names=nested_session_names,
    )
    _walk_function_body(node.body, gated=nested_gated, ctx=nested_ctx)


def _enter_lambda(node: ast.Lambda, *, ctx: _FuncCtx) -> None:
    # Lambdas have no name of their own; "<lambda>" matches Python's own
    # __name__ convention for one and keeps the qualname scheme uniform with
    # _enter_nested_function. Two lambdas on the same line of the same
    # enclosing scope would collide on (key, line) -- an acceptable heuristic
    # edge case, not reachable by any real production code in this repo.
    qual_parts = [*ctx.qual_parts, "<lambda>"]
    key = f"{ctx.scanner.rel_path}:{'.'.join(qual_parts)}"
    ctx.scanner._record(key)
    lambda_tainted = set(ctx.tainted) | seed_param_taint(
        node, ctx.scanner.import_map, suppress_ambiguous=ctx.scanner.suppress_ambiguous_names
    )
    lambda_session_names = set(ctx.session_names) | seed_session_param_names(node)
    lambda_ctx = _FuncCtx(
        scanner=ctx.scanner,
        key=key,
        qual_parts=qual_parts,
        tainted=lambda_tainted,
        session_names=lambda_session_names,
    )
    # A lambda can never carry a literal-context boundary of its own (no
    # decorators, no statement body to hold an ``async with``), so it always
    # starts ungated -- gated=False unconditionally, never inherited.
    _scan_expr(node.body, lambda_ctx, gated=False)


def _walk_with(stmt: ast.With | ast.AsyncWith, *, gated: bool, ctx: _FuncCtx) -> None:
    gate_found = False
    for item in stmt.items:
        kind = classify_withitem(item)
        if kind in ("gate-literal", "close-gate"):
            gate_found = True
        elif kind == "gate-dynamic":
            ctx.report_dynamic(item.context_expr.lineno)
        context_tainted = _scan_expr(item.context_expr, ctx, gated=gated)
        if item.optional_vars is not None:
            # ``async with page.expect_popup() as info:`` -- info is bound to
            # whatever __aenter__ returns; when the context expression itself
            # is already recognized as tainted (a chain rooted in a seeded
            # name), the binding inherits that taint. This does NOT taint
            # e.g. ``async with client.stream(...) as response:`` -- "client"/
            # "stream" were never seeded, so context_tainted is False there
            # and the binding stays untainted, same as before this fix.
            _scan_target_reads(item.optional_vars, ctx, gated=gated)
            _taint_assign_target(item.optional_vars, context_tainted, ctx)
    _walk_function_body(stmt.body, gated=gated or gate_found, ctx=ctx)
