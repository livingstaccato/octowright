# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Static-analysis pass over saved macro JSON.

Pure module — no I/O, no MCP dependency. Surfaces probable mistakes before
the runtime fails partway through with a generic error: missing required
fields, unknown action types, lifecycle actions that don't belong in
macros, empty conditional branches, and string literals that look like
credentials but aren't parameterized.

The set of supported simple actions and their required fields is mirrored
from `octowright.macros._dispatch_simple`; conditional action shapes mirror
`octowright.conditional`. The lifecycle / replay-skip set mirrors
`octowright.macros._REPLAY_SKIP`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Action catalogues — kept in sync with macros/runtime.py and conditional.py manually.
# ---------------------------------------------------------------------------

# Map: simple action name -> tuple of REQUIRED field names.
# Mirrors `octowright.macros._dispatch_simple`.
_SIMPLE_REQUIRED: dict[str, tuple[str, ...]] = {
    "navigate": ("url",),
    "click": ("selector",),
    "click_by": (),
    "type": ("selector", "text"),
    "fill": ("selector", "value"),
    "fill_by": ("value",),
    "press_key": ("key",),
    "screenshot": (),  # path is optional (skipped if missing)
    "evaluate": ("expression",),
    "wait_for": (),  # selector OR text — runtime accepts either
    "expect_url": ("pattern",),
    "expect_text": ("selector", "text"),
    "expect_selector": ("selector",),
    "expect_js": ("expression",),
    "mock_route": ("pattern",),
    "unmock_route": ("pattern",),
    "set_dialog_policy": ("policy",),
    "set_input_files": ("selector",),
}

# Lifecycle / inspection actions that the runtime silently skips during
# replay. Mirrors `octowright.macros._REPLAY_SKIP`.
_REPLAY_SKIP: frozenset[str] = frozenset({"launch", "close", "snapshot"})

# Conditional action names. Mirrors `octowright.conditional.CONDITIONAL_ACTIONS`.
_CONDITIONAL_ACTIONS: frozenset[str] = frozenset({"if_selector", "try", "try_each"})

_MACRO_CALL_ACTION = "macro_call"

_KNOWN_ACTIONS: frozenset[str] = (
    frozenset(_SIMPLE_REQUIRED) | _REPLAY_SKIP | _CONDITIONAL_ACTIONS | {_MACRO_CALL_ACTION}
)

# ---------------------------------------------------------------------------
# Credential heuristics
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.\w+$")
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")
_HAS_DIGIT = re.compile(r"\d")
_HAS_LETTER = re.compile(r"[A-Za-z]")
_HAS_SPECIAL = re.compile(r"[^A-Za-z0-9]")

# Fields that are NEVER credentials even if they happen to look like one
# (e.g. a CSS selector containing `[id="user@host"]` — unlikely, but cheap
# to skip). We only inspect string fields whose KEY plausibly carries
# user-supplied values.
_CREDENTIAL_CANDIDATE_KEYS: frozenset[str] = frozenset(
    {"value", "text", "url", "expression", "pattern", "body", "key", "prompt_text"}
)
_ARIA_LOCATOR_KEYS: frozenset[str] = frozenset({"role", "role_name", "label", "text", "test_id"})


def _looks_like_password(s: str) -> bool:
    """True if *s* is >= 12 chars and contains digits AND letters AND a special char."""
    if len(s) < 12:
        return False
    return bool(_HAS_DIGIT.search(s) and _HAS_LETTER.search(s) and _HAS_SPECIAL.search(s))


def _looks_like_email(s: str) -> bool:
    return bool(_EMAIL_RE.match(s))


def _is_placeholder(s: str) -> bool:
    return bool(_PLACEHOLDER_RE.search(s))


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str  # short stable identifier, e.g. "missing_required_field"
    message: str
    action_index: int | None  # None for whole-macro issues, index into actions[] for per-action


# ---------------------------------------------------------------------------
# Per-rule helpers
# ---------------------------------------------------------------------------


