# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

import octowright.conditional as conditional
from octowright.macros.calls import MAX_MACRO_CALL_DEPTH, dispatch_macro_call, dispatch_plain_action
from octowright.macros.repair import repair_preview as repair_preview_impl
from octowright.macros.repair import suggest_fix as _suggest_fix
from octowright.macros.runtime import dispatch_simple as runtime_dispatch_simple
from octowright.macros.storage import load_macro
from octowright.macros.substitution import (
    SEMANTIC_LOCATOR_KEYS,
    action_kwargs,
    strip_non_aria_noise,
    substitute,
)

if TYPE_CHECKING:
    from octowright.session import BrowserSession

log = get_logger(__name__)


async def _dispatch_one(
    session: BrowserSession,
    action: dict[str, Any],
    *,
    invocation_stack: list[str] | None = None,
    max_depth: int | None = None,
) -> tuple[int, int]:
    resolved_max_depth = max_depth if max_depth is not None else MAX_MACRO_CALL_DEPTH

    if action.get("action") == "macro_call":
        if invocation_stack is None:
            raise RuntimeError("macro_call can only execute in a macro context with an invocation stack")
        return await dispatch_macro_call(
            session,
            action,
            invocation_stack=invocation_stack,
            max_depth=resolved_max_depth,
            load_macro=load_macro,
            substitute=substitute,
            dispatch_one=_dispatch_one,
        )

    if invocation_stack is None:
        invocation_stack = []

    if action.get("action") in conditional.CONDITIONAL_ACTIONS:

        async def _recurse(recurse_session: Any, recurse_action: dict[str, Any]) -> tuple[int, int]:
            return await _dispatch_one(
                recurse_session,
                recurse_action,
                invocation_stack=invocation_stack,
                max_depth=resolved_max_depth,
            )

        return await conditional.dispatch_conditional(session, action, _recurse)

    return await dispatch_plain_action(
        session,
        action,
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )


async def _dispatch_simple(session: BrowserSession, action: dict[str, Any]) -> tuple[int, int]:
    return await runtime_dispatch_simple(
        session,
        action,
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )


def repair_preview(name: str) -> dict[str, Any]:
    return repair_preview_impl(name, load_macro=load_macro, semantic_keys=SEMANTIC_LOCATOR_KEYS)


async def run_macro(
    session: BrowserSession,
    name: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    macro = load_macro(name)
    effective_args = args or {}
    actions = substitute(macro.get("actions", []), effective_args)

    executed = 0
    skipped = 0
    invocation_stack = [name]

    for index, action in enumerate(actions):
        try:
            executed_count, skipped_count = await _dispatch_one(
                session,
                action,
                invocation_stack=invocation_stack,
            )
        except Exception as exc:
            bundle = await session.diagnostic_bundle()
            fix_suggestion = await _suggest_fix(session, action)
            payload: dict[str, Any] = {
                "macro": name,
                "failed_at_step": index,
                "failed_action": action,
                "original": repr(exc),
                "bundle": bundle,
            }
            if fix_suggestion:
                payload["healing_suggestion"] = fix_suggestion
            raise RuntimeError(payload) from exc
        executed += executed_count
        skipped += skipped_count

    log.info("octowright.macro.run", name=name, executed=executed, skipped=skipped)
    return {"macro": name, "executed": executed, "skipped": skipped, "args_used": effective_args}


async def run_sequence(
    *,
    session: Any,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
) -> dict[str, Any]:
    resolved_args: list[dict[str, Any]] = []
    for index in range(len(names)):
        if args_list is not None and index < len(args_list):
            resolved_args.append(args_list[index] or {})
        else:
            resolved_args.append({})

    steps: list[dict[str, Any]] = []
    all_ok = True
    for name, step_args in zip(names, resolved_args, strict=True):
        try:
            outcome = await run_macro(session=session, name=name, args=step_args)
            steps.append({**outcome, "ok": True})
        except Exception as exc:
            all_ok = False
            steps.append({"macro": name, "ok": False, "error": str(exc), "args_used": step_args})
            if stop_on_failure:
                raise

    return {"sequence": names, "steps": steps, "ok": all_ok}
