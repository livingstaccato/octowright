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

# Passive observer events (page emits these on its own — not user actions).
# We strip them in dispatch_simple so a macro built from a raw recording
# doesn't tally bogus "error" steps for them.
_REPLAY_PASSIVE = {
    "console",
    "popup_opened",
    "websocket_opened",
    "websocket_closed",
    "websocket_inbound",
    "websocket_outbound",
    "markdown_cached",
    "markdown_cache_error",
    "websocket_cache_error",
    "trace_stop_error",
    "dialog_handler_error",
}
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
    # User actions previously recorded but absent from the dispatch map —
    # they were saved into macros and then silently no-op'd as errors.
    "hover": "hover",
    "select_option": "select_option",
    "drag": "drag",
    "navigate_back": "navigate_back",
    "resize": "resize",
    "open_url": "open_url",
    "switch_page": "switch_page",
    "close_page": "close_page",
    "reset_frame": "reset_frame",
}

# Recorded keys that aren't accepted by the session method on replay.
# Some recordings store the resulting URL or other observed state alongside
# the inputs; those need to be dropped before invoking the method.
_REPLAY_DROP_KEYS: dict[str, tuple[str, ...]] = {
    "navigate_back": ("url",),
    "switch_page": ("url",),
    "close_page": ("was_active",),
    # open_url records the resulting page index alongside the inputs; the
    # method signature only accepts (url, target, width, height).
    "open_url": ("page_index",),
}

# Recorded keys that need renaming to match the method's parameter names.
_REPLAY_RENAME_KEYS: dict[str, dict[str, str]] = {
    "drag": {"source": "source_selector", "target": "target_selector"},
}


def _normalize_replay_kwargs(kind: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Apply per-action kwargs normalization that doesn't dispatch the call.

    Adds default values for kinds whose recorder writes less than the method
    signature expects (``type``, ``wait_for``), drops recorded-only metadata
    (``_REPLAY_DROP_KEYS``), and renames recorder field names to method
    parameter names (``_REPLAY_RENAME_KEYS``).
    """
    if kind == "type" and "delay_ms" not in kwargs:
        kwargs = {**kwargs, "delay_ms": 0}
    if kind == "wait_for":
        kwargs = dict(kwargs)
        kwargs.setdefault("selector", None)
        kwargs.setdefault("text", None)
        kwargs.setdefault("timeout_ms", None)
    drop_keys = _REPLAY_DROP_KEYS.get(kind)
    if drop_keys:
        kwargs = {k: v for k, v in kwargs.items() if k not in drop_keys}
    rename_map = _REPLAY_RENAME_KEYS.get(kind)
    if rename_map:
        kwargs = {rename_map.get(k, k): v for k, v in kwargs.items()}
    return kwargs


async def _dispatch_standard(session: Any, kind: str, kwargs: dict[str, Any], method_name: str) -> tuple[int, int]:
    if kind == "screenshot":
        path_value = kwargs.get("path")
        if not path_value:
            return 0, 1
        await getattr(session, method_name)(Path(path_value))
        return 1, 0
    kwargs = _normalize_replay_kwargs(kind, kwargs)
    await getattr(session, method_name)(**kwargs)
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
    if kind in _REPLAY_PASSIVE:
        # Page-emitted observation events — not user actions. Skip without
        # counting an error so a macro built straight from a recording's
        # JSONL doesn't tally bogus failures.
        return 0, 0
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
        else:
            # Any other unknown kind used to silently return (0, 1) — looked
            # identical to an intentional skip. Surface it so missing
            # _ACTION_MAP entries can be diagnosed instead of guessed at.
            log.warning(
                "octowright.macros.unknown_action_kind",
                kind=kind,
                hint="kind is not in _ACTION_MAP, _REPLAY_SKIP, or _REPLAY_PASSIVE",
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
