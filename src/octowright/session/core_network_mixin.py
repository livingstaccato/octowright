# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Network-event capture for ``BrowserSession``.

Hooks Playwright's ``response`` and ``requestfailed`` page events into the
session's bounded request deque, and exposes ``get_network_requests`` for
the dashboard / MCP tools to read back filtered slices with cursor-based
pagination.

Split out of ``core_ops_mixin`` to keep that file under the 500-LOC ratchet
and to give network-capture concerns a single home.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from provide.telemetry import get_logger

from octowright.http_headers import redact_header_values
from octowright.session._protocols import SessionLike
from octowright.session.aria_redaction import resolve_redaction_mode

log = get_logger(__name__)

#: Bytes of a failed response body retained per row. The body exists to carry
#: a refusal reason (``{"detail": "component_allocation_required"}``), which is
#: short; the cap stops a 500 that returns a rendered HTML error page -- or a
#: 50KB stack trace -- from riding the MCP transport. Override with
#: OCTOWRIGHT_NETWORK_BODY_MAX_BYTES; a falsey token disables capture entirely.
NETWORK_BODY_MAX_BYTES_DEFAULT = 2048
_FALSEY = frozenset({"0", "off", "false", "no", "never", "none", "disabled"})


def _matches_url(url_filter: str) -> Callable[[dict[str, Any]], bool]:
    return lambda r: url_filter in r.get("url", "")


def _matches_method(method_filter: str) -> Callable[[dict[str, Any]], bool]:
    target = method_filter.upper()
    return lambda r: r.get("method", "").upper() == target


def _matches_resource_type(resource_type_filter: str) -> Callable[[dict[str, Any]], bool]:
    return lambda r: r.get("resource_type") == resource_type_filter


def _recorded_headers(request: Any) -> dict[str, str]:
    """Request headers as they should be RECORDED, scrubbed by header name.

    These records had no headers at all, which made every header feature
    unverifiable from the tool surface: a field report set a launch header,
    looked here to confirm it applied, saw nothing, and nearly concluded the
    feature was broken -- it took a local echo server to prove otherwise.

    Scrubbed with the same name-based policy the JSONL recorder uses, because
    the headers a browser sends include ``Cookie`` and ``Authorization`` and
    this output goes to an LLM. ``request.headers`` is the synchronous
    property (``all_headers()`` is async and this runs in an event handler);
    it can omit a few values the async form would return, which is an accepted
    cost for not blocking the handler.
    """
    try:
        raw = dict(request.headers)
    except Exception:
        return {}
    return redact_header_values(raw, resolve_redaction_mode())


def network_body_max_bytes() -> int:
    """Per-body byte cap, or 0 when body capture is off.

    Unparsable / negative falls back to the default rather than to disabled:
    this is a diagnostic that is ON by default, and a typo must not silently
    remove the one field that explains a failure. An explicit falsey token is
    the way to turn it off.
    """
    raw = os.environ.get("OCTOWRIGHT_NETWORK_BODY_MAX_BYTES")
    if raw is None:
        return NETWORK_BODY_MAX_BYTES_DEFAULT
    if raw.strip().lower() in _FALSEY:
        return 0
    try:
        value = int(raw)
    except ValueError:
        return NETWORK_BODY_MAX_BYTES_DEFAULT
    if value < 0:
        return NETWORK_BODY_MAX_BYTES_DEFAULT
    return value


def _same_origin(candidate: str, page_url: str) -> bool:
    """Whether *candidate* shares an origin with the page.

    A third party's response body is not the caller's to collect, so only the
    application under test is read. Compared against the session's own ``url``
    string rather than a live ``page.url`` read: this runs in an event handler,
    where a Playwright property read is exactly what the operation-gate
    architecture forbids -- the same reason ``_notify_call_timeout`` uses the
    plain field. It can lag a navigation the tools did not drive, which costs
    a body we could have kept, never one we should not have.
    """
    if not page_url:
        return False
    try:
        left, right = urlsplit(candidate), urlsplit(page_url)
    except ValueError:
        return False
    return bool(left.scheme) and (left.scheme, left.netloc) == (right.scheme, right.netloc)


def _project_request(row: dict[str, Any], include_headers: bool) -> dict[str, Any]:
    """One returned row: a COPY, with headers dropped unless asked for.

    Copied because ``list(deque)`` copies the list and not the dicts inside it,
    so handing back originals lets one reader's in-place edit rewrite the
    session's history for every later reader.
    """
    projected = {key: value for key, value in row.items() if key != "headers"}
    headers = row.get("headers")
    if include_headers and headers is not None:
        projected["headers"] = dict(headers)
    return projected


