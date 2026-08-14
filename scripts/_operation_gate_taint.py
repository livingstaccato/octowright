# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Per-function taint engine for the operation-gate architecture scanner:
the recursive expression walker (``_scan_expr``) and the assignment-target
helpers that mutate a function scope's tainted-name set. Split out of
``_operation_gate_scanner.py`` (kept under the repository's LOC-per-file
convention) -- everything here operates on expressions and per-function
state (``_FuncCtx``); everything statement/scope-shaped (``FileScanner``,
nested ``def`` entry, ``with`` gate classification) stays in the scanner
module, which imports from here.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import TYPE_CHECKING

from scripts._operation_gate_ast import annotation_signal, leftmost_name, seed_param_taint, seed_session_param_names
from scripts._operation_gate_constants import (
    AMBIGUOUS_SEED_NAMES,
    PLAYWRIGHT_CHAIN_ATTRS,
    PLAYWRIGHT_ROOT_ATTRS,
    SEED_PARAM_NAMES,
    TARGET_METHOD,
)

if TYPE_CHECKING:
    from scripts._operation_gate_scanner import FileScanner


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
        if signal is False and not tainted_value:
            # An explicit, concrete non-Playwright local annotation (e.g.
            # ``response: httpx.Response = ...``) overrides only the
            # conventional-NAME heuristic below, the same way a PARAMETER
            # annotation already did -- it must NOT override a genuinely
            # tainted VALUE (e.g. ``page: MyPage = session.page``, where the
            # annotation is misleading but the RHS is unmistakably a real
            # Playwright dereference). Real dataflow evidence always wins
            # over a local type annotation; only fall through to the blind
            # name match when there is no such evidence either.
            ctx.tainted.discard(target.id)
            return
    _taint_assign_target(target, tainted_value, ctx)


def _taint_with_as_target(target: ast.expr, context_tainted: bool, ctx: _FuncCtx) -> None:
    """A ``with ... as X:`` binding only ever ADDS taint, unlike a plain
    assignment's ``_taint_assign_target``, which also DISCARDS it. A plain
    assignment's RHS is an ordinary expression fully visible to ``_scan_expr``,
    so "not recognized as tainted" is good evidence of "genuinely not
    tainted". A context manager's ``__enter__`` return type is invisible to
    this scanner regardless -- ``context_tainted=False`` means "no positive
    evidence", not "proven untainted" -- so discarding here would erase real
    taint a PRIOR statement already established on the same name (e.g.
    ``handle = session._target(); ...; with contextlib.suppress(...) as
    handle: ...; await handle.click(...)`` after the block must still see
    "handle" as tainted). The conventional-name check is independent
    positive evidence, same as it is for a plain assignment."""
    if isinstance(target, ast.Name):
        if context_tainted or _name_based_taint(target.id, ctx):
            ctx.tainted.add(target.id)
        return
    if isinstance(target, ast.Tuple | ast.List):
        for elt in target.elts:
            _taint_with_as_target(elt, context_tainted, ctx)
    elif isinstance(target, ast.Starred):
        _taint_with_as_target(target.value, context_tainted, ctx)


def _enter_lambda(node: ast.Lambda, *, ctx: _FuncCtx) -> None:
    # Lambdas have no name of their own; "<lambda>" matches Python's own
    # __name__ convention for one and keeps the qualname scheme uniform with
    # the scanner module's _enter_nested_function. Two lambdas on the same
    # line of the same enclosing scope would collide on (key, line) -- an
    # acceptable heuristic edge case, not reachable by any real production
    # code in this repo.
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
