# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Unit tests for octowright.macros.calls.

Targets the validation and dispatch helpers directly so that the specific
error-raise branches (lines 27 and 31) are covered without needing to run a
full macro execution pipeline.
"""

from __future__ import annotations

from typing import Any

import pytest

from octowright.macros.calls import (
    MAX_MACRO_CALL_DEPTH,
    dispatch_macro_call,
    format_macro_chain,
    validate_macro_call_shape,
)

# ---------------------------------------------------------------------------
# format_macro_chain
# ---------------------------------------------------------------------------


class TestFormatMacroChain:
    def test_empty_stack(self) -> None:
        assert format_macro_chain([], "leaf") == "leaf"

    def test_single_item_stack(self) -> None:
        assert format_macro_chain(["root"], "child") == "root -> child"

    def test_multi_item_stack(self) -> None:
        assert format_macro_chain(["a", "b", "c"], "d") == "a -> b -> c -> d"


# ---------------------------------------------------------------------------
# validate_macro_call_shape
# ---------------------------------------------------------------------------


class TestValidateMacroCallShape:
    def test_valid_name_returns_tuple(self) -> None:
        name, args = validate_macro_call_shape({"name": "my-macro"})
        assert name == "my-macro"
        assert args == {}

    def test_valid_name_with_args(self) -> None:
        name, args = validate_macro_call_shape({"name": "my-macro", "args": {"key": "val"}})
        assert name == "my-macro"
        assert args == {"key": "val"}

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValueError, match="missing required 'name' field"):
            validate_macro_call_shape({"args": {}})

    def test_name_not_a_string_raises(self) -> None:
        # Line 26 condition: not isinstance(action["name"], str)
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_macro_call_shape({"name": 42})

    def test_name_empty_string_raises(self) -> None:
        # Line 26 condition: not action["name"] (empty string) — covers line 27
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_macro_call_shape({"name": ""})

    def test_args_not_dict_raises(self) -> None:
        # Line 28-31: args present but not a dict — covers line 31
        with pytest.raises(ValueError, match="'args' must be a dict when provided"):
            validate_macro_call_shape({"name": "ok", "args": ["a", "b"]})

    def test_args_as_string_raises(self) -> None:
        with pytest.raises(ValueError, match="'args' must be a dict when provided"):
            validate_macro_call_shape({"name": "ok", "args": "not-a-dict"})

    def test_args_absent_returns_empty_dict(self) -> None:
        _, args = validate_macro_call_shape({"name": "ok"})
        assert args == {}


# ---------------------------------------------------------------------------
# dispatch_macro_call — recursion and depth guards
# ---------------------------------------------------------------------------


class _MinimalSession:
    """Minimal fake BrowserSession for dispatch_macro_call tests."""

    async def navigate(self, url: str) -> dict[str, Any]:
        return {"url": url, "title": ""}

    async def diagnostic_bundle(self) -> dict[str, Any]:
        return {}


@pytest.mark.anyio
async def test_dispatch_macro_call_recursion_detected() -> None:
    session = _MinimalSession()

    def _load_macro(name: str) -> dict[str, Any]:
        return {"actions": []}

    def _substitute(actions: list[Any], args: dict[str, Any]) -> list[Any]:
        return actions

    async def _dispatch_one(
        sess: Any,
        action: Any,
        *,
        invocation_stack: list[str],
        max_depth: int,
    ) -> tuple[int, int]:
        return 1, 0

    with pytest.raises(RuntimeError, match="recursion detected"):
        await dispatch_macro_call(
            session,  # type: ignore[arg-type]
            {"name": "loop"},
            invocation_stack=["loop"],  # already in stack → recursion
            max_depth=MAX_MACRO_CALL_DEPTH,
            load_macro=_load_macro,
            substitute=_substitute,
            dispatch_one=_dispatch_one,
        )


@pytest.mark.anyio
async def test_dispatch_macro_call_depth_exceeded() -> None:
    session = _MinimalSession()

    def _load_macro(name: str) -> dict[str, Any]:
        return {"actions": []}

    def _substitute(actions: list[Any], args: dict[str, Any]) -> list[Any]:
        return actions

    async def _dispatch_one(
        sess: Any,
        action: Any,
        *,
        invocation_stack: list[str],
        max_depth: int,
    ) -> tuple[int, int]:
        return 1, 0

    # Stack already at max_depth=2, so adding "new-macro" would exceed it.
    with pytest.raises(RuntimeError, match="recursion depth exceeded"):
        await dispatch_macro_call(
            session,  # type: ignore[arg-type]
            {"name": "new-macro"},
            invocation_stack=["root", "child"],  # len == max_depth → exceeded
            max_depth=2,
            load_macro=_load_macro,
            substitute=_substitute,
            dispatch_one=_dispatch_one,
        )


@pytest.mark.anyio
async def test_dispatch_macro_call_runs_sub_actions() -> None:
    session = _MinimalSession()
    executed_actions: list[dict[str, Any]] = []

    def _load_macro(name: str) -> dict[str, Any]:
        return {"actions": [{"action": "navigate", "url": "https://octowright.com"}]}

    def _substitute(actions: list[Any], args: dict[str, Any]) -> list[Any]:
        return actions

    async def _dispatch_one(
        sess: Any,
        action: Any,
        *,
        invocation_stack: list[str],
        max_depth: int,
    ) -> tuple[int, int]:
        executed_actions.append(action)
        return 1, 0

    executed, skipped = await dispatch_macro_call(
        session,  # type: ignore[arg-type]
        {"name": "inner-macro"},
        invocation_stack=["outer-macro"],
        max_depth=MAX_MACRO_CALL_DEPTH,
        load_macro=_load_macro,
        substitute=_substitute,
        dispatch_one=_dispatch_one,
    )

    assert executed == 2  # 1 for the macro_call wrapper + 1 child action
    assert skipped == 0
    assert len(executed_actions) == 1
    assert executed_actions[0]["action"] == "navigate"


@pytest.mark.anyio
async def test_dispatch_macro_call_uses_default_max_depth_when_none() -> None:
    """max_depth=None should fall back to MAX_MACRO_CALL_DEPTH (not raise)."""
    session = _MinimalSession()

    def _load_macro(name: str) -> dict[str, Any]:
        return {"actions": []}

    def _substitute(actions: list[Any], args: dict[str, Any]) -> list[Any]:
        return actions

    async def _dispatch_one(
        sess: Any,
        action: Any,
        *,
        invocation_stack: list[str],
        max_depth: int,
    ) -> tuple[int, int]:
        return 1, 0

    executed, skipped = await dispatch_macro_call(
        session,  # type: ignore[arg-type]
        {"name": "some-macro"},
        invocation_stack=["parent"],
        max_depth=None,  # should resolve to MAX_MACRO_CALL_DEPTH
        load_macro=_load_macro,
        substitute=_substitute,
        dispatch_one=_dispatch_one,
    )

    assert executed == 1  # wrapper only, no child actions
    assert skipped == 0
