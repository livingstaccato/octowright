# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Derive the macro-privacy threading inventories from the AST.

Three revisions of ``docs/superpowers/specs/2026-09-11-macro-parameter-privacy-design.md``
asserted call-site counts in prose and all three were wrong, so the counts are
read mechanically here instead. The fourth revision measured them with a
throwaway script that lived outside the repository, which left every table
unreproducible -- the same defect one layer up. This is that script, committed.

Two rules matter more than the tables:

* The **policy surface** (which callees count as a redaction call) is derived,
  not hand-listed. A hand-list silently bounds every inventory: the r4 tables
  covered eight callee names that were never disclosed, so ``redact_preview``
  and ``_redact_sink_value`` were invisible to a document claiming to be
  exhaustive.
* **Durable writes are enumerated independently of the policy surface.** A rule
  defined over redaction calls gets greener when a redaction call is deleted.
  A sink that performs no redaction at all -- ``Recorder.record``, which writes
  and flushes per action through a raw file handle -- is exactly the sink that
  rule cannot see.

Run: ``uv run --active python scripts/derive_privacy_sites.py``
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

SRC = Path("src/octowright")

# Packages Part 0 threads a ledger through. Everything else is reported
# separately rather than silently dropped -- see PART_0_SCOPE in the report.
IN_SCOPE_PACKAGES = ("macros", "artifacts")

# ``recorder.py`` is a package-root module, not a package, and it is the sink a
# macro run writes to per action. Scoping by package alone is what hid it from
# the r4 inventory.
IN_SCOPE_MODULES = ("recorder.py",)

# A name is part of the policy surface if it is defined in one of these
# modules, or if it matches POLICY_NAME_RE anywhere under src/octowright.
POLICY_MODULES = (SRC / "macros" / "privacy.py", SRC / "artifacts" / "redaction.py")
POLICY_NAME_RE = re.compile(r"(redact|scrub|sensitive|credential)", re.IGNORECASE)

# Durable-write sinks, derived from two independent shapes: the repository's
# own write helpers, and any raw handle write. The second shape is what makes
# the recorder visible.
WRITE_HELPERS = frozenset({"atomic_write_text", "_json_write", "write_text", "write_bytes", "dump"})
HANDLE_WRITES = frozenset({"write", "writelines"})
WRITE_MODE_RE = re.compile(r"[wax]")

# Names whose presence in an enclosing scope means the site could thread a
# policy without a signature change.
MACROISH = frozenset({"macro", "called", "args", "args_used", "effective_args", "step_args", "call_args"})

# Names meaning the site already holds a RESOLVED policy. A site can lack every
# MACROISH binding (it cannot resolve) while holding one of these (it does not
# need to). Reporting only the first column would call such a site blind.
POLICYISH = frozenset({"sensitive_values", "scrub_values", "ledger", "privacy", "resolved_privacy"})

# The dispatch chain whose return annotations decide whether policy can travel
# back up. Derived from the AST; listed here only to select what to print.
CHAIN = (
    ("calls.py", "dispatch_macro_call"),
    ("execution.py", "_dispatch_one"),
    ("execution.py", "_run_macro_impl"),
    ("execution.py", "run_sequence"),
)


def call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def is_write_open(node: ast.Call) -> bool:
    """``open(path, "a")`` / ``path.open("a")`` -- a durable sink by mode."""
    if call_name(node) != "open":
        return False
    modes = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
    return any(WRITE_MODE_RE.search(m.value) for m in modes)


def derive_policy_surface() -> tuple[frozenset[str], dict[str, str], dict[str, str]]:
    """Return the policy-surface callee names, why each qualified, and where."""
    surface: dict[str, str] = {}
    defined_in: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        in_policy_module = path in POLICY_MODULES
        # Module-level defs only. A method defined inside a policy module
        # (``SensitiveRecorder.record``) is not itself a policy callee, and
        # counting it would make every ``recorder.record(...)`` a boundary.
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or node.name.startswith("__"):
                continue
            if in_policy_module:
                surface.setdefault(node.name, f"defined in {path}")
            elif POLICY_NAME_RE.search(node.name):
                surface.setdefault(node.name, f"name matches /{POLICY_NAME_RE.pattern}/ in {path}")
            defined_in.setdefault(node.name, str(path))
    return frozenset(surface), surface, defined_in


