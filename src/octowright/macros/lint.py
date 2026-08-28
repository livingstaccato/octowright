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

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .lint_credentials import (
    _CREDENTIAL_CANDIDATE_KEYS,
    _is_placeholder,
    _looks_like_email,
    _looks_like_password,
)
from .lint_fields import ambiguous_rename_fields, unknown_fields
from .lint_urls import code_carries_credential, url_carries_credential
from .runtime import _ACTION_MAP
from .substitution import SEMANTIC_FINDER_KEYS

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
    "hover": ("selector",),
    "select_option": ("selector",),
    "drag": (),
    "a11y_dragdrop": ("source_selector",),
    "resize": ("width", "height"),
    "open_url": ("url",),
    "switch_page": ("index",),
    "close_page": ("index",),
}

# Lifecycle / inspection actions that the runtime silently skips during
# replay. Mirrors `octowright.macros._REPLAY_SKIP`.
_REPLAY_SKIP: frozenset[str] = frozenset({"launch", "close", "snapshot"})

# Conditional action names. Mirrors `octowright.conditional.CONDITIONAL_ACTIONS`.
_CONDITIONAL_ACTIONS: frozenset[str] = frozenset({"if_selector", "try", "try_each"})

_MACRO_CALL_ACTION = "macro_call"

#: Candidate fields that hold a URL, and so are scanned by URL part.
_URL_LIKE_KEYS: frozenset[str] = frozenset({"url", "pattern"})
#: Candidate fields that hold JavaScript, scanned for embedded tokens only.
_CODE_LIKE_KEYS: frozenset[str] = frozenset({"expression"})

#: The finder keys a click_by/fill_by must set EXACTLY one of — the arity
#: `build_locator` enforces. Derived from substitution rather than listed:
#: Including `role_name` here would let `{"action": "click_by", "role_name":
#: "Save"}` passed lint and then raised `ValueError: exactly one of
#: role/label/text/test_id must be set` on replay.
_ARIA_LOCATOR_KEYS: frozenset[str] = frozenset(SEMANTIC_FINDER_KEYS)

_KNOWN_ACTIONS: frozenset[str] = (
    frozenset(_SIMPLE_REQUIRED) | frozenset(_ACTION_MAP) | _REPLAY_SKIP | _CONDITIONAL_ACTIONS | {_MACRO_CALL_ACTION}
)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str  # short stable identifier, e.g. "missing_required_field"
    message: str
    action_index: int | None  # None for whole-macro issues, index into actions[] for per-action


#: Report one problem on the action under inspection. Every ``_check_simple_*``
#: helper reports through this single callback. Mixing a callback for "missing
#: field" with direct ``issues.append`` for anything else gives one family of
#: checks two ways to say the same thing.
_Report = Callable[..., None]


# ---------------------------------------------------------------------------
# Per-rule helpers
# ---------------------------------------------------------------------------


def _field_carries_credential(key: str, val: str) -> bool:
    """Whether one candidate field's value looks like a literal credential.

    Dispatch is by field KIND, not by one special-cased name. The 0.14.4 fix
    special-cased the literal key ``url``, which left the other URL-shaped and
    code-shaped candidates (``pattern`` on expect_url/mock_route/unmock_route,
    ``expression`` on evaluate/expect_js) still running the blob heuristic --
    so the noise relocated instead of going away. A route glob and a JS
    expression satisfy "12+ chars mixing letter/digit/special" exactly as
    readily as a URL does.
    """
    if key in _URL_LIKE_KEYS:
        verdict = url_carries_credential(val)
        # None == "not parseable as a URL"; fall through rather than assert
        # that an unparsable string is credential-free (see lint_urls).
        if verdict is not None:
            return verdict
    elif key in _CODE_LIKE_KEYS:
        return code_carries_credential(val)
    return _looks_like_email(val) or _looks_like_password(val)


def _check_credentials(action: dict[str, Any], outer_index: int, issues: list[Issue]) -> None:
    """Scan candidate string fields for things that look like literal credentials."""
    for key, val in action.items():
        if key not in _CREDENTIAL_CANDIDATE_KEYS:
            continue
        if not isinstance(val, str):
            continue
        if _is_placeholder(val):
            continue
        if not _field_carries_credential(key, val):
            continue
        issues.append(
            Issue(
                severity="warning",
                code="looks_like_credential",
                message=(
                    f"field {key!r} looks like a literal credential (value redacted) — "
                    "consider {{email}} parameterization"
                ),
                action_index=outer_index,
            )
        )


def _check_simple(action: dict[str, Any], kind: str, outer_index: int, issues: list[Issue]) -> None:
    """Validate a known simple action's required fields."""

    def _report(message: str, code: str = "missing_required_field") -> None:
        issues.append(Issue(severity="error", code=code, message=message, action_index=outer_index))

    _check_simple_locator_fields(action, kind, _report)
    _check_simple_drag_fields(action, kind, _report)
    _check_a11y_dragdrop_verify_arity(action, kind, _report)
    _check_simple_required_fields(action, kind, _report)


