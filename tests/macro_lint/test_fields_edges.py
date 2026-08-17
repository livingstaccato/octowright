# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Edge/defensive branches of ``lint_fields`` the happy-path tests don't reach.

Each of these is a real guard, not incidental code: a kind that resolves to a
nonexistent method, a VAR_POSITIONAL parameter, and an action kind the
dispatch map has never heard of. If any of these guards is wrong, a future
edit to ``_ACTION_MAP``/``BrowserSession`` fails open (everything allowed)
rather than failing closed (nothing allowed) -- the opposite of what a
credential-adjacent validator should do under uncertainty.
"""

from __future__ import annotations

import pytest

from octowright.macros import lint_fields
from octowright.macros.lint_fields import _session_method_params, allowed_fields_for, unknown_fields


def test_session_method_params_returns_none_for_nonexistent_method() -> None:
    assert _session_method_params("this_method_does_not_exist_on_browser_session") is None


def test_session_method_params_ignores_var_positional(monkeypatch: pytest.MonkeyPatch) -> None:
    """A *args parameter must be skipped, not treated as a named field."""

    def fake_method(self, *args: object, selector: str) -> None: ...

    from octowright.session import BrowserSession

    monkeypatch.setattr(BrowserSession, "__lint_fields_test_fake__", fake_method, raising=False)
    result = _session_method_params("__lint_fields_test_fake__")
    assert result is not None
    names, takes_kwargs = result
    assert names == {"selector"}
    assert takes_kwargs is False


def test_allowed_fields_for_unknown_kind_is_empty() -> None:
    """A kind absent from _ACTION_MAP can't be reasoned about -> empty, not everything."""
    assert allowed_fields_for("this_action_kind_does_not_exist") == frozenset()


def test_allowed_fields_for_kind_mapping_to_nonexistent_method_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """_ACTION_MAP pointing at a method that no longer exists must fail closed."""
    monkeypatch.setitem(lint_fields._ACTION_MAP, "__fake_dangling_kind__", "__no_such_session_method__")
    assert allowed_fields_for("__fake_dangling_kind__") == frozenset()


def test_unknown_fields_for_unreasoned_kind_is_empty_not_everything() -> None:
    """'don't check' (empty diff) is the safe failure mode for an unknown kind."""
    assert unknown_fields("this_action_kind_does_not_exist", frozenset({"anything", "at", "all"})) == frozenset()
