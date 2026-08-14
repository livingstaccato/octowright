# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-file AST traversal engine for the operation-gate architecture scanner.

Split out of ``check_operation_gate_architecture.py`` (kept under the
repository's LOC-per-file convention). ``FileScanner`` walks one module,
finding every function/method scope (arbitrarily nested -- each nested
``def`` is its OWN scope, never inheriting an enclosing decorator or
``async with`` boundary) and recording, per scope, the raw (uncovered)
Playwright-access hit lines and any dynamic operation-name forwarding sites.
The per-expression taint engine itself (``_scan_expr`` and friends) lives in
the sibling ``_operation_gate_taint.py``.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts._operation_gate_ast import (
    build_import_map,
    classify_withitem,
    has_ambiguous_library_import,
    has_literal_gate_decorator,
    is_protocol_class,
    is_type_checking_test,
    seed_param_taint,
    seed_session_param_names,
)
from scripts._operation_gate_constants import SEED_BASE_NAMES
from scripts._operation_gate_taint import (
    _FuncCtx,
    _scan_expr,
    _scan_target_reads,
    _taint_annotated_target,
    _taint_assign_target,
    _taint_with_as_target,
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
        # Conservative union, not sequential mutation: only ONE of body/orelse
        # runs at runtime, so a reassignment that un-taints a name in one
        # branch must never leak into the other branch's walk, and a name
        # left tainted by EITHER branch must stay tainted after the if/else
        # (a false negative here silently hides a real gate violation on the
        # branch that keeps the Playwright handle).
        before = set(ctx.tainted)
        _walk_function_body(stmt.body, gated=gated, ctx=ctx)
        body_tainted = ctx.tainted
        ctx.tainted = set(before)
        _walk_function_body(stmt.orelse, gated=gated, ctx=ctx)
        ctx.tainted = body_tainted | ctx.tainted
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
            _taint_with_as_target(item.optional_vars, context_tainted, ctx)
    _walk_function_body(stmt.body, gated=gated or gate_found, ctx=ctx)
