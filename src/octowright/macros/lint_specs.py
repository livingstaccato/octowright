# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``macro_lint`` says about a macro's ``parameter_specs`` (#248, Part B).

Every finding is a warning: a malformed or ignored declaration never stops a
run, which classifies the parameter by its name instead
(`parameter_specs.resolve_macro_privacy`). Pure; returns ``(code, message)``
pairs that `lint.lint_macro` turns into whole-macro issues.
"""

from __future__ import annotations

from typing import Any

from octowright.macros.parameter_specs import SPECS_KEY, declared_sensitivity, resolve_macro_privacy


def _parameter_names(macro: dict[str, Any]) -> set[str] | None:
    raw = macro.get("parameters")
    if isinstance(raw, dict):
        return {str(name) for name in raw}
    if isinstance(raw, list):
        return {name for name in raw if isinstance(name, str)}
    return None


def lint_parameter_specs(macro: dict[str, Any]) -> list[tuple[str, str]]:
    declared = declared_sensitivity(macro)
    findings = [("bad_parameter_specs", problem) for problem in declared.problems]
    # A run adds what the caller passed as a credential; statically only the
    # name and the expect_no_text position can hold an unmark down.
    findings.extend(("ignored_public_declaration", warning) for warning in resolve_macro_privacy(macro).ignored)
    names = _parameter_names(macro)
    if names is not None:
        for name in sorted((declared.sensitive | declared.public) - names):
            findings.append(
                (
                    "unknown_parameter_spec",
                    f"{SPECS_KEY} names {name!r}, which is not one of the macro's parameters; "
                    "only a top-level parameter can be declared",
                )
            )
    return findings


def sensitive_parameters(macro: dict[str, Any]) -> frozenset[str]:
    """The top-level parameters *macro* resolves as sensitive: by name, position or declaration."""
    privacy = resolve_macro_privacy(macro).privacy
    declared = declared_sensitivity(macro)
    names = (_parameter_names(macro) or set()) | declared.sensitive | declared.public
    return frozenset(name for name in names if privacy.tier_of(name) is not None)


def lint_sensitivity_shrink(macro: dict[str, Any], previous: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Warn when *macro* makes a parameter it still takes less sensitive than *previous* (the version on disk).

    A parameter the new version no longer takes receives no value, so dropping
    it shrinks nothing.
    """
    if not isinstance(previous, dict):
        return []
    kept = _parameter_names(macro) or set()
    shrank = sorted((sensitive_parameters(previous) - sensitive_parameters(macro)) & kept)
    if not shrank:
        return []
    names = ", ".join(repr(name) for name in shrank)
    return [
        (
            "sensitive_parameters_shrank",
            f"parameter(s) {names} were sensitive in the saved version and are not in this one, so their "
            "values will be shown in run results and left out of the scrub set; declare them "
            f'{{"sensitive": true}} in {SPECS_KEY} if that is not intended',
        )
    ]
