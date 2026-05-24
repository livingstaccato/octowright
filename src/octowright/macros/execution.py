# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

import octowright.conditional as conditional
from octowright._tracing import counter, histogram, span
from octowright.browser_pool.visuals import _describe_action
from octowright.defaults import MACRO_SLOWMO_MS, METRICS_MACRO_LABEL_CAP
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
from octowright.mcp_types import MacroRepairPreviewResult, MacroRunResult, MacroSequenceResult, MacroSequenceStep

if TYPE_CHECKING:
    from octowright.session import BrowserSession

log = get_logger(__name__)

_MACRO_RUN = counter(
    "octowright_macro_run_total",
    description="Macro runs, labelled by macro name and ok/failed status",
)
_MACRO_RUN_DURATION = histogram(
    "octowright_macro_run_duration_seconds",
    description="run_macro elapsed time including all nested calls",
    unit="s",
)

# Bounded set of distinct macro names that have been admitted as label values
# on the per-macro metrics above. Once the size hits METRICS_MACRO_LABEL_CAP,
# any further unseen name collapses to ``"(overflow)"`` so the time-series
# count for these metrics stays bounded in long-lived deployments.
_MACRO_LABEL_SEEN: set[str] = set()
_MACRO_LABEL_OVERFLOW = "(overflow)"
# Running count of macro-name lookups that collapsed to the overflow bucket
# because the cap was already saturated. Surfaces in ``octowright_status``
# so an operator can see when dynamic macro names are filling the cap with
# junk and call :func:`reset_macro_label_seen` (or restart the daemon) to
# recover real per-macro labels.
_MACRO_LABEL_OVERFLOW_COUNT = 0


def _macro_label(name: str) -> str:
    """Return a bounded-cardinality label value for ``name``.

    Names already in the seen-set pass through verbatim (no eviction — order
    of arrival is the only signal we have, but evicting an already-admitted
    name would let its label start aliasing other names, which is worse than
    a stable-but-fixed roster). New names are admitted up to
    :data:`METRICS_MACRO_LABEL_CAP`; beyond that, all new names collapse to
    a single ``"(overflow)"`` bucket and increment
    ``_MACRO_LABEL_OVERFLOW_COUNT`` for operator visibility via
    ``octowright_status``.

    If dynamic macro names have saturated the cap and operator-process
    access is available, call :func:`reset_macro_label_seen` to clear the
    set (intentionally not surfaced as a remote MCP tool — keeps the API
    surface lean and reset is rare enough to warrant in-process action).
    """
    global _MACRO_LABEL_OVERFLOW_COUNT
    if name in _MACRO_LABEL_SEEN:
        return name
    if len(_MACRO_LABEL_SEEN) < METRICS_MACRO_LABEL_CAP:
        _MACRO_LABEL_SEEN.add(name)
        return name
    _MACRO_LABEL_OVERFLOW_COUNT += 1
    return _MACRO_LABEL_OVERFLOW


def reset_macro_label_seen() -> int:
    """Clear the per-macro label seen-set and return its prior size.

    Operator escape hatch for the situation where dynamic macro names (e.g.
    ``migrate-table-{uuid}``) have permanently filled the
    :data:`METRICS_MACRO_LABEL_CAP`-slot cap, forcing every real macro
    metric into the ``(overflow)`` bucket. The only alternative used to be
    a daemon restart.

    Intentionally NOT exposed as a remote MCP tool — keeps the agent-facing
    API surface lean. This helper is here for tests and for an operator
    with process access (e.g. an interactive debugger or a small
    in-process patch). The current state is visible via the
    ``metrics`` block of ``octowright_status``.

    Returns the number of label-set entries that were cleared (0 when
    already empty). Also resets the overflow-count surface so it tracks
    overflow events since the last reset rather than cumulative-forever.
    """
    global _MACRO_LABEL_OVERFLOW_COUNT
    prior = len(_MACRO_LABEL_SEEN)
    _MACRO_LABEL_SEEN.clear()
    _MACRO_LABEL_OVERFLOW_COUNT = 0
    return prior


_STATUS_PUSH_JS = "(p) => { if (window.__octowright_macro_status) window.__octowright_macro_status(p); }"


async def _push_status(
    session: BrowserSession,
    *,
    text: str | None = None,
    visible: bool = True,
    start: bool = False,
    done: bool = False,
) -> None:
    """Best-effort push of a status string to the in-page macro pill.

    Failures are swallowed: the pill is purely informational and must never
    interrupt or fail a macro. Common failure modes (page navigating, page
    closed, status JS not yet injected) are all benign.

    `done=True` freezes the elapsed counter and disables the pill's
    auto-hide so the final state stays on screen.
    """
    page = getattr(session, "page", None)
    if page is None:
        return
    payload: dict[str, Any] = {"visible": visible}
    if text is not None:
        payload["text"] = text
    if start:
        payload["start"] = True
    if done:
        payload["done"] = True
    try:
        await page.evaluate(_STATUS_PUSH_JS, payload)
    except Exception:
        pass


def _resolve_slowmo_ms(slowmo_ms: int | None) -> int:
    if slowmo_ms is not None:
        return max(0, int(slowmo_ms))
    return max(0, int(MACRO_SLOWMO_MS))


def _format_status(invocation_stack: list[str] | None, action: dict[str, Any]) -> str:
    chain = " > ".join(invocation_stack) if invocation_stack else ""
    desc = _describe_action(action)
    return f"{chain} | {desc}" if chain else desc


