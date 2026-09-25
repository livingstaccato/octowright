# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The network and page-error context a macro failure payload carries.

Split out of ``execution.py`` (at the repository's LOC ceiling). Both producers
are best-effort: a session that cannot answer must not turn a macro failure
into a different, more confusing failure, so anything raised here yields an
empty block rather than replacing the real error.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from provide.telemetry import get_logger

from octowright.artifacts.digest import sanitize_url

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

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
