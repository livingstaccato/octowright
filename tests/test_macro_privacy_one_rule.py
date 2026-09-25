# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live replay and the exported CLI classify a macro's arguments by one rule.

An argument is sensitive when its name is credential-like, or when it is
substituted into an ``expect_no_text`` ``text`` -- it is then the forbidden text
itself. Before, live replay used the name alone (so ``{{forbidden}}`` came back
in cleartext in ``args_used``) while the export hard-redacted everything feeding
a fill as well (so ``qty='1'`` turned ``#item-1`` into ``#item-<redacted>``).
The export's value-variant function is also rendered from the live one's source.
"""

from __future__ import annotations

import inspect
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.artifacts.script_export import render_macro_cli
from octowright.macros import execution, privacy
from octowright.macros.privacy import MacroArgPrivacy, assertion_text_args

SSN = "123-45-6789"
ACTIONS: list[dict[str, Any]] = [
    {"action": "fill", "selector": "#item-{{qty}}", "value": "{{qty}}"},
    {"action": "click", "selector": "#item-{{qty}} button"},
    {"action": "expect_no_text", "selector": "body", "text": "{{forbidden}}"},
]
ARGS = {"forbidden": SSN, "qty": "1"}


def _exported_module(monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]]) -> dict[str, Any]:
    async_api = types.ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: None  # type: ignore[attr-defined]
    package = types.ModuleType("playwright")
    package.async_api = async_api  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)
    source = render_macro_cli(name="m", macro={"actions": actions})
    module: dict[str, Any] = {"__name__": "m"}
    exec(compile(source, "<m>", "exec"), module)
    module["__source__"] = source
    return module


def _session() -> MagicMock:
    session = MagicMock()
    session.instance_id = "i"
    session.kind = "chromium"
    session.diagnostic_bundle = AsyncMock(return_value={})
    session.get_network_requests = MagicMock(return_value={"requests": []})
    session.page_errors = []
    return session


def test_the_assertion_argument_set_is_positional_and_reaches_nested_calls() -> None:
    nested = [{"action": "if", "then": [{"action": "expect_no_text", "text": "id {{a}} or {{b}}"}]}]
    assert assertion_text_args(ACTIONS) == {"forbidden"}
    assert assertion_text_args(nested) == {"a", "b"}
    assert assertion_text_args([{"action": "fill", "value": "{{password}}"}]) == frozenset()


@pytest.mark.anyio
async def test_live_args_used_redacts_the_forbidden_text_and_keeps_an_ordinary_fill_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": ACTIONS})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_dispatch_one", AsyncMock(return_value=(1, 0)))
    result = await execution.run_macro(_session(), "m", dict(ARGS))
    assert result["args_used"] == {"forbidden": "<redacted>", "qty": "1"}


@pytest.mark.anyio
async def test_live_failure_scrubs_the_forbidden_text_but_not_an_ordinary_fill_arg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise RuntimeError(f"timed out waiting for #item-1 after seeing {SSN}")

    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": ACTIONS})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "_dispatch_one", fail)
    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(_session(), "m", dict(ARGS))
    assert SSN not in str(caught.value)
    assert "#item-1" in str(caught.value)


@pytest.mark.anyio
async def test_a_failed_sequence_step_redacts_its_forbidden_text(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise RuntimeError("boom")

    session = _session()
    session.operation = MagicMock(return_value=AsyncMock())
    monkeypatch.setattr(execution, "load_macro", lambda _name: {"actions": ACTIONS})
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "_dispatch_one", fail)
    result = await execution.run_sequence(session=session, names=["m"], args_list=[dict(ARGS)], stop_on_failure=False)
    assert result["steps"][0]["args_used"] == {"forbidden": "<redacted>", "qty": "1"}


def test_the_export_redacts_the_same_arguments_as_live_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _exported_module(monkeypatch, ACTIONS)
    assert module["_HARD_REDACTED_ARGS"] == frozenset({"forbidden"})
    assert module["_redact_args"](dict(ARGS)) == {"forbidden": "<redacted>", "qty": "1"}
    assert module["_redact_args"](dict(ARGS)) == execution._redact_args_for_response(
        dict(ARGS), MacroArgPrivacy.for_macro(ACTIONS)
    )


def test_the_export_does_not_blind_scrub_an_argument_merely_for_feeding_a_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _exported_module(monkeypatch, ACTIONS)
    # What the script scrubs its log with: the blind-scrub set plus _HARD_REDACTED_ARGS' values.
    scrub = sorted(
        set(module["_blind_scrub_arg_values"](dict(ARGS)))
        | {v for k, v in ARGS.items() if k in module["_HARD_REDACTED_ARGS"]},
        key=lambda value: (-len(value), value),
    )
    assert module["_redact_value"]("clicked #item-1", scrub) == "clicked #item-1"
    assert SSN not in module["_redact_value"](f"page showed {SSN}", scrub)


TRICKY = [
    "plain",
    "Tr0ub4dor&3",
    'quote"d',
    "<tag>",
    "Secret_pa*ss_1",
    "pässwörd-ß",
    "100%-sure",
    "a b+c/d?e=f",
    "emoji-\U0001f511",
    "back\\slash",
    "",
]


@pytest.mark.parametrize("value", TRICKY)
def test_the_exported_variants_equal_the_live_ones(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    module = _exported_module(monkeypatch, [])
    assert tuple(module["_serialized_variants"](value)) == privacy._serialized_variants(value)


def test_the_exported_variants_are_rendered_from_the_live_source(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _exported_module(monkeypatch, [])
    assert inspect.getsource(privacy._serialized_variants).rstrip() in module["__source__"]