async def _dispatch_one(
    session: BrowserSession,
    action: dict[str, Any],
    *,
    invocation_stack: list[str] | None = None,
    max_depth: int | None = None,
    slowmo_ms: int = 0,
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
            dispatch_one=lambda *a, **kw: _dispatch_one(*a, slowmo_ms=slowmo_ms, **kw),
        )

    if invocation_stack is None:
        invocation_stack = []

    # Push status before dispatch so the pill reflects the action that's
    # actually running. macro_call is handled above (its child actions push
    # their own deeper status when they hit _dispatch_one).
    await _push_status(session, text=_format_status(invocation_stack, action))

    # Slowmo runs AFTER the status push so the pill reflects the upcoming
    # action while the user gets time to read it before we actually dispatch.
    if slowmo_ms > 0:
        await asyncio.sleep(slowmo_ms / 1000)

    if action.get("action") in conditional.CONDITIONAL_ACTIONS:

        async def _recurse(recurse_session: Any, recurse_action: dict[str, Any]) -> tuple[int, int]:
            return await _dispatch_one(
                recurse_session,
                recurse_action,
                invocation_stack=invocation_stack,
                max_depth=resolved_max_depth,
                slowmo_ms=slowmo_ms,
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


def repair_preview(name: str) -> MacroRepairPreviewResult:
    return repair_preview_impl(name, load_macro=load_macro, semantic_keys=SEMANTIC_LOCATOR_KEYS)


async def run_macro(
    session: BrowserSession,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    slowmo_ms: int | None = None,
) -> MacroRunResult:
    with span(
        "octowright.macro.run",
        macro=name,
        instance_id=getattr(session, "instance_id", None),
        kind=getattr(session, "kind", None),
    ):
        return await _run_macro_impl(session, name, args, slowmo_ms=slowmo_ms)


async def _run_macro_impl(
    session: BrowserSession,
    name: str,
    args: dict[str, Any] | None,
    *,
    slowmo_ms: int | None,
) -> MacroRunResult:
    macro = load_macro(name)
    effective_args = args or {}
    actions = substitute(macro.get("actions", []), effective_args)

    executed = 0
    skipped = 0
    invocation_stack = [name]
    resolved_slowmo = _resolve_slowmo_ms(slowmo_ms)

    # Reset the pill's elapsed timer so the user sees this macro's runtime
    # rather than a clock continued from a previous run.
    await _push_status(session, text=f"{name} | starting", start=True)

    macro_started = time.monotonic()
    completed_ok = False
    try:
        for index, action in enumerate(actions):
            try:
                executed_count, skipped_count = await _dispatch_one(
                    session,
                    action,
                    invocation_stack=invocation_stack,
                    slowmo_ms=resolved_slowmo,
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
        completed_ok = True
    finally:
        elapsed_s = time.monotonic() - macro_started
        # Pill stays open showing the final state — `done` freezes the elapsed
        # counter and suspends auto-hide so the user can read it. The next
        # macro's `start` push (or an explicit visible:false) clears it.
        outcome_label = "done" if completed_ok else "failed"
        await _push_status(session, text=f"{name} | {outcome_label}", done=True)
        # Metrics + structured log must fire on BOTH the ok and failed paths.
        # Previously these sat outside the try/finally, so a raised
        # RuntimeError skipped them entirely — the "failed" datapoint never
        # landed, the histogram only ever measured successful runs, and the
        # operator-visible log line vanished on the unhappy path.
        macro_label = _macro_label(name)
        _MACRO_RUN.add(
            1,
            attributes={"macro": macro_label, "status": "ok" if completed_ok else "failed"},
        )
        _MACRO_RUN_DURATION.record(elapsed_s, attributes={"macro": macro_label})
        log.info(
            "octowright.macro.run",
            name=name,
            executed=executed,
            skipped=skipped,
            slowmo_ms=resolved_slowmo,
            status="ok" if completed_ok else "failed",
            elapsed_s=round(elapsed_s, 3),
        )

    return {
        "macro": name,
        "executed": executed,
        "skipped": skipped,
        "args_used": effective_args,
        "slowmo_ms": resolved_slowmo,
        "elapsed_s": round(elapsed_s, 3),
    }


async def run_sequence(
    *,
    session: Any,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
    slowmo_ms: int | None = None,
) -> MacroSequenceResult:
    # Wrap the whole sequence in a single parent span so the per-macro
    # ``octowright.macro.run`` spans nest underneath it in the trace tree
    # (OTel context propagation handles the nesting automatically). Without
    # this, N successive run_macro calls produced N sibling top-level spans
    # with no aggregate to anchor sequence-level latency / status views.
    with span(
        "octowright.macro.run_sequence",
        names_count=len(names),
        stop_on_failure=stop_on_failure,
    ):
        resolved_args: list[dict[str, Any]] = []
        for index in range(len(names)):
            if args_list is not None and index < len(args_list):
                resolved_args.append(args_list[index] or {})
            else:
                resolved_args.append({})

        steps: list[MacroSequenceStep] = []
        all_ok = True
        for name, step_args in zip(names, resolved_args, strict=True):
            try:
                outcome = await run_macro(session=session, name=name, args=step_args, slowmo_ms=slowmo_ms)
                steps.append({**outcome, "ok": True})
            except Exception as exc:
                all_ok = False
                steps.append({"macro": name, "ok": False, "error": str(exc), "args_used": step_args})
                if stop_on_failure:
                    raise

        return {"sequence": names, "steps": steps, "ok": all_ok}
