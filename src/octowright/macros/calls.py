# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Nested macro-call execution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from octowright.credential_sinks import CREDENTIAL_CALL_MARKER
from octowright.macros.nesting import MacroLoader, iter_nested_actions
from octowright.macros.runtime import dispatch_simple as runtime_dispatch_simple
from octowright.macros.substitution import own_site_origins

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

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


def actions_assert_network_clean(actions: Any, load_macro: MacroLoader | None = None) -> bool:
    """Whether running *actions* can reach an ``expect_network_clean`` step.

    Any depth counts, so an assertion inside ``try``/``if_selector`` or any
    other container does, and with *load_macro* a ``macro_call`` is followed
    into the called macro (`nesting.iter_nested_actions`).
    """
    return any(
        action.get("action") == "expect_network_clean" for action in iter_nested_actions(actions, load_macro=load_macro)
    )


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
    # Taint follows the value: an arg the caller's credential was substituted
    # into stays credential-tier in the callee, whatever the callee calls it.
    marked = action.get(CREDENTIAL_CALL_MARKER)
    tainted = frozenset(str(name) for name in marked) if isinstance(marked, list) else frozenset()
    called_actions = substitute(
        called.get("actions", []), call_args, trusted_origins=own_site_origins(session), credential_args=tainted
    )

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
