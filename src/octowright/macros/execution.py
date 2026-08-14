# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

import octowright.conditional as conditional
from octowright._tracing import counter, histogram, span
from octowright.defaults import MACRO_SLOWMO_MS, METRICS_MACRO_LABEL_CAP
from octowright.macros.calls import MAX_MACRO_CALL_DEPTH, dispatch_macro_call, dispatch_plain_action
from octowright.macros.descriptions import describe_action
from octowright.macros.repair import repair_apply as repair_apply_impl
from octowright.macros.repair import repair_preview as repair_preview_impl
from octowright.macros.repair import suggest_fix as _suggest_fix
from octowright.macros.runtime import dispatch_simple as runtime_dispatch_simple
from octowright.macros.storage import load_macro, write_macro
from octowright.macros.substitution import (
    SEMANTIC_LOCATOR_KEYS,
    action_kwargs,
    strip_non_aria_noise,
    substitute,
)
from octowright.mcp_types import (
    MacroRepairApplyResult,
    MacroRepairPreviewResult,
    MacroRunResult,
    MacroSequenceResult,
    MacroSequenceStep,
)

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

# Action kinds whose ``value`` field carries user-supplied data that often
# resolves to a credential (``{{password}}``-style placeholders are resolved
# in-place by ``substitute()`` before dispatch). Redacted before the action
# dict is embedded in the RuntimeError payload, sent to the macro-pill, or
# emitted in any log line.
_REDACTED_MACRO_VALUE = "<redacted>"
_REDACT_VALUE_ACTIONS: frozenset[str] = frozenset({"fill", "type", "fill_by"})


