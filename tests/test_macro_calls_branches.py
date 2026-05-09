# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.macros.calls (nested macro_call dispatch).

Targets the 13 surviving mutmut mutants by pinning every branch in
`validate_macro_call_shape`, `format_macro_chain`, `dispatch_macro_call`
(recursion guard + depth guard + executed/skipped accumulation), and the
plain-action passthrough. Mutations survive when callers test only the
happy path without asserting depth/cycle errors verbatim.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from octowright.macros.calls import (
    MAX_MACRO_CALL_DEPTH,
    dispatch_macro_call,
    dispatch_plain_action,
    format_macro_chain,
    validate_macro_call_shape,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ─── format_macro_chain ──────────────────────────────────────────────────────


class TestFormatMacroChain:
    def test_empty_stack_yields_just_next_name(self) -> None:
        """`[*[], 'x']` → ['x'] → join 'x'."""
        assert format_macro_chain([], "x") == "x"

    def test_single_stack_joined_with_arrow(self) -> None:
        """' -> '.join — mutating the separator would change format."""
        assert format_macro_chain(["a"], "b") == "a -> b"

    def test_multi_stack_joins_every_element(self) -> None:
        """The unpacking [*stack, next_name] must include every entry."""
        assert format_macro_chain(["a", "b", "c"], "d") == "a -> b -> c -> d"


# ─── validate_macro_call_shape ───────────────────────────────────────────────


class TestValidateMacroCallShape:
    def test_returns_name_and_default_empty_args(self) -> None:
        """No 'args' key → empty dict."""
        name, args = validate_macro_call_shape({"name": "foo"})
        assert name == "foo"
        assert args == {}

    def test_returns_name_and_args_when_present(self) -> None:
        """args dict round-trips."""
        name, args = validate_macro_call_shape({"name": "foo", "args": {"k": 1}})
        assert name == "foo"
        assert args == {"k": 1}

    def test_missing_name_raises_value_error(self) -> None:
        """No 'name' key → ValueError mentioning the prefix."""
        with pytest.raises(ValueError) as exc:
            validate_macro_call_shape({})
        assert "macro_call action missing required 'name' field" in str(exc.value)

    def test_non_string_name_raises_value_error(self) -> None:
        """Non-string name → ValueError mentioning 'non-empty string'."""
        with pytest.raises(ValueError) as exc:
            validate_macro_call_shape({"name": 42})
        assert "non-empty string" in str(exc.value)

    def test_empty_string_name_raises_value_error(self) -> None:
        """Empty string fails the `not action['name']` clause."""
        with pytest.raises(ValueError) as exc:
            validate_macro_call_shape({"name": ""})
        assert "non-empty string" in str(exc.value)

    def test_non_dict_args_raises_value_error(self) -> None:
        """args present but not a dict → ValueError mentioning 'must be a dict'."""
        with pytest.raises(ValueError) as exc:
            validate_macro_call_shape({"name": "foo", "args": [1, 2]})
        assert "must be a dict" in str(exc.value)

    def test_string_args_raises_value_error(self) -> None:
        """args=str → still rejected."""
        with pytest.raises(ValueError) as exc:
            validate_macro_call_shape({"name": "foo", "args": "k=v"})
        assert "must be a dict" in str(exc.value)


# ─── dispatch_macro_call: helpers + happy path ──────────────────────────────


def _make_dispatch(events: list[Any]) -> Any:
    """Build a fake dispatch_one that records calls and returns (1, 0)."""

    async def dispatch(session: Any, action: dict[str, Any], **kwargs: Any) -> tuple[int, int]:
        events.append({"action": action, "stack": kwargs.get("invocation_stack")})
        return (1, 0)

    return dispatch


def _identity_substitute(actions: list[dict[str, Any]], _args: dict[str, Any]) -> list[dict[str, Any]]:
    return actions


class TestDispatchMacroCallHappy:
    @pytest.mark.anyio
    async def test_returns_executed_count_includes_self_plus_subactions(self) -> None:
        """The +1 for the macro_call itself plus per-subaction (1, 0) returns."""
        events: list[Any] = []
        executed, skipped = await dispatch_macro_call(
            session=MagicMock(),
            action={"name": "child"},
            invocation_stack=["parent"],
            max_depth=10,
            load_macro=lambda _name: {"name": "child", "actions": [{"action": "click"}, {"action": "fill"}]},
            substitute=_identity_substitute,
            dispatch_one=_make_dispatch(events),
        )
        # 1 (the macro_call itself) + 2 children → 3 executed, 0 skipped.
        assert (executed, skipped) == (3, 0)
        assert len(events) == 2

    @pytest.mark.anyio
    async def test_invocation_stack_extended_with_called_name(self) -> None:
        """Each child sees [*invocation_stack, called_name] as its stack."""
        events: list[Any] = []
        await dispatch_macro_call(
            session=MagicMock(),
            action={"name": "child"},
            invocation_stack=["parent"],
            max_depth=10,
            load_macro=lambda _name: {"actions": [{"action": "click"}]},
            substitute=_identity_substitute,
            dispatch_one=_make_dispatch(events),
        )
        assert events[0]["stack"] == ["parent", "child"]

    @pytest.mark.anyio
    async def test_substitute_called_with_loaded_actions_and_args(self) -> None:
        """substitute receives the loaded macro's `actions` and the call args."""
        captured: list[Any] = []

        def my_substitute(actions: list[dict[str, Any]], args: dict[str, Any]) -> list[dict[str, Any]]:
            captured.append((actions, args))
            return actions

        await dispatch_macro_call(
            session=MagicMock(),
            action={"name": "child", "args": {"k": "v"}},
            invocation_stack=[],
            max_depth=10,
            load_macro=lambda _name: {"actions": [{"action": "click"}]},
            substitute=my_substitute,
            dispatch_one=_make_dispatch([]),
        )
        assert captured == [([{"action": "click"}], {"k": "v"})]

    @pytest.mark.anyio
    async def test_load_macro_called_with_called_name(self) -> None:
        """load_macro receives the value of action['name'], verbatim."""
        captured: list[str] = []

        def my_loader(name: str) -> dict[str, Any]:
            captured.append(name)
            return {"actions": []}

        await dispatch_macro_call(
            session=MagicMock(),
            action={"name": "the-target"},
            invocation_stack=[],
            max_depth=10,
            load_macro=my_loader,
            substitute=_identity_substitute,
            dispatch_one=_make_dispatch([]),
        )
        assert captured == ["the-target"]

    @pytest.mark.anyio
    async def test_macro_with_no_actions_key_returns_only_self_count(self) -> None:
        """No 'actions' field on loaded macro → executed=1, skipped=0."""
        executed, skipped = await dispatch_macro_call(
            session=MagicMock(),
            action={"name": "child"},
            invocation_stack=[],
            max_depth=10,
            load_macro=lambda _: {},
            substitute=_identity_substitute,
            dispatch_one=_make_dispatch([]),
        )
        assert (executed, skipped) == (1, 0)


class TestDispatchMacroCallGuards:
    @pytest.mark.anyio
    async def test_recursion_cycle_raises(self) -> None:
        """Calling 'a' while 'a' is already on the stack → recursion error."""
        with pytest.raises(RuntimeError) as exc:
            await dispatch_macro_call(
                session=MagicMock(),
                action={"name": "a"},
                invocation_stack=["a", "b"],
                max_depth=10,
                load_macro=lambda _: {"actions": []},
                substitute=_identity_substitute,
                dispatch_one=_make_dispatch([]),
            )
        msg = str(exc.value)
        assert "recursion detected" in msg
        assert "a -> b -> a" in msg

    @pytest.mark.anyio
    async def test_depth_limit_raises_with_chain(self) -> None:
        """Stack depth = max → depth-exceeded error mentioning the chain and limit."""
        with pytest.raises(RuntimeError) as exc:
            await dispatch_macro_call(
                session=MagicMock(),
                action={"name": "x"},
                invocation_stack=["a", "b", "c"],
                max_depth=3,
                load_macro=lambda _: {"actions": []},
                substitute=_identity_substitute,
                dispatch_one=_make_dispatch([]),
            )
        msg = str(exc.value)
        assert "recursion depth exceeded" in msg
        assert "(3)" in msg
        assert "a -> b -> c -> x" in msg

    @pytest.mark.anyio
    async def test_max_depth_none_uses_default_constant(self) -> None:
        """max_depth=None → MAX_MACRO_CALL_DEPTH used."""
        # Stack at MAX_MACRO_CALL_DEPTH triggers depth error even when max_depth=None.
        stack = ["m" + str(i) for i in range(MAX_MACRO_CALL_DEPTH)]
        with pytest.raises(RuntimeError) as exc:
            await dispatch_macro_call(
                session=MagicMock(),
                action={"name": "x"},
                invocation_stack=stack,
                max_depth=None,
                load_macro=lambda _: {"actions": []},
                substitute=_identity_substitute,
                dispatch_one=_make_dispatch([]),
            )
        assert f"({MAX_MACRO_CALL_DEPTH})" in str(exc.value)

    @pytest.mark.anyio
    async def test_depth_just_below_limit_is_ok(self) -> None:
        """len(stack) = max - 1 must succeed (boundary off-by-one guard)."""
        stack = ["a", "b"]
        executed, _ = await dispatch_macro_call(
            session=MagicMock(),
            action={"name": "x"},
            invocation_stack=stack,
            max_depth=3,  # boundary: 2 < 3 OK
            load_macro=lambda _: {"actions": []},
            substitute=_identity_substitute,
            dispatch_one=_make_dispatch([]),
        )
        assert executed == 1

    @pytest.mark.anyio
    async def test_validation_error_propagates(self) -> None:
        """validate_macro_call_shape errors aren't swallowed."""
        with pytest.raises(ValueError):
            await dispatch_macro_call(
                session=MagicMock(),
                action={},  # missing 'name'
                invocation_stack=[],
                max_depth=10,
                load_macro=lambda _: {"actions": []},
                substitute=_identity_substitute,
                dispatch_one=_make_dispatch([]),
            )


# ─── dispatch_plain_action ───────────────────────────────────────────────────


class TestDispatchPlainAction:
    @pytest.mark.anyio
    async def test_passes_through_to_runtime_dispatch_simple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direct passthrough to runtime.dispatch_simple with same kwargs."""
        captured: dict[str, Any] = {}

        async def fake_runtime(session: Any, action: dict[str, Any], **kwargs: Any) -> tuple[int, int]:
            captured["session"] = session
            captured["action"] = action
            captured["kwargs"] = kwargs
            return (1, 0)

        from octowright.macros import calls as _calls

        monkeypatch.setattr(_calls, "runtime_dispatch_simple", fake_runtime)

        keys = ("role", "label")
        strip = MagicMock()
        kwargs_helper = MagicMock()
        result = await dispatch_plain_action(
            session=MagicMock(name="sess"),
            action={"action": "click", "selector": "#x"},
            semantic_keys=keys,
            strip_non_aria_noise=strip,
            action_kwargs=kwargs_helper,
        )
        assert result == (1, 0)
        assert captured["action"] == {"action": "click", "selector": "#x"}
        assert captured["kwargs"]["semantic_keys"] is keys
        assert captured["kwargs"]["strip_non_aria_noise"] is strip
        assert captured["kwargs"]["action_kwargs"] is kwargs_helper
