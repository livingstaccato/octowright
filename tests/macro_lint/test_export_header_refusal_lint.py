# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``macro_lint`` warns, at save time, about an inject_headers step an exported script will refuse.

An exported script refuses a credential-named ``inject_headers`` header the
step's ``forward_on_redirect`` does not name, because a script's route lets a
navigation redirect carry it. That refusal came only when the script ran the
step -- and ``macro_run``, which matches navigations per hop, accepts the same
step -- so nothing said so when the macro was saved. Both use one rule
(`credential_sinks.redirect_exposed_credential_headers`).
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.credential_sinks import redirect_exposed_credential_headers
from octowright.http_headers import is_credential_header
from octowright.macros.lint import lint_macro

CODE = "export_refuses_credential_header"


def _inject(headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {"action": "inject_headers", "pattern": "https://app.example.test/**", "headers": headers, **extra}


def _issues(action: dict[str, Any]) -> list[Any]:
    return [i for i in lint_macro({"parameters": ["token"], "actions": [action]}) if i.code == CODE]


@pytest.mark.parametrize("header", ["Authorization", "Cookie", "X-Api-Key"])
def test_a_credential_header_without_the_opt_in_is_a_warning(header: str) -> None:
    (issue,) = _issues(_inject({header: "{{token}}"}))

    assert issue.severity == "warning"
    assert issue.action_index == 0
    assert header in issue.message and "forward_on_redirect" in issue.message
    assert "{{token}}" not in issue.message, "names the header, never its value"


def test_a_step_in_a_branch_is_reported_under_its_outer_step() -> None:
    action = {"action": "try", "actions": [_inject({"Authorization": "Bearer {{token}}"})]}

    (issue,) = _issues(action)

    assert issue.action_index == 0


@pytest.mark.parametrize(
    "action",
    [
        _inject({"Authorization": "Bearer {{token}}"}, forward_on_redirect={"authorization": True}),
        _inject({"X-Env": "staging"}),
        # Malformed: reported as bad_forward_on_redirect, not twice.
        _inject({"Authorization": "x"}, forward_on_redirect={"Authorization": "yes"}),
        {"action": "set_extra_http_headers", "headers": {"Authorization": "x"}},
    ],
)
def test_no_warning_where_the_script_would_not_refuse(action: dict[str, Any]) -> None:
    assert _issues(action) == []


def test_the_script_and_the_lint_share_one_rule() -> None:
    from octowright.artifacts.script_export import render_macro_cli

    source = render_macro_cli(name="m", macro={"actions": []}, include_evidence=False)
    assert "redirect_exposed_credential_headers(action, headers, is_credential_header)" in source
    action = _inject({"Authorization": "a", "X-Api-Key": "b", "X-Env": "c"}, forward_on_redirect={"X-API-KEY": True})
    assert redirect_exposed_credential_headers(action, action["headers"], is_credential_header) == ["Authorization"]
