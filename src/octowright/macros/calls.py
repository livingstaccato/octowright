# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Nested macro-call execution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from octowright.macros.runtime import dispatch_simple as runtime_dispatch_simple

if TYPE_CHECKING:
    from octowright.session import BrowserSession

MAX_MACRO_CALL_DEPTH = 32
_RECURSION_PREFIX = "macro_call"


def format_macro_chain(stack: list[str], next_name: str) -> str:
    return " -> ".join([*stack, next_name])


def validate_macro_call_shape(action: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if "name" not in action:
        raise ValueError(f"{_RECURSION_PREFIX} action missing required 'name' field")
    if not isinstance(action["name"], str) or not action["name"]:
        raise ValueError(f"{_RECURSION_PREFIX} action 'name' must be a non-empty string")
    if "args" in action and not isinstance(action["args"], dict):
        raise ValueError(f"{_RECURSION_PREFIX} action 'args' must be a dict when provided")
    return action["name"], action.get("args", {})


async def dispatch_macro_call(
    session: BrowserSession,
    action: dict[str, Any],
    *,
    invocation_stack: list[str],
    max_depth: int | None,
    load_macro: Any,
    substitute: Any,
    dispatch_one: Any,
) -> tuple[int, int]:
    called_name, call_args = validate_macro_call_shape(action)
    next_chain = format_macro_chain(invocation_stack, called_name)
    resolved_max_depth = max_depth if max_depth is not None else MAX_MACRO_CALL_DEPTH

    if called_name in invocation_stack:
        raise RuntimeError(f"{_RECURSION_PREFIX} recursion detected: {next_chain}")
    if len(invocation_stack) >= resolved_max_depth:
        raise RuntimeError(f"{_RECURSION_PREFIX} recursion depth exceeded ({resolved_max_depth}) at {next_chain}")

    called = load_macro(called_name)
    called_actions = substitute(called.get("actions", []), call_args)

    executed, skipped = 1, 0
    for subaction in called_actions:
        e, s = await dispatch_one(
            session,
            subaction,
            invocation_stack=[*invocation_stack, called_name],
            max_depth=resolved_max_depth,
        )
        executed += e
        skipped += s
    return executed, skipped


async def dispatch_plain_action(
    session: BrowserSession,
    action: dict[str, Any],
    *,
    semantic_keys: tuple[str, ...],
    strip_non_aria_noise: Any,
    action_kwargs: Any,
) -> tuple[int, int]:
    return await runtime_dispatch_simple(
        session,
        action,
        semantic_keys=semantic_keys,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )
