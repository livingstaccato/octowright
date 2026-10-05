# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A macro's written twins are built only when it has a ``try`` to report them.

``register_written_steps`` built the redacted written form of every step, at
every depth, on every run and every ``macro_call``, so that a ``try`` that
suppresses a step can record it as written. Only a ``try``'s own direct
steps are ever looked up, and they sit in the same macro as the ``try``, so a
macro without one paid for a registry nothing reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright import conditional
from octowright.macros import failure_context
from octowright.macros.privacy import MacroArgPrivacy

PLAIN = [
    {"action": "click", "selector": "#a"},
    {"action": "if_selector", "selector": "#b", "then": [{"action": "click", "selector": "#c"}]},
]
NESTED_TRY = [
    {
        "action": "if_selector",
        "selector": "#b",
        "then": [{"action": "try", "actions": [{"action": "click", "selector": "#c"}]}],
    }
]


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    real = failure_context.written_actions

    def spy(actions: Any, privacy_for: Any) -> Any:
        calls.append(actions)
        return real(actions, privacy_for)

    monkeypatch.setattr(failure_context, "written_actions", spy)
    return calls


def test_a_macro_without_try_builds_no_written_twins(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _spy(monkeypatch)
    with conditional.written_steps_scope():
        failure_context.register_written_steps(PLAIN, PLAIN, lambda _name: MacroArgPrivacy())
    assert calls == []


@pytest.mark.parametrize(
    "actions", [NESTED_TRY, [{"action": "try", "actions": [{"action": "click", "selector": "#x"}]}]]
)
def test_a_try_anywhere_still_registers_its_steps(monkeypatch: pytest.MonkeyPatch, actions: list[Any]) -> None:
    calls = _spy(monkeypatch)
    expanded = [dict(step) for step in actions]
    with conditional.written_steps_scope():
        failure_context.register_written_steps(actions, expanded, lambda _name: MacroArgPrivacy())
        assert calls == [actions]
        assert conditional.written_step(expanded[0]) == actions[0]