def _page_requests(
    retained: list[dict[str, Any]],
    retained_base: int,
    start: int,
    predicates: list[Callable[[dict[str, Any]], bool]],
    include_headers: bool,
    limit: int | None,
) -> tuple[list[dict[str, Any]], int, bool]:
    """Rows for one read, plus the cursor to resume from and whether it capped.

    When capped, the cursor is the absolute index of the first MATCHING row not
    returned -- not the row after the last one returned. The cursor indexes the
    unfiltered stream, so resuming from the wrong one silently loses every
    match the cap left behind.
    """
    rows: list[dict[str, Any]] = []
    for offset in range(start, len(retained)):
        row = retained[offset]
        if not all(predicate(row) for predicate in predicates):
            continue
        if limit is not None and len(rows) >= limit:
            return rows, retained_base + offset, True
        rows.append(_project_request(row, include_headers))
    return rows, retained_base + len(retained), False


class SessionNetworkMixin(SessionLike):
    def _handle_response(self, response: Any) -> None:
        request = response.request
        row: dict[str, Any] = {
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "status": response.status,
            "status_text": response.status_text,
            "headers": _recorded_headers(request),
        }
        self._append_network_request(row)
        self._maybe_capture_body(response, row)

    def _maybe_capture_body(self, response: Any, row: dict[str, Any]) -> None:
        """Schedule a body read for a failed same-origin response.

        Without the body, a failing request is recoverable only as its status
        code -- and a 409 from one endpoint can have many distinct causes, so
        the code alone is not actionable. The refusal reason is already on the
        wire and is usually the entire diagnosis.

        Read EAGERLY, in a task, because it cannot be read later: measured
        against Chromium, a body requested after the page has navigated away
        fails with ``Protocol error (Network.getResponseBody): No resource
        with given identifier``. A lazy read at tool-call time would therefore
        return nothing precisely when someone is investigating a failure.

        Scoped to non-2xx so an ordinary page costs nothing -- successful
        bodies are large, numerous and rarely interesting -- and to same-origin
        so a third party's response is not collected. The row is mutated in
        place once the read lands; it is the same dict already in the deque.
        """
        cap = network_body_max_bytes()
        if cap <= 0 or 200 <= response.status < 300:
            return
        if not _same_origin(request_url := row["url"], self.url or ""):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop (a sync test harness driving the handler directly);
            # metadata is already recorded, the body is simply not fetched.
            return
        task = loop.create_task(self._read_response_body(response, row, cap, request_url))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _read_response_body(self, response: Any, row: dict[str, Any], cap: int, url: str) -> None:
        """Best-effort: a body that cannot be read leaves the row as it was.

        Never raises. This runs detached from any caller, so an exception here
        would surface as an unretrievable task error rather than reaching
        anyone who could act on it -- and a missing body must degrade to
        today's behaviour, not to a broken response record.
        """
        try:
            body = await response.body()
        except Exception as exc:
            log.debug("octowright.session.response_body_unavailable", url=url, error=repr(exc))
            return
        row["body_truncated"] = len(body) > cap
        row["body"] = body[:cap].decode("utf-8", errors="replace")

    def _handle_request_failed(self, request: Any) -> None:
        self._append_network_request(
            {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": None,
                "failure": request.failure,
                "headers": _recorded_headers(request),
            }
        )

    def _append_network_request(self, request: dict[str, Any]) -> None:
        if self._network_requests.maxlen is not None and len(self._network_requests) == self._network_requests.maxlen:
            self._network_requests_dropped += 1
        self._network_requests.append(request)

    def get_network_requests(
        self,
        url_filter: str | None = None,
        method_filter: str | None = None,
        resource_type_filter: str | None = None,
        since: int | None = None,
        include_headers: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Read back a filtered, cursor-paginated slice of the request deque.

        ``include_headers`` is opt-in because a recorded row's header map is
        most of its size -- ~900 JSON chars against ~130 without, measured on a
        typical Chromium navigation header set, nearly all of it identical
        boilerplate (``user-agent``, ``sec-ch-ua*``, ``accept``) repeated per
        row. Always-on, an unfiltered read of an ordinary page went from
        roughly 6.6k tokens to 45k. Ask for them to verify a header actually
        rode the request; leave them off for ordinary traffic inspection.

        ``limit`` caps the rows in ONE read. Uncapped, a read returns the
        whole 5000-entry deque.
        """
        retained = list(self._network_requests)
        retained_base = self._network_requests_dropped
        start = 0 if since is None else max(0, since - retained_base)
        predicates = [
            pred(value)
            for value, pred in (
                (url_filter, _matches_url),
                (method_filter, _matches_method),
                (resource_type_filter, _matches_resource_type),
            )
            if value
        ]
        rows, next_cursor, truncated = _page_requests(
            retained, retained_base, start, predicates, include_headers, limit
        )
        return {
            "requests": rows,
            "next_cursor": next_cursor,
            "total": len(retained),
            "total_retained": len(retained),
            "dropped": self._network_requests_dropped,
            "returned": len(rows),
            "truncated": truncated,
        }