def _redact_action(action: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of ``action`` with credential-bearing fields
    replaced by ``<redacted>``. Non-redacted actions return a copy unchanged
    so callers can mutate freely without aliasing back into the macro list."""
    redacted = dict(action)
    if redacted.get("action") in _REDACT_VALUE_ACTIONS:
        for key in ("value", "text"):
            if key in redacted:
                redacted[key] = _REDACTED_MACRO_VALUE
    return redacted


_SENSITIVE_ARG_KEY_PARTS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "credential",
)
# ``email`` and ``username`` are exact-match only: the lint module already
# flags raw email-shaped values as PII; redacting them in the response keeps
# the args_used echo from leaking the user's identity even when the value
# was supplied via plain ``{{email}}`` template substitution.
_SENSITIVE_ARG_EXACT_KEYS = frozenset({"pw", "pwd", "auth", "email", "username"})


def _redact_args_for_response(args: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in args.items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in _SENSITIVE_ARG_EXACT_KEYS or any(part in normalized for part in _SENSITIVE_ARG_KEY_PARTS):
            redacted[str(key)] = _REDACTED_MACRO_VALUE
        else:
            redacted[str(key)] = value
    return redacted


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
    session: SessionLike,
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
    page = session.page
    if page is None:
        return
    payload: dict[str, Any] = {"visible": visible}
    if text is not None:
        payload["text"] = text
    if start:
        payload["start"] = True
    if done:
        payload["done"] = True
    async with session.operation("macro_status"):
        try:
            await page.evaluate(_STATUS_PUSH_JS, payload)
        except Exception as exc:
            # Per silent-swallow policy: this is a user-action path, so log instead
            # of truly swallowing. A failed pill push must not break macro dispatch.
            log.debug("octowright.macro.pill_push_failed", error=repr(exc))


def _resolve_slowmo_ms(slowmo_ms: int | None) -> int:
    if slowmo_ms is not None:
        return max(0, int(slowmo_ms))
    return max(0, int(MACRO_SLOWMO_MS))


def _format_status(invocation_stack: list[str] | None, action: dict[str, Any]) -> str:
    chain = " > ".join(invocation_stack) if invocation_stack else ""
    # The pill text is visible to the user and may end up in screenshots /
    # traces; redact credential fields before handing them to the describer.
    desc = describe_action(_redact_action(action))
    return f"{chain} | {desc}" if chain else desc


async def _dispatch_one(
    session: SessionLike,
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

        async def _recurse(recurse_session: SessionLike, recurse_action: dict[str, Any]) -> tuple[int, int]:
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


async def _dispatch_simple(session: SessionLike, action: dict[str, Any]) -> tuple[int, int]:
    return await runtime_dispatch_simple(
        session,
        action,
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
        strip_non_aria_noise=strip_non_aria_noise,
        action_kwargs=action_kwargs,
    )


def repair_preview(name: str) -> MacroRepairPreviewResult:
    return repair_preview_impl(name, load_macro=load_macro, semantic_keys=SEMANTIC_LOCATOR_KEYS)


def repair_apply(name: str, action_index: int) -> MacroRepairApplyResult:
    return repair_apply_impl(
        name,
        action_index,
        load_macro=load_macro,
        write_macro=write_macro,
        semantic_keys=SEMANTIC_LOCATOR_KEYS,
    )


async def _report_progress(ctx: Any | None, progress: float, total: float, message: str | None) -> None:
    """Best-effort MCP progress emission for a long-running macro.

    No-ops when there is no Context (direct, non-MCP callers) and never raises out
    of macro execution — a progress hiccup must not fail the macro. When the
    follower bridge has injected a progressToken, each notification also re-arms
    the in-flight deadline so a steadily-progressing macro isn't killed by the
    flat bridge timeout (see ``proxy_supervisor``).
    """
    if ctx is None:
        return
    with contextlib.suppress(Exception):
        await ctx.report_progress(progress, total=total, message=message)


async def run_macro(
    session: SessionLike,
    name: str,
    args: dict[str, Any] | None = None,
    *,
    slowmo_ms: int | None = None,
    ctx: Any | None = None,
) -> MacroRunResult:
    async with session.operation("macro_run"):
        with span(
            "octowright.macro.run",
            macro=name,
            instance_id=session.instance_id,
            kind=session.kind,
        ):
            return await _run_macro_impl(session, name, args, slowmo_ms=slowmo_ms, ctx=ctx)


async def _run_macro_impl(
    session: SessionLike,
    name: str,
    args: dict[str, Any] | None,
    *,
    slowmo_ms: int | None,
    ctx: Any | None = None,
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
                # The action dict reaches the MCP client AND the structured
                # log line below. ``substitute()`` has already resolved
                # ``{{password}}``-style placeholders into the action, so
                # the raw value field can be a literal credential — strip
                # it before exposing the payload to either sink.
                redacted_action = _redact_action(action)
                payload: dict[str, Any] = {
                    "macro": name,
                    "failed_at_step": index,
                    # Partial-state signal: a multi-step macro that fails midway
                    # has already applied steps 0..index-1 to the live browser.
                    # Surface both the count and the (credential-redacted)
                    # descriptors of what landed so the agent can reason about
                    # the half-applied state instead of seeing an opaque error.
                    "executed": executed,
                    "executed_actions": [_redact_action(done) for done in actions[:index]],
                    "failed_action": redacted_action,
                    "original": repr(exc),
                    "bundle": bundle,
                }
                if fix_suggestion:
                    payload["healing_suggestion"] = fix_suggestion
                raise RuntimeError(payload) from exc
            executed += executed_count
            skipped += skipped_count
            # Emit progress after each landed step (count up to the total). Drives
            # the follower bridge's deadline re-arm and any client progress bar.
            await _report_progress(ctx, index + 1, len(actions), action.get("action"))
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
            instance_id=session.instance_id,
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
        "args_used": _redact_args_for_response(effective_args),
        "slowmo_ms": resolved_slowmo,
        "elapsed_s": round(elapsed_s, 3),
    }


async def run_sequence(
    *,
    session: SessionLike,
    names: list[str],
    args_list: list[dict[str, Any]] | None = None,
    stop_on_failure: bool = True,
    slowmo_ms: int | None = None,
    ctx: Any | None = None,
) -> MacroSequenceResult:
    # The outer lease keeps "macro_run_sequence" as the observable root for
    # every member macro's run_macro re-entry (same task, no re-queueing) --
    # a manual action can't interleave between sequence steps any more than
    # it can between actions inside a single run_macro.
    async with session.operation("macro_run_sequence"):
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
                    outcome = await run_macro(session=session, name=name, args=step_args, slowmo_ms=slowmo_ms, ctx=ctx)
                    steps.append({**outcome, "ok": True})
                except Exception as exc:
                    all_ok = False
                    steps.append(
                        {
                            "macro": name,
                            "ok": False,
                            "error": str(exc),
                            "args_used": _redact_args_for_response(step_args),
                        }
                    )
                    if stop_on_failure:
                        raise

            return {"sequence": names, "steps": steps, "ok": all_ok}
