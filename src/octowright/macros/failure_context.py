# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What a macro failure payload carries besides the dispatch error.

Split out of ``execution.py`` (at the repository's LOC ceiling). Two kinds of
field, scrubbed by different rules. Page-derived text -- the exception, the
console, failed requests, page errors, the A11y tree -- is scrubbed of every
value the run and the session ledger hold, wherever it appears
(`failure_scrub_values`). Fields that echo the macro's own definition -- the
failed and executed steps -- show the step as written (`written_actions`),
which is text the client already holds, so they are not ledger-scrubbed.

The network and page-error producers are best-effort: a session that cannot
answer must not turn a macro failure into a different, more confusing
failure, so anything raised there yields an empty block rather than replacing
the real error.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright import conditional
from octowright.artifacts.digest import sanitize_url
from octowright.macros._redact import _REDACTED_MACRO_VALUE, _redact_action
from octowright.macros.privacy import MacroArgPrivacy, scrub_sensitive_values, with_session_values

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

# Console messages attached to a macro failure payload. Half the window is
# kept for the plain tail and the rest goes to the newest diagnostic-level
# messages (see ``_select_console_tail``), so a chatty page cannot flush the
# useful line out of it.
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
# Uncaught page exceptions attached to a macro failure payload. They are not
# console messages, so without this an ``N page error(s)`` failure named nothing.
MACRO_FAILURE_PAGE_ERROR_TAIL = 10


def failure_scrub_values(session: SessionLike, run_values: tuple[str, ...]) -> tuple[str, ...]:
    """What a payload's page-derived text is scrubbed of: the run's values and the session's, each anywhere.

    The session ledger also holds values admitted outside any macro -- a
    password the input classification hid from a direct ``browser_fill`` --
    and the page may have echoed one into the console or a request the payload
    carries. Flattened: the ledger's word bounds are for the recording, and
    the payload is returned to the client, so an echo glued to identifier
    characters (``hunter2-reset``) is scrubbed here too. Never applied to
    `written_actions`, where a typed ordinary word (``admin``) would rewrite
    the macro's own ``#admin-menu``.
    """
    return with_session_values(session, run_values)


def written_actions(actions: list[Any], privacy_for: Callable[[Any], MacroArgPrivacy]) -> list[Any]:
    """*actions* as the macro wrote them, for the payload fields that echo its definition.

    Before substitution, so a placeholder shows as ``{{name}}`` and never as
    the value the step dispatched -- which is how the run's own classified
    values stay out of these fields without a scrub rewriting the macro's
    text. Two redactions still apply, both by structure rather than by value,
    at every depth of a ``try``/``if_selector`` container: a
    ``fill``/``type``/``expect_no_text`` value (`_redact_action`), and a
    ``macro_call``'s arguments, classified by the called macro the way
    ``args_used`` is (*privacy_for* maps a macro name to its view), since a
    step may pass a credential literally.
    """
    return [_written(action, privacy_for) for action in actions]


def register_written_steps(
    actions: list[Any], expanded: list[Any], privacy_for: Callable[[Any], MacroArgPrivacy]
) -> None:
    """Let a ``try``/``try_each`` that suppresses a step of *expanded* report it as written.

    The `written_actions` form, so the record gets the payload's redactions too.
    Skipped for a macro with no ``try``: nothing would ever look its steps up.
    """
    if conditional.has_try(actions):
        conditional.register_written_steps(written_actions(actions, privacy_for), expanded)


def tracking_substitute(substitute: Any, privacy_for: Callable[[Any], MacroArgPrivacy]) -> Any:
    """*substitute*, registering the written twin of every step it expands (a called macro's)."""

    def expand(actions: list[Any], args: dict[str, Any], **kwargs: Any) -> list[Any]:
        expanded: list[Any] = substitute(actions, args, **kwargs)
        register_written_steps(actions, expanded, privacy_for)
        return expanded

    return expand


def _written(value: Any, privacy_for: Callable[[Any], MacroArgPrivacy]) -> Any:
    if isinstance(value, list):
        return [_written(item, privacy_for) for item in value]
    if not isinstance(value, dict):
        return value
    shown = _redact_action({key: _written(item, privacy_for) for key, item in value.items()})
    call_args = value.get("args")
    if shown.get("action") == "macro_call" and isinstance(call_args, dict):
        shown["args"] = privacy_for(shown.get("name")).redact(call_args, marker=_REDACTED_MACRO_VALUE)
    return shown


def payload_url(url: Any) -> Any:
    """*url* with userinfo, query and fragment removed; origin and path kept.

    The payload goes over MCP to a model and its logs. A signed URL, a session
    id or an app-generated token rides the query string, and none of those is a
    macro argument, so the argument scrubber never knew to remove it. The path
    is what identifies the failing endpoint; the rest is rarely the diagnosis.
    The rule is the macro digest's (`digest.sanitize_url`), so the two cannot
    drift; a non-string is passed through untouched.
    """
    return sanitize_url(url) if isinstance(url, str) else url


def failed_requests_tail(session: SessionLike) -> list[dict[str, Any]]:
    """The newest failed / non-2xx requests, for a failure payload.

    Reads the session's own bounded deque rather than taking a window from the
    failing step: the deque has no per-step boundary, and a request the page
    issued moments before the step began is exactly as likely to be the cause.
    Newest-first bounding is what keeps it relevant. URLs go through
    :func:`payload_url`; the rows are copies, so the session's history is not
    rewritten.
    """
    try:
        rows = session.get_network_requests(limit=None)["requests"]
    except Exception as exc:
        # Empty rather than raised (module docstring), but logged: the repo's
        # silent-swallow policy covers teardown and parse-skips, not a
        # user-facing failure payload quietly missing its network block. The
        # type only: the message of a session-side failure is not scrubbed here.
        log.debug("octowright.macro.failure_network_tail_failed", error_type=type(exc).__name__)
        return []
    failed = [row for row in rows if row.get("failure") or (row.get("status") or 0) >= 400]
    return [{**row, "url": payload_url(row.get("url"))} for row in failed[-MACRO_FAILURE_NETWORK_TAIL:]]


def page_errors_tail(session: SessionLike) -> list[dict[str, Any]]:
    """The newest uncaught page exceptions, as copies."""
    try:
        errors = list(session.page_errors)
    except Exception as exc:
        log.debug("octowright.macro.failure_page_errors_tail_failed", error_type=type(exc).__name__)
        return []
    return [dict(error) for error in errors[-MACRO_FAILURE_PAGE_ERROR_TAIL:]]


def _truncate_console_message(message: Any) -> Any:
    """Return ``message`` with an over-long ``text`` capped, never mutated."""
    if not isinstance(message, dict):
        return message
    text = message.get("text")
    if not isinstance(text, str) or len(text) <= MACRO_FAILURE_CONSOLE_TEXT_CHARS:
        return message
    return {**message, "text": text[:MACRO_FAILURE_CONSOLE_TEXT_CHARS] + "…[truncated]"}


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


async def _diagnostic_bundle(session: SessionLike, sensitive_values: tuple[str, ...]) -> dict[str, Any]:
    """The diagnostic producer's bundle, split by sink kind (#248).

    The page may render a value this run or the session ledger holds (a
    classified argument, a password typed earlier). Text can be scrubbed and
    pixels cannot: with any value held, the producer writes its HTML file and
    returns its console tail scrubbed of them all, and takes no screenshot.
    Composition roots can retain their own explicitly safe evidence at the
    authorized screenshot boundary. The returned bundle is scrubbed once more
    here, for a producer that did not apply the scrub, and then cut to size --
    in that order, so a cut cannot leave the start of a value behind.
    """
    try:
        if sensitive_values:
            raw = await session.diagnostic_bundle(
                console_tail=MACRO_FAILURE_CONSOLE_TAIL,
                scrub=lambda value: _scrubbed(value, sensitive_values),
                screenshot=False,
            )
        else:
            raw = await session.diagnostic_bundle(console_tail=MACRO_FAILURE_CONSOLE_TAIL)
    except Exception as secondary:
        return {"diagnostic_error": _scrubbed(repr(secondary), sensitive_values)}
    bundle = _scrubbed(raw, sensitive_values) if isinstance(raw, dict) else {}
    return _truncate_bundle_console(bundle)


def _scrubbed(value: Any, sensitive_values: tuple[str, ...]) -> Any:
    """*value* scrubbed of every one of *sensitive_values*, anywhere, flat: see `failure_scrub_values`."""
    return scrub_sensitive_values(value, sensitive_values, marker=_REDACTED_MACRO_VALUE)


async def build_failure_payload(
    session: SessionLike,
    *,
    name: str,
    index: int,
    written: list[dict[str, Any]],
    privacy_for: Callable[[Any], MacroArgPrivacy],
    executed: int,
    safe_original: str,
    sensitive_values: tuple[str, ...],
    suggest_fix: Callable[..., Awaitable[Any]],
) -> dict[str, Any]:
    """Assemble the failure payload from three independently-fallible producers.

    Each producer is tried separately so one failing does not cost the caller
    the other two: its own error is recorded IN the payload rather than raised
    over the dispatch failure the payload exists to explain. *written* is the
    macro's steps before substitution: the fields echoing them are not
    scrubbed of *sensitive_values*, the page-derived ones are.
    """
    bundle = await _diagnostic_bundle(session, sensitive_values)

    shown = written_actions(written[: index + 1], privacy_for)
    try:
        fix_suggestion = await suggest_fix(
            session, shown[index], scrub_page=lambda text: _scrubbed(text, sensitive_values)
        )
    except Exception as secondary:
        fix_suggestion = None
        bundle["healing_error"] = _scrubbed(repr(secondary), sensitive_values)
    try:
        failed_requests = _scrubbed(failed_requests_tail(session), sensitive_values)
        page_errors = _scrubbed(page_errors_tail(session), sensitive_values)
    except Exception as secondary:  # defensive around injected session implementations
        failed_requests, page_errors = [], []
        bundle["network_error"] = _scrubbed(repr(secondary), sensitive_values)

    payload: dict[str, Any] = {
        "macro": name,
        "failed_at_step": index,
        # Partial-state signal: a multi-step macro that fails midway has
        # already applied steps 0..index-1 to the live browser. Surface both
        # the count and the steps that landed, as the macro wrote them, so the
        # agent can reason about the half-applied state instead of seeing an
        # opaque error.
        "executed": executed,
        "executed_actions": shown[:index],
        "failed_action": shown[index],
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
        # What an ``N page error(s)`` failure counted: uncaught exceptions are
        # not console messages, so the console tail never shows them.
        "page_errors": page_errors,
    }
    if fix_suggestion:
        payload["healing_suggestion"] = fix_suggestion
    return payload
