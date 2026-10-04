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
