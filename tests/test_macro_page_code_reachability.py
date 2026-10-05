# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Page code is refused up front only where it will run; a branch is judged when it is taken.

The up-front refusal walked every container, so a credential macro whose
page code sat in an ``if_selector`` branch the page never took was refused
before it started. Both replay and the exported CLI judge each step as it is
dispatched, so the up-front walk now covers only the steps a run reaches
whenever it gets that far -- top-level steps, a ``try``'s steps and a
``try_each``'s first branch -- and a conditional branch is refused when it is
taken. A ``macro_run_sequence``'s credential set still applies to every step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from octowright import conditional
from octowright.artifacts.script_export import render_macro_cli
from octowright.credential_sinks import refuse_page_code
from tests.macro_lint.test_cli_export_execution import _install, _Recorder
from tests.test_macro_credential_fill_origin import SECRET
from tests.test_macro_credential_page_code import FILL, LISTEN, _code_calls, _code_session, _run
from tests.test_macro_sequence_page_code import _sequence

BRANCH = {"action": "if_selector", "selector": "#banner", "then": [LISTEN]}


def _present(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
    async def _selector_present(_session: Any, _selector: str, _timeout_ms: int) -> bool:
        return present

    monkeypatch.setattr(conditional, "selector_present", _selector_present)


# --- the up-front walk ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "actions",
    [
        [FILL, BRANCH],
        [FILL, {"action": "if_selector", "selector": "#x", "else": [LISTEN]}],
        [FILL, {"action": "try_each", "branches": [[{"action": "click", "selector": "#a"}], [LISTEN]]}],
    ],
)
def test_page_code_in_a_conditional_branch_is_left_to_dispatch(actions: list[dict[str, Any]]) -> None:
    refuse_page_code(actions, ["password"])


@pytest.mark.parametrize(
    "actions",
    [
        [FILL, LISTEN],
        [FILL, {"action": "try", "actions": [LISTEN]}],
        [FILL, {"action": "try_each", "branches": [[LISTEN], [{"action": "click", "selector": "#a"}]]}],
        [FILL, {"action": "try", "actions": [{"action": "try", "actions": [LISTEN]}]}],
    ],
)
def test_page_code_every_run_reaches_is_still_refused_up_front(actions: list[dict[str, Any]]) -> None:
    with pytest.raises(ValueError, match="page code"):
        refuse_page_code(actions, ["password"])


# --- live replay ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_an_untaken_branch_no_longer_refuses_the_run(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _code_session(tmp_path)
    _present(monkeypatch, False)

    result = await _run(monkeypatch, session, {"outer": [FILL, BRANCH]})

    assert result["executed"] == 2  # the fill and the if_selector, which took its absent else
    session.fill.assert_awaited_once()
    assert _code_calls(session) == []


@pytest.mark.anyio
async def test_a_taken_branch_is_refused_when_it_is_dispatched(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _code_session(tmp_path)
    _present(monkeypatch, True)

    with pytest.raises((RuntimeError, ValueError), match=r"page code.*\{\{password\}\}") as caught:
        await _run(monkeypatch, session, {"outer": [FILL, BRANCH]})

    assert _code_calls(session) == []
    assert SECRET not in str(caught.value)


@pytest.mark.anyio
@pytest.mark.parametrize(("taken", "refused"), [(True, True), (False, False)])
async def test_the_sequence_credential_set_still_applies_to_a_branch(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, taken: bool, refused: bool
) -> None:
    """The probe step carries no credential of its own; the sequence's login step does."""
    session = _code_session(tmp_path)
    _present(monkeypatch, taken)

    result = await _sequence(monkeypatch, session, {"login": [FILL], "probe": [BRANCH]}, ["login", "probe"])

    probe = result["steps"][1]
    assert probe["ok"] is (not refused)
    if refused:
        assert "runs page code" in probe["error"] and "{{password}}" in probe["error"]
    session.evaluate.assert_not_awaited()


# --- the exported CLI ----------------------------------------------------------------------


def _script(monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], _Recorder]:
    rec = _Recorder()
    _install(monkeypatch, rec)
    source = render_macro_cli(
        name="guarded", macro={"parameters": ["password"], "actions": actions}, include_evidence=False
    )
    namespace: dict[str, Any] = {}
    exec(source, namespace)  # executing the generated artefact is the point
    return namespace, rec


def _run_script(namespace: dict[str, Any]) -> Any:
    return asyncio.run(namespace["run_guarded"](password=SECRET, trusted_origins=("https://app.example.test",)))


LOGIN = [{"action": "navigate", "url": "https://app.example.test/login"}, FILL]


def test_the_script_no_longer_refuses_up_front_for_an_untaken_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The script has no conditionals, so it stops at the branch -- but not with a page-code refusal."""
    namespace, rec = _script(monkeypatch, [*LOGIN, BRANCH])

    with pytest.raises(RuntimeError, match="unsupported macro action") as caught:
        _run_script(namespace)

    assert "page code" not in str(caught.value)
    assert "goto" in rec.names(), "the browser ran the steps before the branch"


def test_the_script_refuses_page_code_when_it_is_dispatched(monkeypatch: pytest.MonkeyPatch) -> None:
    """The up-front walk taken away, the dispatch-time guard still refuses the step."""
    namespace, rec = _script(monkeypatch, [*LOGIN, LISTEN])
    namespace["refuse_page_code"] = lambda *_a, **_kw: None

    with pytest.raises(RuntimeError, match=r"page code.*\{\{password\}\}") as caught:
        _run_script(namespace)

    assert "evaluate" not in rec.names()
    assert "handle.fill" in rec.names(), "refused at its own step, after the fill ran"
    assert SECRET.lower() not in str(caught.value).lower()
