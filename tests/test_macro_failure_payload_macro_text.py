# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A failure payload echoes the macro as written, and scrubs what the page said.

A failure payload is scrubbed of every run and session-ledger value anywhere,
so an echo glued to identifier characters (``hunter2-reset``) cannot reach the
client (``test_typed_password_ledger_bounds``). Applied to every field, that
rule also rewrote the macro's own text: a session-typed password that is an
ordinary word (``admin``) turned ``failed_action.selector`` and
``executed_actions`` into ``#<redacted>-menu``, and ``_suggest_fix`` was handed
the mangled selector. The fields that echo the macro's definition are the text
the client already holds, so they show the action as written -- a placeholder
stays ``{{name}}`` rather than being substituted and then scrubbed -- and only
page-derived text is ledger-scrubbed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution, privacy

TYPED = "admin"  # pragma: allowlist secret -- a fixture, never a real credential
PASSWORD = "fixture-not-a-real-password"  # pragma: allowlist secret -- a fixture


def _session() -> MagicMock:
    session = MagicMock()
    session.durable_text_scrubber = None
    session.launch_url = None
    session.base_url = None
    session.diagnostic_bundle = AsyncMock(return_value={})
    session.page_errors = []
    session.get_network_requests = MagicMock(return_value={"requests": []})
    session.snapshot = AsyncMock(side_effect=RuntimeError("no snapshot"))
    return session


async def _fail_at(
    monkeypatch: pytest.MonkeyPatch,
    session: MagicMock,
    actions: list[dict[str, Any]],
    *,
    fail_index: int,
    error: str,
    args: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run *actions*, failing step *fail_index* with *error*; the payload and what dispatch saw."""
    dispatched: list[dict[str, Any]] = []

    async def dispatch(_session: Any, action: dict[str, Any], **_kwargs: Any) -> tuple[int, int]:
        dispatched.append(action)
        if len(dispatched) - 1 == fail_index:
            raise RuntimeError(error)
        return (1, 0)

    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": actions})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_dispatch_one", dispatch)
    with pytest.raises(RuntimeError) as raised:
        await execution._run_macro_impl(session, "m", args, slowmo_ms=0)
    return raised.value.args[0], dispatched


async def test_a_typed_ordinary_word_does_not_rewrite_the_macros_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    privacy.admit_redacted_input(session, TYPED)
    actions = [{"action": "click", "selector": "#admin-home"}, {"action": "click", "selector": "#admin-menu"}]

    payload, _ = await _fail_at(monkeypatch, session, actions, fail_index=1, error="no element")

    assert payload["failed_action"] == {"action": "click", "selector": "#admin-menu"}
    assert payload["executed_actions"] == [{"action": "click", "selector": "#admin-home"}]


async def test_the_same_word_from_the_page_is_still_scrubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session()
    privacy.admit_redacted_input(session, TYPED)
    session.page_errors = [{"message": "TypeError: admin-token undefined"}]
    body = '{"error": "account admin_panel locked"}'
    session.get_network_requests = MagicMock(
        return_value={"requests": [{"url": "https://app.test/api", "status": 409, "response_body": body}]}
    )
    actions = [{"action": "click", "selector": "#admin-menu"}]

    payload, _ = await _fail_at(monkeypatch, session, actions, fail_index=0, error="login as admin-x failed")

    assert TYPED not in payload["original"], payload["original"]
    assert TYPED not in repr(payload["page_errors"]), payload["page_errors"]
    assert TYPED not in repr(payload["failed_requests"]), payload["failed_requests"]
    assert payload["failed_action"]["selector"] == "#admin-menu"


async def test_a_placeholder_is_shown_unsubstituted_never_as_its_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """The step dispatched the value; the payload shows the placeholder the macro wrote."""
    session = _session()
    actions = [
        {"action": "click", "selector": "#row-{{order}}"},
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
    ]

    payload, dispatched = await _fail_at(
        monkeypatch,
        session,
        actions,
        fail_index=1,
        error=f"fill rejected {PASSWORD}",
        args={"order": "o-4411", "password": PASSWORD},
    )

    assert dispatched[0]["selector"] == "#row-o-4411", "dispatch still gets the substituted step"
    assert payload["executed_actions"] == [{"action": "click", "selector": "#row-{{order}}"}]
    assert payload["failed_action"] == {"action": "fill", "selector": "#pw", "value": "<redacted>"}
    assert PASSWORD not in repr(payload)


async def test_a_literal_credential_argument_of_a_called_macro_is_redacted_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step as written may carry a literal credential in a call's ``args``;
    it is redacted by argument name, the rule ``args_used`` follows."""
    session = _session()
    actions = [{"action": "macro_call", "name": "m", "args": {"password": PASSWORD, "who": "ops"}}]

    payload, _ = await _fail_at(monkeypatch, session, actions, fail_index=0, error="child failed")

    assert payload["failed_action"]["args"] == {"password": "<redacted>", "who": "ops"}
    assert PASSWORD not in repr(payload)


async def test_the_healing_suggestion_names_the_selector_as_written(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real ``suggest_fix``: its selector is the macro's, its a11y tree is the page's."""
    session = _session()
    privacy.admit_redacted_input(session, TYPED)
    session.snapshot = AsyncMock(return_value={"aria": "- button 'admin'\n- link 'admin-panel'"})
    actions = [{"action": "click", "selector": "#admin-menu"}]

    payload, _ = await _fail_at(monkeypatch, session, actions, fail_index=0, error="no element")

    healing = payload["healing_suggestion"]
    assert "'#admin-menu' failed" in healing, healing
    tree = healing.split("---")[1]
    assert TYPED not in tree, tree
