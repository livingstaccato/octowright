# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Network-event capture for ``BrowserSession``.

Hooks Playwright's ``response`` and ``requestfailed`` page events into the
session's bounded request deque and its ``NetworkLedger`` (the counts and
in-flight requests ``expect_network_clean`` judges, subscribed to lazily once
something will ask), and exposes ``get_network_requests`` for
the dashboard / MCP tools to read back filtered slices with cursor-based
pagination.

Split out of ``core_ops_mixin`` to keep that file under the repository's LOC
ceiling and to give network-capture concerns a single home. A navigation the
SSRF guard answered with a client-redirect document is recorded with the 3xx
the server really sent (``ssrf_guard.client_redirect_of``,
``served_as: "client_redirect"``), not the synthetic 200 the browser saw.
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
from octowright.session.input_redaction import live_scrubbed
from octowright.ssrf_guard import client_redirect_of

log = get_logger(__name__)

#: Bytes of a failed response body retained per row. The body exists to carry
#: a refusal reason (``{"detail": "component_allocation_required"}``), which is
#: short; the cap stops a 500 that returns a rendered HTML error page -- or a
#: 50KB stack trace -- from riding the MCP transport. Override with
#: OCTOWRIGHT_NETWORK_BODY_MAX_BYTES; a falsey token disables capture entirely.
NETWORK_BODY_MAX_BYTES_DEFAULT = 2048
#: Declared ``Content-Length`` above which a failed body is not read at all.
#: ``response.body()`` materialises the WHOLE body before the cap above slices
#: it, so a same-origin endpoint answering 500 with a gigabyte forced a
#: gigabyte into the daemon. Deliberately far above the retained cap: an HTML
#: error page is commonly tens of KB and its first 2 KiB is still worth having.
RESPONSE_BODY_READ_MAX_BYTES = 1024 * 1024
#: gzip and deflate cannot expand by more than ~1032:1, so a compressed body
#: declared at or below this decodes within ``RESPONSE_BODY_READ_MAX_BYTES``.
#: Short refusal reasons -- what the body exists to carry -- fit comfortably.
COMPRESSED_BODY_READ_MAX_BYTES = RESPONSE_BODY_READ_MAX_BYTES // 1032
#: Encodings whose worst-case expansion is bounded by the ratio above. Brotli
#: and zstd are deliberately absent: a few hundred bytes of either can decode to
#: gigabytes, so no declared length makes their bodies safe to read.
_RATIO_BOUNDED_ENCODINGS = frozenset({"gzip", "x-gzip", "deflate"})
#: Failed-body reads in flight per session. Each is one background task holding
#: up to the read ceiling, and a hostile page can fire failing requests at will.
RESPONSE_BODY_READS_IN_FLIGHT_MAX = 8
#: Uncaught page exceptions retained per session. Only the count reaches
#: ``expect_network_clean``; the messages are kept for a human debugging.
PAGE_ERROR_LIMIT = 200
PAGE_ERROR_TEXT_CHARS = 2000
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