def _check_unknown_fields(action: dict[str, Any], kind: str, outer_index: int, issues: list[Issue]) -> None:
    """Flag fields the runtime would splat at a session method that rejects them.

    Severity is error where replay really raises: it cannot do what the action
    says, and catching that at save time is the whole point (see lint_fields).
    """
    # `screenshot` is the one kind _dispatch_standard special-cases: it forwards
    # only `path`, so a stray field there is dropped instead of raising. Naming
    # a TypeError would send the author hunting a crash that never happens --
    # and error severity does worse than mislead. `PUT /api/macros/{name}` gates
    # the save on `error_count == 0` (http/routes/meta._validation_body), so an
    # error here makes the macro UNSAVABLE through the dashboard over a field
    # this very message calls harmless. Report the drop; do not block on it.
    ignored_by_replay = kind == "screenshot"
    consequence = (
        "replay ignores it, so the action will not do what the field says"
        if ignored_by_replay
        else "replay would fail with TypeError"
    )
    severity = "warning" if ignored_by_replay else "error"
    # key=str: a YAML macro can carry a non-string key (YAML 1.1 resolves a
    # bare `on:`/`yes:`/`no:` to a bool), and a bare sorted() raises TypeError
    # on the mixed set -- an analyzer crashing on its input instead of
    # reporting on it, unlike every other malformed-input guard in this file.
    for field in sorted(unknown_fields(kind, frozenset(action)), key=str):
        issues.append(
            Issue(
                severity=severity,
                code="unknown_field",
                message=(
                    f"action {kind!r} does not accept field {field!r} — "
                    f"{consequence}; check the spelling against the tool's parameters"
                ),
                action_index=outer_index,
            )
        )


def _check_ambiguous_fields(action: dict[str, Any], kind: str, outer_index: int, issues: list[Issue]) -> None:
    """Flag an action carrying both spellings of a renamed field."""
    for recorded, param in ambiguous_rename_fields(kind, frozenset(action)):
        issues.append(
            Issue(
                severity="error",
                code="ambiguous_field",
                message=(
                    f"action {kind!r} carries both {recorded!r} and {param!r}, which are the same field — "
                    "replay keeps whichever comes last in the JSON, so the effective value is not stable; "
                    "keep one"
                ),
                action_index=outer_index,
            )
        )


def _provided_locator_keys(action: dict[str, Any]) -> list[str]:
    """The finders ``build_locator`` counts as set, computed the way it does.

    Membership is ``is not None``, NOT truthiness: ``build_locator`` filters on
    ``v is not None``, so ``text=""`` is a provided finder there. Linting it as
    missing produced an error-severity issue for an action replay accepts, and
    ``PUT /api/macros/{name}`` rejects a macro on any error — making it
    unsavable through the dashboard.
    """
    return sorted(k for k in _ARIA_LOCATOR_KEYS if action.get(k) is not None)


def _check_simple_locator_fields(action: dict[str, Any], kind: str, report: _Report) -> None:
    """Locator ARITY, not just presence.

    ``build_locator`` requires EXACTLY one of role/label/text/test_id. Checking
    only "at least one" let a two-finder ``click_by`` lint clean and then raise
    ``ValueError: exactly one of role/label/text/test_id must be set`` on
    replay — and with no ``selector`` to fall back to, ``_dispatch_click_or_fill``
    re-raises it. Same lint↔replay parity defect that ``role_name``-only had,
    in the other direction.
    """
    if kind not in {"click_by", "fill_by"}:
        return
    provided = _provided_locator_keys(action)
    if not provided:
        report(f"action {kind!r} is missing required locator field (one of role, label, text, or test_id)")
    elif len(provided) > 1:
        report(
            f"action {kind!r} sets {len(provided)} locator fields ({', '.join(provided)}) — "
            "replay requires exactly one of role/label/text/test_id and raises ValueError otherwise; keep one",
            "ambiguous_locator",
        )


def _check_simple_drag_fields(action: dict[str, Any], kind: str, report: _Report) -> None:
    if kind != "drag":
        return
    if not (action.get("source") or action.get("source_selector")):
        report("action 'drag' is missing required field 'source' (or 'source_selector')")
    if not (action.get("target") or action.get("target_selector")):
        report("action 'drag' is missing required field 'target' (or 'target_selector')")


_A11Y_VERIFY_FIELDS: tuple[str, ...] = (
    "verify_js",
    "verify_selector_appears",
    "verify_selector_gone",
    "verify_text_contains",
)


def _check_a11y_dragdrop_verify_arity(action: dict[str, Any], kind: str, report: _Report) -> None:
    """Verify ARITY, mirroring the tool's own validation.

    The linter is the only thing standing between a hand-edited macro and a
    drag step that checks nothing: with zero verify fields the action reports
    success without having confirmed the drop, and with two it is ambiguous
    which one gates success. The tool rejects both at call time; without this
    the macro would lint clean and fail at replay.
    """
    if kind != "a11y_dragdrop":
        return
    provided = [f for f in _A11Y_VERIFY_FIELDS if action.get(f)]
    if len(provided) != 1:
        report(
            f"action 'a11y_dragdrop' requires exactly one verify_* field "
            f"({', '.join(_A11Y_VERIFY_FIELDS)}), got {len(provided)}"
        )


def _check_simple_required_fields(action: dict[str, Any], kind: str, report: _Report) -> None:
    for field in _SIMPLE_REQUIRED[kind]:
        if field not in action or action.get(field) in (None, ""):
            report(f"action {kind!r} is missing required field {field!r}")


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

    # Every dispatchable kind, not just the _SIMPLE_REQUIRED subset: get_text_by,
    # switch_frame, navigate_back and reset_frame are in _ACTION_MAP and in
    # neither. These fail open outside _ACTION_MAP, so conditionals are unaffected.
    _check_unknown_fields(action, kind, outer_index, issues)
    _check_ambiguous_fields(action, kind, outer_index, issues)

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