class Scanner(ast.NodeVisitor):
    """Collect policy boundaries, durable writes and scrub-tuple branches."""

    def __init__(self, path: Path, surface: frozenset[str], defined_in: dict[str, str]) -> None:
        self.path = path
        self.surface = surface
        self.defined_in = defined_in
        self.stack: list[tuple[str, set[str]]] = []
        self.boundaries: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.branches: list[dict[str, Any]] = []
        self.returns: dict[str, str] = {}

    @property
    def fn(self) -> str:
        return self.stack[-1][0] if self.stack else "<module>"

    def bound_names(self) -> set[str]:
        return {name for _fn, names in self.stack for name in names}

    def _enter(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        names = {a.arg for a in node.args.args + node.args.kwonlyargs}
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                names |= {t.id for t in sub.targets if isinstance(t, ast.Name)}
            elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                names.add(sub.target.id)
        if not self.stack:
            self.returns[node.name] = ast.unparse(node.returns) if node.returns else "<none>"
        self.stack.append((node.name, names))
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter

    def _record_boundary(self, node: ast.Call, name: str) -> None:
        self.boundaries.append(
            {
                "file": str(self.path),
                "line": node.lineno,
                "fn": self.fn,
                "call": name,
                "in_scope": sorted(self.bound_names() & MACROISH),
                "has_policy": sorted(self.bound_names() & POLICYISH),
                "intra_module": self.defined_in.get(name) == str(self.path),
            }
        )

    def _record_write(self, node: ast.Call, name: str, shape: str) -> None:
        self.writes.append(
            {
                "file": str(self.path),
                "line": node.lineno,
                "fn": self.fn,
                "call": name,
                "shape": shape,
                "has_policy": sorted(self.bound_names() & POLICYISH),
                "streaming": shape != "helper",
            }
        )

    def visit_Call(self, node: ast.Call) -> None:
        name = call_name(node)
        if name in self.surface:
            self._record_boundary(node, name)
        if name in WRITE_HELPERS:
            self._record_write(node, name, "helper")
        elif name in HANDLE_WRITES and isinstance(node.func, ast.Attribute):
            self._record_write(node, name, "raw handle")
        elif is_write_open(node):
            self._record_write(node, name, "open for write")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        names = {n.id for n in ast.walk(node.test) if isinstance(n, ast.Name)}
        if names & {"sensitive_values", "scrub_values"}:
            self.branches.append(
                {"file": str(self.path), "line": node.lineno, "fn": self.fn, "test": ast.unparse(node.test)[:70]}
            )
        self.generic_visit(node)


def scan_tree(
    surface: frozenset[str], defined_in: dict[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, str]]]:
    boundaries: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    branches: list[dict[str, Any]] = []
    returns: dict[str, dict[str, str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        scanner = Scanner(path, surface, defined_in)
        scanner.visit(ast.parse(path.read_text(encoding="utf-8")))
        boundaries += scanner.boundaries
        writes += scanner.writes
        branches += scanner.branches
        returns[path.name] = scanner.returns
    return boundaries, writes, branches, returns


def package_of(row: dict[str, Any]) -> str:
    return Path(row["file"]).relative_to(SRC).parts[0] if Path(row["file"]) != SRC else ""


def in_scope(row: dict[str, Any]) -> bool:
    return package_of(row) in IN_SCOPE_PACKAGES or Path(row["file"]).name in IN_SCOPE_MODULES


def is_cross_module(row: dict[str, Any]) -> bool:
    """A boundary whose callee is defined in another module."""
    return not str(row["file"]).endswith(("privacy.py", "redaction.py")) or row["fn"] == "<module>"


def substitution_facts() -> dict[str, Any]:
    """Whether ``substitute`` can be resolved eagerly: purity plus call order."""
    path = SRC / "macros" / "substitution.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    io_names = {"open", "read_text", "load_macro", "loads", "read_bytes"}
    io_calls = sorted({call_name(n) for n in ast.walk(tree) if isinstance(n, ast.Call)} & io_names)
    sites = []
    for other in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(other.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and call_name(node) == "substitute":
                sites.append(f"{other}:{node.lineno}")
    return {"io_calls_in_substitution": io_calls, "substitute_call_sites": sites}


def corpus_facts() -> dict[str, Any] | None:
    """Measure the local macro corpus, including nesting under conditionals."""
    try:
        from octowright.defaults import MACROS_DIR
    except Exception:  # pragma: no cover - defaults import is environment-dependent
        return None
    if not MACROS_DIR.exists():
        return None
    totals: Counter[str] = Counter()
    callees: Counter[str] = Counter()
    for path in sorted(MACROS_DIR.glob("*.json")):
        try:
            macro = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        totals["macros"] += 1
        _walk_actions(macro.get("actions") or [], totals, callees, guarded=False)
    return {"totals": dict(totals), "top_callees": callees.most_common(5)}


def _walk_actions(actions: list[Any], totals: Counter[str], callees: Counter[str], *, guarded: bool) -> None:
    for action in actions:
        if not isinstance(action, dict):
            continue
        kind = action.get("action")
        if kind == "macro_call":
            _count_macro_call(action, totals, callees, guarded=guarded)
        for key in ("then", "else", "actions", "try", "except"):
            nested = action.get(key)
            if isinstance(nested, list):
                totals["actions_in_branches"] += 1
                _walk_actions(nested, totals, callees, guarded=True)


_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


def _count_macro_call(action: dict[str, Any], totals: Counter[str], callees: Counter[str], *, guarded: bool) -> None:
    totals["macro_call"] += 1
    if guarded:
        totals["macro_call_under_branch"] += 1
    callees[str(action.get("macro") or action.get("name"))] += 1
    args = action.get("args")
    if isinstance(args, dict) and args:
        totals["macro_call_with_args"] += 1
        if any("{{" in str(v) for v in args.values()):
            totals["macro_call_args_with_placeholders"] += 1
        _count_nested_exposure(args, totals)


def _count_nested_exposure(args: dict[str, Any], totals: Counter[str]) -> None:
    """Can a nested credential value be absent from the OUTER scrub tuple?

    Collection is name-based and happens once, on the outer args
    (``execution.py:543``). Scrubbing is value-based. A nested credential is
    therefore already covered whenever its value arrives from an outer arg whose
    own name classifies -- which is why the structural gap is latent. It becomes
    live for a literal in the macro definition, or for a placeholder fed by an
    outer name the classifier does not flag.
    """
    from octowright.macros.privacy import is_sensitive_arg_key

    for key, value in args.items():
        if not is_sensitive_arg_key(key):
            continue
        totals["nested_credential_args"] += 1
        placeholders = _PLACEHOLDER_RE.findall(str(value))
        if not placeholders:
            totals["nested_credential_LITERAL"] += 1
        elif not any(is_sensitive_arg_key(name) for name in placeholders):
            totals["nested_credential_from_UNCLASSIFIED_outer"] += 1
        else:
            totals["nested_credential_from_classified_outer"] += 1


def _print_surface(sources: dict[str, str]) -> None:
    print("=" * 100)
    print("0. POLICY SURFACE -- derived, not hand-listed")
    print("=" * 100)
    for name in sorted(sources):
        print(f"{name:<34}{sources[name]}")
    print(f"\ntotal callee names: {len(sources)}")


def _print_boundaries(boundaries: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 100)
    print("A. POLICY BOUNDARIES -- every call into the derived surface")
    print("=" * 100)
    scoped = [r for r in boundaries if in_scope(r)]
    print(f"{'file:line':<46}{'enclosing fn':<30}{'call':<26}{'can resolve':<22}holds policy")
    for row in scoped:
        loc = f"{row['file']}:{row['line']}"
        scope = ",".join(row["in_scope"]) or "** NONE **"
        policy = ",".join(row["has_policy"]) or "--"
        marker = "   [intra-module]" if row["intra_module"] else ""
        print(f"{loc:<46}{row['fn']:<30}{row['call']:<26}{scope:<22}{policy}{marker}")
    cross = [r for r in scoped if not r["intra_module"]]
    blind = [r for r in cross if not r["in_scope"] and not r["has_policy"]]
    partial = [r for r in cross if not r["in_scope"] and r["has_policy"]]
    print(f"\nin Part 0 scope: {len(scoped)}   cross-module: {len(cross)}")
    print(f"cannot resolve AND holds no policy (BLIND): {len(blind)}   holds a resolved policy already: {len(partial)}")
    print("\nOUTSIDE Part 0 scope -- counted, not threaded. Listed so the filter hides nothing:")
    outside = Counter(f"{package_of(r)}/{r['call']}" for r in boundaries if not in_scope(r))
    for key, count in sorted(outside.items()):
        print(f"  {key:<52}{count}")
    print(f"  total outside: {sum(outside.values())}")


def _print_writes(writes: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 100)
    print("B. DURABLE WRITES -- enumerated independently of the policy surface")
    print("=" * 100)
    scoped = [r for r in writes if in_scope(r)]
    print(f"{'file:line':<50}{'enclosing fn':<28}{'shape':<18}holds policy")
    for row in scoped:
        loc = f"{row['file']}:{row['line']}"
        policy = ",".join(row["has_policy"]) or "-- NONE --"
        print(f"{loc:<50}{row['fn']:<28}{row['shape']:<18}{policy}")
    needs = [r for r in scoped if not r["has_policy"]]
    streaming = [r for r in scoped if r["streaming"]]
    print(f"\ntotal: {len(writes)}   in Part 0 scope: {len(scoped)}")
    print(f"of those STREAMING (not via a write helper): {len(streaming)}   holding no policy: {len(needs)}")


def _print_branches(branches: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 100)
    print("C. BRANCHES ON THE SCRUB TUPLE")
    print("=" * 100)
    for row in branches:
        print(f"{row['file']}:{row['line']:<6} {row['fn']:<34} if {row['test']}")
    print(f"\ntotal: {len(branches)}")


def _print_returns(returns: dict[str, dict[str, str]]) -> None:
    print("\n" + "=" * 100)
    print("D. RETURN SIGNATURES OF THE DISPATCH CHAIN")
    print("=" * 100)
    for module, fn in CHAIN:
        print(f"{fn:<22} ({module}) -> {returns.get(module, {}).get(fn, '<not found>')}")


def _print_substitution(facts: dict[str, Any]) -> None:
    print("\n" + "=" * 100)
    print("E. SUBSTITUTION -- is eager whole-graph resolution possible?")
    print("=" * 100)
    print(f"I/O calls inside macros/substitution.py : {facts['io_calls_in_substitution'] or 'none (pure)'}")
    for site in facts["substitute_call_sites"]:
        print(f"substitute() call site                  : {site}")


def _print_corpus(facts: dict[str, Any] | None) -> None:
    print("\n" + "=" * 100)
    print("F. CORPUS (this machine only)")
    print("=" * 100)
    if facts is None:
        print("macro corpus not present; skipped")
        return
    for key, value in sorted(facts["totals"].items()):
        print(f"{key:<34}{value}")
    for name, count in facts["top_callees"]:
        print(f"most-called nested macro              {name} ({count})")


def main() -> None:
    surface, sources, defined_in = derive_policy_surface()
    boundaries, writes, branches, returns = scan_tree(surface, defined_in)
    _print_surface(sources)
    _print_boundaries(boundaries)
    _print_writes(writes)
    _print_branches(branches)
    _print_returns(returns)
    _print_substitution(substitution_facts())
    _print_corpus(corpus_facts())


if __name__ == "__main__":
    main()
