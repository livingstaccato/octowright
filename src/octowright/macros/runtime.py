# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

log = get_logger(__name__)

_REPLAY_SKIP = {"launch", "close", "snapshot"}
_ACTION_MAP = {
    "navigate": "navigate",
    "click": "click",
    "type": "type_text",
    "fill": "fill",
    "press_key": "press_key",
    "screenshot": "screenshot",
    "evaluate": "evaluate",
    "wait_for": "wait_for",
    "expect_url": "expect_url",
    "expect_text": "expect_text",
    "expect_selector": "expect_selector",
    "expect_js": "expect_js",
    "mock_route": "mock_route",
    "unmock_route": "unmock_route",
    "set_dialog_policy": "set_dialog_policy",
    "set_input_files": "set_input_files",
    "click_by": "click_by",
    "fill_by": "fill_by",
}


async def _dispatch_standard(session: Any, kind: str, kwargs: dict[str, Any], method_name: str) -> tuple[int, int]:
    if kind == "screenshot":
        path_value = kwargs.get("path")
        if not path_value:
            return 0, 1
        kwargs = dict(kwargs)
        kwargs["path"] = Path(path_value)
        await getattr(session, method_name)(kwargs["path"])
        return 1, 0
    if kind == "type" and "delay_ms" not in kwargs:
        kwargs = dict(kwargs)
        kwargs["delay_ms"] = 0
    if kind == "wait_for":
        kwargs = dict(kwargs)
        kwargs.setdefault("selector", None)
        kwargs.setdefault("text", None)
        kwargs.setdefault("timeout_ms", None)
    method = getattr(session, method_name)
    await method(**kwargs)
    return 1, 0


async def _dispatch_click_or_fill(
    session: Any, kind: str, kwargs: dict[str, Any], semantic_keys: tuple[str, ...]
) -> tuple[int, int]:
    is_fill = kind in {"fill", "fill_by"}
    fallback_method: Callable[..., Awaitable[Any]]
    semantic_method: Callable[..., Awaitable[Any]] | None
    if is_fill:
        fallback_method = session.fill
        semantic_method = getattr(session, "fill_by", None)
    else:
        fallback_method = session.click
        semantic_method = getattr(session, "click_by", None)

    semantic_kwargs = {k: v for k, v in kwargs.items() if k in semantic_keys}
    if semantic_method is None:
        semantic_kwargs = {}
    else:
        if "timeout_ms" in kwargs:
            semantic_kwargs["timeout_ms"] = kwargs["timeout_ms"]
        if is_fill and "value" in kwargs:
            semantic_kwargs["value"] = kwargs["value"]

    if bool(semantic_kwargs) and semantic_method is not None:
        try:
            await semantic_method(**semantic_kwargs)
            return 1, 0
        except Exception:
            if "selector" not in kwargs:
                raise

    fallback_kwargs = {k: v for k, v in kwargs.items() if k not in semantic_keys}
    fallback_kwargs.pop("timeout_ms", None)
    await fallback_method(**fallback_kwargs)
    return 1, 0


async def dispatch_simple(
    session: Any,
    action: dict[str, Any],
    *,
    semantic_keys: tuple[str, ...],
    strip_non_aria_noise: Callable[[str, dict[str, Any]], dict[str, Any]],
    action_kwargs: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[int, int]:
    kind = action.get("action", "")
    if kind in _REPLAY_SKIP:
        return 0, 1
    if kind not in _ACTION_MAP:
        # macro_call needs an invocation_stack — it only dispatches through
        # the full _dispatch_one path in macros/execution.py. Reaching the
        # simple dispatcher means a caller routed the wrong way (e.g. via
        # dispatch_plain_action from inside a conditional). Surface that
        # because silently skipping it produces wrong macro behaviour.
        if kind == "macro_call":
            log.warning(
                "octowright.macros.macro_call_in_simple_dispatch",
                hint="macro_call requires the full _dispatch_one with invocation_stack; this caller skipped it",
            )
        return 0, 1

    kwargs = strip_non_aria_noise(kind, action_kwargs(action))
    if kind in {"click", "fill", "click_by", "fill_by"}:
        return await _dispatch_click_or_fill(session, kind, kwargs, semantic_keys)

    method_name = _ACTION_MAP[kind]
    if not hasattr(session, method_name):
        return 0, 1
    return await _dispatch_standard(session, kind, kwargs, method_name)


async def dispatch_one(
    session: Any,
    action: dict[str, Any],
    *,
    semantic_keys: tuple[str, ...],
    strip_non_aria_noise: Callable[[str, dict[str, Any]], dict[str, Any]],
    action_kwargs: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[int, int]:
    import octowright.conditional as _cond

    if action.get("action") in _cond.CONDITIONAL_ACTIONS:

        async def _recurse(s: Any, a: dict[str, Any]) -> tuple[int, int]:
            return await dispatch_one(
                s,
                a,
                semantic_keys=semantic_keys,
                strip_non_aria_noise=strip_non_aria_noise,
                action_kwargs=action_kwargs,
            )

        return await _cond.dispatch_conditional(session, action, _recurse)

    return await dispatch_simple(
        session,
        action,
        semantic_keys=semantic_keys,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )
