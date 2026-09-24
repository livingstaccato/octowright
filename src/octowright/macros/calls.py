# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Nested macro-call execution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.macros.runtime import dispatch_simple as runtime_dispatch_simple
from octowright.macros.substitution import own_site_hosts

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

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


def actions_assert_network_clean(actions: Any, load_macro: Any, substitute: Any) -> bool:
    """Whether running *actions* can reach an ``expect_network_clean`` step.

    Walks every nested value, so an assertion inside ``try``/``if_selector`` or
    any other container counts, and follows ``macro_call`` into the called
    macro with its call args substituted -- the same expansion dispatch does.
    Each macro is visited once, so recursion terminates. A called macro that
    cannot be loaded contributes nothing: dispatching it fails anyway.
    """
    seen: set[str] = set()
    stack: list[Any] = [actions]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        kind = node.get("action")
        if kind == "expect_network_clean":
            return True
        name = node.get("name")
        if kind == "macro_call" and isinstance(name, str) and name not in seen:
            seen.add(name)
            call_args = node.get("args")
            try:
                called = load_macro(name)
                stack.append(substitute(called.get("actions", []), call_args if isinstance(call_args, dict) else {}))
            except Exception as exc:
                log.debug("octowright.macro.network_clean_scan_unloadable", macro=name, error=repr(exc))
        stack.extend(node.values())
    return False


async def dispatch_macro_call(
    session: SessionLike,
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
    called_actions = substitute(called.get("actions", []), call_args, trusted_hosts=own_site_hosts(session))

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
    session: SessionLike,
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