def _check_credentials(action: dict[str, Any], outer_index: int, issues: list[Issue]) -> None:
    """Scan candidate string fields for things that look like literal credentials."""
    for key, val in action.items():
        if key not in _CREDENTIAL_CANDIDATE_KEYS:
            continue
        if not isinstance(val, str):
            continue
        if _is_placeholder(val):
            continue
        if _looks_like_email(val) or _looks_like_password(val):
            issues.append(
                Issue(
                    severity="warning",
                    code="looks_like_credential",
                    message=(
                        f"value {val!r} in field {key!r} looks like a literal credential — "
                        "consider {{email}} parameterization"
                    ),
                    action_index=outer_index,
                )
            )


def _check_simple(action: dict[str, Any], kind: str, outer_index: int, issues: list[Issue]) -> None:
    """Validate a known simple action's required fields."""
    if kind in {"click_by", "fill_by"} and not any(action.get(k) for k in _ARIA_LOCATOR_KEYS):
        issues.append(
            Issue(
                severity="error",
                code="missing_required_field",
                message=(f"action {kind!r} is missing required locator field (one of role, label, text, or test_id)"),
                action_index=outer_index,
            )
        )

    required = _SIMPLE_REQUIRED[kind]
    for field in required:
        if field not in action or action.get(field) in (None, ""):
            issues.append(
                Issue(
                    severity="error",
                    code="missing_required_field",
                    message=f"action {kind!r} is missing required field {field!r}",
                    action_index=outer_index,
                )
            )


def _check_if_selector(action: dict[str, Any], outer_index: int, issues: list[Issue]) -> None:
    if "selector" not in action or not action.get("selector"):
        issues.append(
            Issue(
                severity="error",
                code="if_selector_missing_selector",
                message="if_selector is missing required field 'selector'",
                action_index=outer_index,
            )
        )
    then_branch = action.get("then") or []
    else_branch = action.get("else") or []
    if not then_branch and not else_branch:
        issues.append(
            Issue(
                severity="warning",
                code="if_selector_empty_branches",
                message="if_selector has no actions in either 'then' or 'else' (no-op condition)",
                action_index=outer_index,
            )
        )


def _check_macro_call(action: dict[str, Any], outer_index: int, issues: list[Issue]) -> None:
    name = action.get("name")
    if not isinstance(name, str) or not name:
        issues.append(
            Issue(
                severity="error",
                code="macro_call_invalid_name",
                message="macro_call is missing required non-empty string field 'name'",
                action_index=outer_index,
            )
        )

    if "args" in action and not isinstance(action["args"], dict):
        issues.append(
            Issue(
                severity="error",
                code="macro_call_invalid_args",
                message="macro_call field 'args' must be a dict when provided",
                action_index=outer_index,
            )
        )


def _check_try(action: dict[str, Any], outer_index: int, issues: list[Issue]) -> None:
    if "actions" not in action or not isinstance(action.get("actions"), list):
        issues.append(
            Issue(
                severity="error",
                code="try_missing_actions",
                message="try is missing required field 'actions' (must be a list)",
                action_index=outer_index,
            )
        )
        return
    if len(action["actions"]) == 0:
        issues.append(
            Issue(
                severity="warning",
                code="try_empty_actions",
                message="try has an empty 'actions' list",
                action_index=outer_index,
            )
        )


def _check_try_each(action: dict[str, Any], outer_index: int, issues: list[Issue]) -> None:
    if "branches" not in action or not isinstance(action.get("branches"), list):
        issues.append(
            Issue(
                severity="error",
                code="try_each_missing_branches",
                message="try_each is missing required field 'branches' (must be a list)",
                action_index=outer_index,
            )
        )
        return
    branches = action["branches"]
    if len(branches) == 0:
        issues.append(
            Issue(
                severity="error",
                code="try_each_empty_branches",
                message="try_each has an empty 'branches' list",
                action_index=outer_index,
            )
        )
        return
    for i, branch in enumerate(branches):
        if not isinstance(branch, list) or len(branch) == 0:
            issues.append(
                Issue(
                    severity="warning",
                    code="try_each_branch_empty",
                    message=f"try_each branch [{i}] is empty",
                    action_index=outer_index,
                )
            )


