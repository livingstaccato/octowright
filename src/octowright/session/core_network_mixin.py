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

from collections.abc import Callable
from typing import Any

from octowright.http_headers import redact_header_values
from octowright.session._protocols import SessionLike
from octowright.session.aria_redaction import resolve_redaction_mode


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
        self._append_network_request(
            {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "status": response.status,
                "status_text": response.status_text,
                "headers": _recorded_headers(request),
            }
        )

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

        ``limit`` caps the rows in ONE read. There was previously no cap at
        all, so a read could return the whole 5000-entry deque.
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
