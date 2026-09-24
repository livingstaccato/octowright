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
from urllib.parse import urlsplit, urlunsplit

from provide.telemetry import get_logger

if TYPE_CHECKING:
    from octowright.session._protocols import SessionLike

log = get_logger(__name__)

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
    """
    if not isinstance(url, str):
        return url
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        # Unparsable: keep only what precedes any query/fragment/userinfo marker.
        return url.split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1]


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
