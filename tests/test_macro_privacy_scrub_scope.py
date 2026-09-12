# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The value-scrub set must carry data, not the field names that structure it.

``sensitive_arg_values`` feeds a blind substring scrub over every diagnostic
surface. A structural key name collected into that set ("name", "role", "id")
therefore rewrites unrelated failure text, which is exactly what the macro
failure bundle exists to preserve.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros.privacy import scrub_sensitive_values, sensitive_arg_values

STRUCTURAL_ARGS = {"user": {"name": "a4-subject-canary", "role": "a4-role-canary"}}
DIAGNOSTIC = "timeout waiting for selector [name=q] role=listbox id=main"


def _exported_module(name: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name=name, macro={"actions": []})
    module: dict[str, Any] = {"__name__": name.replace("-", "_")}
    exec(compile(source, f"<{name}>", "exec"), module)
    return module


def test_structural_key_names_below_a_classified_branch_are_not_scrub_tokens() -> None:
    values = sensitive_arg_values(STRUCTURAL_ARGS)

    assert "a4-subject-canary" in values
    assert "a4-role-canary" in values
    assert "name" not in values
    assert "role" not in values


def test_a_classified_branch_does_not_rewrite_unrelated_failure_text() -> None:
    values = sensitive_arg_values(STRUCTURAL_ARGS)

    assert scrub_sensitive_values(DIAGNOSTIC, values) == DIAGNOSTIC


def test_identity_shaped_keys_below_a_classified_branch_stay_scrubbed() -> None:
    args = {"credential": {"a4-keyed-canary@example.test": "A4-KEYED-VALUE-CANARY"}}

    values = sensitive_arg_values(args)

    assert "a4-keyed-canary@example.test" in values
    assert "A4-KEYED-VALUE-CANARY" in values


def test_exported_classifier_matches_runtime_on_structural_key_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _exported_module("privacy-scrub-scope", monkeypatch)

    exported = tuple(module["_sensitive_arg_values"](STRUCTURAL_ARGS))

    assert exported == sensitive_arg_values(STRUCTURAL_ARGS)
    assert module["_redact_value"](DIAGNOSTIC, list(exported)) == DIAGNOSTIC