def _declared_length(response: Any) -> int | None:
    """The response's ``Content-Length``, or ``None`` when absent or unparsable."""
    headers = getattr(response, "headers", None)
    raw = headers.get("content-length") if isinstance(headers, dict) else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _unbounded_body_reason(response: Any) -> str | None:
    """Why *response*'s body must not be read, or ``None`` when its size is bounded.

    Playwright has no ranged or streaming body read: ``response.body()``
    materialises the whole DECODED body before any cap applies. So a body is
    read only when its decoded size is known to be bounded first. No
    trustworthy ``Content-Length`` (chunked, absent, unparsable) means the size
    is unknown until read -- ``"unknown_length"``. ``Content-Length`` counts
    ENCODED bytes, so a compressed body is read only for a ratio-bounded
    encoding declared under ``COMPRESSED_BODY_READ_MAX_BYTES`` -- else
    ``"encoded"``.
    """
    declared = _declared_length(response)
    if declared is None or declared < 0:
        return "unknown_length"
    if declared > RESPONSE_BODY_READ_MAX_BYTES:
        return "too_large"
    headers = getattr(response, "headers", None)
    encoding = str(headers.get("content-encoding", "") if isinstance(headers, dict) else "").strip().lower()
    if encoding in ("", "identity"):
        return None
    if encoding in _RATIO_BOUNDED_ENCODINGS and declared <= COMPRESSED_BODY_READ_MAX_BYTES:
        return None
    return "encoded"


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
    # Declared on SessionLike too; repeated because this mixin assigns it, and
    # mypy otherwise reports its first read here as "Cannot determine type".
    _inflight_tracking: bool
    #: Failed-body reads scheduled and not yet finished; see
    #: ``RESPONSE_BODY_READS_IN_FLIGHT_MAX``. A class default so a bare mixin
    #: subject needs no setup.
    _body_reads_in_flight: int = 0

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
        redirect = client_redirect_of(request)
        if redirect is not None:
            # The browser saw the SSRF guard's client-redirect document; the
            # server answered a 3xx, and that is what the chain should show.
            row.update(
                status=redirect["status"],
                status_text=redirect["status_text"],
                redirect_location=redirect["location"],
                served_as="client_redirect",
            )
        # Scrubbed BEFORE it is appended: the body read below mutates this
        # same dict in place once it lands, so it must be the buffered copy.
        row = live_scrubbed(self, row)
        self._append_network_request(row)
        self._network.response(response)
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
        if self._body_reads_in_flight >= RESPONSE_BODY_READS_IN_FLIGHT_MAX:
            row["body_skipped"] = "busy"
            return
        self._body_reads_in_flight += 1
        task = loop.create_task(self._read_response_body(response, row, cap, request_url))
        self._bg_tasks.add(task)
        task.add_done_callback(self._body_read_done)

    def _body_read_done(self, task: Any) -> None:
        self._body_reads_in_flight -= 1
        self._bg_tasks.discard(task)

    async def _read_response_body(self, response: Any, row: dict[str, Any], cap: int, url: str) -> None:
        """Best-effort: a body that cannot be read leaves the row as it was.

        Never raises. This runs detached from any caller, so an exception here
        would surface as an unretrievable task error rather than reaching
        anyone who could act on it -- and a missing body must degrade to
        today's behaviour, not to a broken response record.
        """
        skipped = _unbounded_body_reason(response)
        if skipped is not None:
            row["body_skipped"] = skipped
            if skipped == "too_large":
                row["body_size"] = _declared_length(response)
            return
        try:
            body = await response.body()
        except Exception as exc:
            log.debug("octowright.session.response_body_unavailable", url=url, error=repr(exc))
            return
        row["body_truncated"] = len(body) > cap
        row["body"] = live_scrubbed(self, body[:cap].decode("utf-8", errors="replace"))

    def enable_inflight_tracking(self) -> None:
        """Track requests in flight on every page, now and opened later. Idempotent.

        Off until something will judge it -- a ``mark_network_clean`` step, a
        macro run that asserts ``expect_network_clean``, or a direct call to it.
        Enabled by that direct call, the settle wait sees only requests started
        from then on, which is why the other two enable it earlier. A macro run
        turns it off again when it ends (``disable_inflight_tracking``).
        """
        if self._inflight_tracking:
            return
        self._inflight_tracking = True
        for page in list(self.pages):
            self._track_page_requests(page)

    def disable_inflight_tracking(self) -> bool:
        """Stop tracking requests in flight, unless a ``mark_network_clean`` window is open.

        Called when a macro run ends, so the cost is paid only while something
        will judge it. An open mark keeps it on: a verify macro checking
        ``since="mark"`` judges requests the journey started, and a request
        started between the two runs while tracking was off would not be waited
        for. The mark is never closed, so after one the session keeps tracking.

        Entries are dropped with the listeners: nothing would see them end.
        Returns whether tracking was turned off.
        """
        if not self._inflight_tracking or self._network.explicit_mark is not None:
            return False
        self._inflight_tracking = False
        for page, handlers in list(self._tracked_pages.items()):
            for event, handler in handlers:
                try:
                    page.remove_listener(event, handler)
                except Exception as exc:
                    # A closed page has no listeners left to remove.
                    log.debug("octowright.session.inflight_listener_remove_failed", page_event=event, error=repr(exc))
        self._tracked_pages.clear()
        self._network.clear_inflight()
        return True

    def _track_page_requests(self, page: Any) -> None:
        if page in self._tracked_pages:
            return
        ledger = self._network
        handlers: list[tuple[str, Any]] = [
            ("request", lambda request: ledger.request_started(request, page)),
            ("requestfinished", ledger.request_finished),
            ("framenavigated", lambda frame: ledger.frame_navigated(frame, page)),
            ("framedetached", ledger.frame_detached),
        ]
        # Kept per page so disable_inflight_tracking can remove exactly these.
        self._tracked_pages[page] = handlers
        for event, handler in handlers:
            page.on(event, handler)

    def _handle_request_failed(self, request: Any) -> None:
        self._network.request_failed(request)
        self._append_network_request(
            live_scrubbed(
                self,
                {
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "status": None,
                    "failure": request.failure,
                    "headers": _recorded_headers(request),
                },
            )
        )

    def _handle_page_error(self, error: Any) -> None:
        self.page_errors.append(live_scrubbed(self, {"message": str(error)[:PAGE_ERROR_TEXT_CHARS]}))
        self._network.page_error()

    def mark_network_clean_window(self) -> None:
        """Start the per-run window ``expect_network_clean`` judges. Called per macro run."""
        self._network.mark_run()

    def network_failures_since(self, since: str = "run") -> tuple[int, int, int]:
        """(request failures, page errors, HTTP errors) since the run start or the explicit mark."""
        failed, page_errors, http_errors, _evicted = self._network.since(self._network.window(since))
        return failed, page_errors, http_errors

    def _forget_page_requests(self, page: Any) -> None:
        """Wired to the page's ``close``: its requests will never finish.

        Also drops its listener entry. The handlers close over the page, so an
        entry left in the weak-keyed map would keep its own key -- and every
        page the session ever opened -- alive.
        """
        self._network.page_closed(page)
        self._tracked_pages.pop(page, None)

    def pending_requests(self) -> int:
        """Requests still in flight; a closed page's were dropped when it closed."""
        return self._network.pending()

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
