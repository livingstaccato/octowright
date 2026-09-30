# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""One walk answers "what is nested in these actions", and a run reads each macro once.

A ``macro_call`` used to be read from disk up to three times per dispatch --
the network-clean scan, the nested call's privacy step and the dispatch itself
-- each a synchronous parse on the event loop, and the scan deep-copied the
called macro through ``substitute`` just to look at its action kinds.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution, substitution
from octowright.macros.calls import actions_assert_network_clean
from octowright.macros.nesting import RunMacros, iter_nested_actions
from octowright.macros.privacy import MacroArgPrivacy
from octowright.session.core import BrowserSession


def _counting_loader(macros: dict[str, list[dict[str, Any]]]) -> tuple[Any, Counter[str]]:
    reads: Counter[str] = Counter()

    def load(name: str) -> dict[str, Any]:
        reads[name] += 1
        if name not in macros:
            raise FileNotFoundError(name)
        return {"name": name, "actions": macros[name]}

    return load, reads


def _session(tmp_path: Any) -> BrowserSession:
    session = BrowserSession(
        instance_id="t",
        kind="chromium",
        label="t",
        url="https://app.example.test/",
        launch_url="https://app.example.test/",
        page=AsyncMock(),
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )
    session.inject_headers = AsyncMock(return_value={})  # type: ignore[method-assign]
    return session


_CHILD = [{"action": "inject_headers", "pattern": "https://app.example.test/**", "headers": {"X-Q": "{{q}}"}}]


@pytest.mark.anyio
async def test_a_macro_call_reads_the_called_macro_once(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    load, reads = _counting_loader(
        {
            "parent": [
                {"action": "macro_call", "name": "child", "args": {"q": "1"}},
                {"action": "macro_call", "name": "child", "args": {"q": "2"}},
            ],
            "child": _CHILD,
        }
    )
    monkeypatch.setattr(execution, "load_macro", load)
    await execution.run_macro(_session(tmp_path), "parent", {})
    assert reads == {"parent": 1, "child": 1}


@pytest.mark.anyio
async def test_a_sequence_reads_each_member_once_even_when_one_fails(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    load, reads = _counting_loader({"m": [{"action": "macro_call", "name": "missing"}]})
    monkeypatch.setattr(execution, "load_macro", load)
    result = await execution.run_sequence(
        session=_session(tmp_path), names=["m", "m"], args_list=[{}, {}], stop_on_failure=False
    )
    assert [step["ok"] for step in result["steps"]] == [False, False]
    assert reads == {"m": 1, "missing": 1}


def test_the_scan_reads_a_called_macro_raw_when_nothing_it_reads_is_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("substitute called")

    monkeypatch.setattr(substitution, "substitute", refuse)
    macros = {"child": [{"action": "expect_network_clean"}, {"action": "fill", "value": "{{q}}"}]}
    assert actions_assert_network_clean(
        [{"action": "macro_call", "name": "child", "args": {"q": "x"}}], lambda name: {"actions": macros[name]}
    )


def test_the_scan_expands_a_call_name_that_is_a_placeholder() -> None:
    macros = {
        "outer": [{"action": "macro_call", "name": "{{which}}"}],
        "inner": [{"action": "try", "actions": [{"action": "expect_network_clean"}]}],
    }
    call = [{"action": "macro_call", "name": "outer", "args": {"which": "inner"}}]
    assert actions_assert_network_clean(call, lambda name: {"actions": macros[name]})


def test_without_a_loader_calls_are_not_followed() -> None:
    macros = {"child": [{"action": "expect_no_text", "text": "{{forbidden}}"}]}
    call = [{"action": "macro_call", "name": "child"}]
    assert [a["action"] for a in iter_nested_actions(call)] == ["macro_call"]
    followed = iter_nested_actions(call, load_macro=lambda name: {"actions": macros[name]})
    assert [a["action"] for a in followed] == ["macro_call", "expect_no_text"]
    assert MacroArgPrivacy.for_macro(call).assertion_args == frozenset()


def test_run_macros_remembers_a_failed_load() -> None:
    load, reads = _counting_loader({})
    macros = RunMacros(load)
    for _ in range(2):
        with pytest.raises(FileNotFoundError):
            macros("gone")
    assert reads == {"gone": 1}


def test_the_privacy_view_is_positional_where_the_name_only_view_is_not() -> None:
    args = {"forbidden": "123-45-6789", "qty": "1"}
    view = MacroArgPrivacy.for_macro([{"action": "expect_no_text", "text": "{{forbidden}}"}])
    assert view.redact(args) == {"forbidden": "<redacted>", "qty": "1"}
    assert view.blind_scrub(args) == ("123-45-6789",)
    assert MacroArgPrivacy().redact(args) == args
