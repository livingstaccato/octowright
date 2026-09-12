# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

import octowright.conditional as conditional
from octowright._tracing import counter, histogram, span
from octowright.defaults import MACRO_SLOWMO_MS, METRICS_MACRO_LABEL_CAP
from octowright.macros._redact import _REDACTED_MACRO_VALUE, _redact_action
from octowright.macros.calls import MAX_MACRO_CALL_DEPTH, dispatch_macro_call, dispatch_plain_action
from octowright.macros.descriptions import describe_action
from octowright.macros.privacy import (
    install_sensitive_recorder,
)
from octowright.macros.privacy import (
    redact_args as _privacy_redact_args,
)
from octowright.macros.privacy import (
    scrub_sensitive_values as _privacy_scrub_sensitive_values,
)
from octowright.macros.privacy import (
    sensitive_arg_values as _sensitive_arg_values,
)
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
from octowright.session.timeouts import bounded

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

# _redact_action / _REDACTED_MACRO_VALUE now live in octowright.macros._redact
# (imported above) so repair.py can redact the same way without a circular
# import back into this module.


def _scrub_sensitive_values(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    return _privacy_scrub_sensitive_values(value, sensitive_values, marker=_REDACTED_MACRO_VALUE)


def _redact_args_for_response(args: dict[str, Any]) -> dict[str, Any]:
    return _privacy_redact_args(args, marker=_REDACTED_MACRO_VALUE)


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

# Console messages attached to a macro failure payload. Errors are claimed
# first (see ``_select_console_tail``), so this bounds payload size rather
# than being a window a chatty page can flush the useful line out of.
MACRO_FAILURE_CONSOLE_TAIL = 10
# Per-message cap: the count above bounds the number of messages, not their
# SIZE, and one console.log of a stringified response would otherwise push
# megabytes over the MCP transport. Generous next to capture_summaries' 88-char
# digest cap because this text is read as the cause, not skimmed as a summary.
MACRO_FAILURE_CONSOLE_TEXT_CHARS = 2000
# Failed / non-2xx requests attached to a macro failure payload. A timeout is
# almost never the bug -- it is the symptom of something the page reported and
# the macro could not see. In the case this was built for, the page logged a
# 409 two seconds into a 45s wait and the macro then sat polling for a row the
# server had already refused to create; both facts were in-process at the
# moment of failure and neither reached the error. Bounded like the console
# tail so a long-running step cannot produce an unreadable payload.
MACRO_FAILURE_NETWORK_TAIL = 10
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
    metric into the ``(overflow)`` bucket. The only other fix is a daemon
    restart.

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
    payload: dict[str, Any] = {"visible": visible}
    if text is not None:
        payload["text"] = text
    if start:
        payload["start"] = True
    if done:
        payload["done"] = True
    async with session.operation("macro_status"):
        page = session.page
        if page is None:
            return
        try:
            await bounded(page.evaluate(_STATUS_PUSH_JS, payload), operation="macro_status")
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


async def _dispatch_classified_screenshot(
    session: SessionLike,
    action: dict[str, Any],
    sensitive_values: tuple[str, ...],
) -> tuple[int, int]:
    """Route a screenshot taken under classified args through the privacy handler.

    A screenshot of a page a credential was typed into is a durable copy of
    that credential, so the generic capture path is refused outright: a
    composition root must install both the authority flag and a handler that
    knows what evidence is safe to keep.
    """
    authorized = getattr(session, "_octowright_sensitive_screenshot_authority", None) is True
    handler = getattr(session, "_octowright_sensitive_screenshot_handler", None)
    if not authorized or not callable(handler):
        raise RuntimeError("classified macro screenshot requires an explicit privacy handler")
    handled = await handler(action=action, sensitive_values=sensitive_values)
    if handled is None:
        raise RuntimeError("classified macro screenshot privacy handler refused the action")
    return handled


async def _dispatch_one(
    session: SessionLike,
    action: dict[str, Any],
    *,
    invocation_stack: list[str] | None = None,
    max_depth: int | None = None,
    slowmo_ms: int = 0,
    sensitive_values: tuple[str, ...] = (),
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
            dispatch_one=lambda *a, **kw: _dispatch_one(
                *a,
                slowmo_ms=slowmo_ms,
                sensitive_values=sensitive_values,
                **kw,
            ),
        )

    # Push status before dispatch so the pill reflects the action that's
    # actually running. macro_call is handled above (its child actions push
    # their own deeper status when they hit _dispatch_one).
    await _push_status(session, text=_format_status(invocation_stack, action))

    # Slowmo runs AFTER the status push so the pill reflects the upcoming
    # action while the user gets time to read it before we actually dispatch.
    if slowmo_ms > 0:
        await asyncio.sleep(slowmo_ms / 1000)

    # A composition root may install a synchronous, process-local authority
    # check for browser actions. It runs after every awaited status/slowmo step
    # and immediately before conditional/plain dispatch, so no scheduler turn
    # can separate the check from the browser operation.
    boundary = getattr(session, "_octowright_before_macro_action", None)
    if boundary is not None:
        boundary(
            action=action,
            invocation_stack=tuple(invocation_stack or ()),
        )

    if action.get("action") == "screenshot" and sensitive_values:
        return await _dispatch_classified_screenshot(session, action, sensitive_values)

    if action.get("action") in conditional.CONDITIONAL_ACTIONS:

        async def _recurse(recurse_session: SessionLike, recurse_action: dict[str, Any]) -> tuple[int, int]:
            return await _dispatch_one(
                recurse_session,
                recurse_action,
                invocation_stack=invocation_stack,
                max_depth=resolved_max_depth,
                slowmo_ms=slowmo_ms,
                sensitive_values=sensitive_values,
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


def _truncate_console_message(message: Any) -> Any:
    """Return ``message`` with an over-long ``text`` capped, never mutated."""
    if not isinstance(message, dict):
        return message
    text = message.get("text")
    if not isinstance(text, str) or len(text) <= MACRO_FAILURE_CONSOLE_TEXT_CHARS:
        return message
    return {**message, "text": text[:MACRO_FAILURE_CONSOLE_TEXT_CHARS] + "…[truncated]"}


def _failed_requests_tail(session: SessionLike) -> list[dict[str, Any]]:
    """The newest failed / non-2xx requests, for a failure payload.

    Reads the session's own bounded deque rather than taking a window from the
    failing step: the deque has no per-step boundary, and a request the page
    issued moments before the step began is exactly as likely to be the cause.
    Newest-first bounding is what keeps it relevant.

    Best-effort by construction -- a session that cannot answer must not turn
    a macro failure into a different, more confusing failure, so anything
    raised here yields no network block rather than replacing the real error.
    """
    try:
        rows = session.get_network_requests(limit=None)["requests"]
    except Exception:
        return []
    failed = [row for row in rows if row.get("failure") or (row.get("status") or 0) >= 400]
    return failed[-MACRO_FAILURE_NETWORK_TAIL:]


def _truncate_bundle_console(bundle: dict[str, Any]) -> dict[str, Any]:
    """Cap each console message's text so a chatty page can't bloat the error.

    Replaces the list rather than editing the messages, so this holds no
    opinion about whether the producer handed back copies or the session's
    live ring-buffer entries. It did copy them -- but an invariant maintained
    across two modules by a comment is how the buffer got rewritten the first
    time, and only this function needed to know.
    """
    messages = bundle.get("console_tail")
    if not isinstance(messages, list):
        return bundle
    bundle["console_tail"] = [_truncate_console_message(message) for message in messages]
    return bundle


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


async def _build_failure_payload(
    session: SessionLike,
    *,
    name: str,
    index: int,
    action: dict[str, Any],
    actions: list[dict[str, Any]],
    executed: int,
    safe_original: str,
    sensitive_values: tuple[str, ...],
) -> dict[str, Any]:
    """Assemble the failure payload from three independently-fallible producers.

    Each producer is tried separately so one failing does not cost the caller
    the other two: its own error is recorded IN the payload rather than raised
    over the dispatch failure the payload exists to explain.
    """
    if sensitive_values:
        # The generic diagnostic producer persists raw HTML and a raw
        # screenshot. Classified macros may have rendered an argument into
        # either, so do not invoke it. Composition roots can retain their own
        # explicitly safe evidence at the authorized screenshot boundary.
        bundle: dict[str, Any] = {"diagnostic_suppressed": "classified macro arguments"}
    else:
        try:
            bundle = _truncate_bundle_console(await session.diagnostic_bundle(console_tail=MACRO_FAILURE_CONSOLE_TAIL))
        except Exception as secondary:
            bundle = {"diagnostic_error": repr(secondary)}

    redacted_action = _scrub_sensitive_values(_redact_action(action), sensitive_values)
    try:
        fix_suggestion = _scrub_sensitive_values(await _suggest_fix(session, redacted_action), sensitive_values)
    except Exception as secondary:
        fix_suggestion = None
        bundle["healing_error"] = _scrub_sensitive_values(repr(secondary), sensitive_values)
    try:
        failed_requests = _scrub_sensitive_values(_failed_requests_tail(session), sensitive_values)
    except Exception as secondary:  # defensive around injected session implementations
        failed_requests = []
        bundle["network_error"] = _scrub_sensitive_values(repr(secondary), sensitive_values)

    payload: dict[str, Any] = {
        "macro": name,
        "failed_at_step": index,
        # Partial-state signal: a multi-step macro that fails midway has
        # already applied steps 0..index-1 to the live browser. Surface both
        # the count and the (credential-redacted) descriptors of what landed so
        # the agent can reason about the half-applied state instead of seeing
        # an opaque error.
        "executed": executed,
        "executed_actions": [
            _scrub_sensitive_values(_redact_action(done), sensitive_values) for done in actions[:index]
        ],
        "failed_action": redacted_action,
        "original": safe_original,
        "bundle": bundle,
        # The console tail and final URL were already in `bundle`; the failing
        # requests were not, so a payload could report "timed out waiting for
        # #foo" while the 409 that explains it sat unread. Carries the response
        # body for a failed same-origin request (see
        # session/core_network_mixin), which is usually the whole diagnosis --
        # a status code alone is not actionable.
        #
        # A sibling of `bundle` rather than a key inside it: `bundle` is what
        # diagnostic_bundle() returned, and folding another producer's data into
        # it makes that claim false for every reader (a whole-record assertion
        # caught exactly this).
        "failed_requests": failed_requests,
    }
    if fix_suggestion:
        payload["healing_suggestion"] = fix_suggestion
    return payload


async def _finish_macro_run(
    session: SessionLike,
    *,
    name: str,
    completed_ok: bool,
    macro_started: float,
    executed: int,
    skipped: int,
    resolved_slowmo: int,
) -> float:
    """Close out a run: final pill push, metrics, structured log. Returns elapsed.

    Runs from the caller's ``finally`` so both the ok and failed paths reach it
    -- outside it, a raised RuntimeError skips the lot: the "failed" datapoint
    never lands, the histogram only ever measures successful runs, and the
    operator-visible log line vanishes on the unhappy path.
    """
    elapsed_s = time.monotonic() - macro_started
    status = "ok" if completed_ok else "failed"
    # Pill stays open showing the final state -- `done` freezes the elapsed
    # counter and suspends auto-hide so the user can read it. The next macro's
    # `start` push (or an explicit visible:false) clears it.
    await _push_status(session, text=f"{name} | {'done' if completed_ok else 'failed'}", done=True)
    macro_label = _macro_label(name)
    _MACRO_RUN.add(1, attributes={"macro": macro_label, "status": status})
    _MACRO_RUN_DURATION.record(elapsed_s, attributes={"macro": macro_label})
    log.info(
        "octowright.macro.run",
        name=name,
        instance_id=session.instance_id,
        executed=executed,
        skipped=skipped,
        slowmo_ms=resolved_slowmo,
        status=status,
        elapsed_s=round(elapsed_s, 3),
    )
    return elapsed_s


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
    sensitive_values = _sensitive_arg_values(effective_args)
    install_sensitive_recorder(session, sensitive_values)
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
            failure: RuntimeError | None = None
            failure_cause: Exception | None = None
            safe_original: str | None = None
            try:
                executed_count, skipped_count = await _dispatch_one(
                    session,
                    action,
                    invocation_stack=invocation_stack,
                    slowmo_ms=resolved_slowmo,
                    sensitive_values=sensitive_values,
                )
            except Exception as exc:
                safe_original = str(_scrub_sensitive_values(repr(exc), sensitive_values))
                if not sensitive_values:
                    failure_cause = exc
            if safe_original is not None:
                # Leave the raw dispatch handler before asking any diagnostic
                # producer to run. If one of those producers fails, its error
                # is represented in the payload; it never escapes while the
                # credential-bearing dispatch exception is active context.
                payload = await _build_failure_payload(
                    session,
                    name=name,
                    index=index,
                    action=action,
                    actions=actions,
                    executed=executed,
                    safe_original=safe_original,
                    sensitive_values=sensitive_values,
                )
                failure = RuntimeError(payload)
            # Raise after leaving the handler so the raw caught exception is
            # not retained as ``__context__`` on the caller-visible failure.
            if failure is not None:
                if failure_cause is not None:
                    raise failure from failure_cause
                raise failure from None
            executed += executed_count
            skipped += skipped_count
            # Emit progress after each landed step (count up to the total). Drives
            # the follower bridge's deadline re-arm and any client progress bar.
            await _report_progress(ctx, index + 1, len(actions), action.get("action"))
        completed_ok = True
    finally:
        elapsed_s = await _finish_macro_run(
            session,
            name=name,
            completed_ok=completed_ok,
            macro_started=macro_started,
            executed=executed,
            skipped=skipped,
            resolved_slowmo=resolved_slowmo,
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