def _lint_if_selector(action: dict, idx: int, issues: list[Issue]) -> None:
    _check_if_selector(action, idx, issues)
    # `or []` would fall through truthy-non-list values (e.g. then="x" iterates
    # chars; then=1 raises TypeError). Walk only when the branch is a list.
    then_branch = action.get("then")
    if isinstance(then_branch, list):
        for sub in then_branch:
            _lint_action(sub, idx, issues)
    else_branch = action.get("else")
    if isinstance(else_branch, list):
        for sub in else_branch:
            _lint_action(sub, idx, issues)


def _lint_try(action: dict, idx: int, issues: list[Issue]) -> None:
    _check_try(action, idx, issues)
    actions = action.get("actions")
    if isinstance(actions, list):
        for sub in actions:
            _lint_action(sub, idx, issues)


def _lint_try_each(action: dict, idx: int, issues: list[Issue]) -> None:
    _check_try_each(action, idx, issues)
    branches = action.get("branches")
    if isinstance(branches, list):
        for branch in branches:
            if isinstance(branch, list):
                for sub in branch:
                    _lint_action(sub, idx, issues)


# Per-action-kind linters that need recursion or specialized checks. Simple
# actions (those in _SIMPLE_REQUIRED) and lifecycle skips are still handled
# inline because they share the same one-liner shape.
_LINT_HANDLERS: dict[str, Callable[[dict, int, list[Issue]], None]] = {
    "if_selector": _lint_if_selector,
    "try": _lint_try,
    "try_each": _lint_try_each,
    _MACRO_CALL_ACTION: lambda action, idx, issues: _check_macro_call(action, idx, issues),
}


def _lint_action(action: Any, outer_index: int, issues: list[Issue]) -> None:
    """Lint a single action; recurse into conditional sub-actions.

    Nested issues are reported under the OUTER action's index — we don't
    invent compound paths.
    """
    if not isinstance(action, dict):
        issues.append(
            Issue(
                severity="error",
                code="action_not_object",
                message=f"action at index {outer_index} is not a JSON object",
                action_index=outer_index,
            )
        )
        return

    kind = action.get("action")
    if not isinstance(kind, str) or not kind:
        issues.append(
            Issue(
                severity="error",
                code="missing_action_field",
                message=f"action at index {outer_index} has no 'action' field",
                action_index=outer_index,
            )
        )
        return

    if kind in _REPLAY_SKIP:
        issues.append(
            Issue(
                severity="warning",
                code="lifecycle_in_macro",
                message=f"action {kind!r} will be silently skipped at runtime; consider removing",
                action_index=outer_index,
            )
        )
        return

    if kind in _SIMPLE_REQUIRED:
        _check_simple(action, kind, outer_index, issues)
        _check_credentials(action, outer_index, issues)
        return

    handler = _LINT_HANDLERS.get(kind)
    if handler is not None:
        handler(action, outer_index, issues)
        return

    if kind not in _KNOWN_ACTIONS:
        issues.append(
            Issue(
                severity="warning",
                code="unknown_action",
                message=f"unknown action {kind!r} — could be a typo or future action type",
                action_index=outer_index,
            )
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lint_macro(macro: dict) -> list[Issue]:
    """Return zero or more Issues. Pure — no I/O.

    Whole-macro issues use ``action_index=None``; per-action issues use the
    index into the top-level ``actions`` list. Issues found inside nested
    conditional branches are still reported under the OUTER action's index.
    """
    issues: list[Issue] = []

    actions = macro.get("actions")
    if actions is None:
        issues.append(
            Issue(
                severity="error",
                code="missing_actions",
                message="macro has no 'actions' field",
                action_index=None,
            )
        )
        return issues

    if not isinstance(actions, list):
        issues.append(
            Issue(
                severity="error",
                code="actions_not_list",
                message="macro 'actions' field is not a list",
                action_index=None,
            )
        )
        return issues

    for i, action in enumerate(actions):
        _lint_action(action, i, issues)

    return issues
