# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What ``expect_network_clean`` counts and waits for, shared with the exported CLI.

A package-root module, like ``console_levels``, so the session and the exported
macro CLI (which must not import the browser stack) share one implementation:
``NetworkLedger``, ``settle_network``, ``request_frame`` and ``is_http_error``
are rendered into the generated script from their source here, so the two
cannot drift. Everything below is plain Python over duck-typed event payloads;
nothing imports Playwright.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

#: A cancelled request is not the page failing: navigating away aborts what is
#: in flight, and apps cancel their own fetches. Measured on Playwright 1.62,
#: Linux, with an AbortController-cancelled fetch: Chromium ``net::ERR_ABORTED``,
#: Firefox ``NS_BINDING_ABORTED``, WebKit ``Load request cancelled`` (the same
#: two Firefox/WebKit spellings for navigating away; Chromium fires no
#: ``requestfailed`` there). ``cancelled`` is NOT measured: it is macOS
#: CFNetwork's NSURLErrorCancelled description, which WebKit on macOS reports
#: through a different network stack than the Linux one measured here.
ABORTED_REQUEST_FAILURES: frozenset[str] = frozenset(
    {"net::ERR_ABORTED", "NS_BINDING_ABORTED", "Load request cancelled", "cancelled"}
)

#: Resource types whose 4xx/5xx ``expect_network_clean(http_errors=True)``
#: counts: page loads and API calls, which are what "the journey worked" means.
#: A missing image, font or favicon is cosmetic and would make the check fail
#: on pages whose flow is fine.
HTTP_ERROR_RESOURCE_TYPES: frozenset[str] = frozenset({"document", "fetch", "xhr"})


#: Resource types a settle wait must not wait for: they stay open by design.
LONG_LIVED_RESOURCE_TYPES: frozenset[str] = frozenset({"eventsource", "websocket", "media"})

#: Requests tracked as in flight at once; the oldest is dropped past this.
INFLIGHT_REQUEST_LIMIT = 1000
#: How long expect_network_clean waits, by default, for in-flight requests to end.
NETWORK_SETTLE_TIMEOUT_MS = 5000
#: Quiet time after the last in-flight request ends, for the follow-up it triggers.
NETWORK_QUIET_SECONDS = 0.1
#: How often the settle wait looks while something is still in flight.
NETWORK_SETTLE_POLL_SECONDS = 0.05


def is_http_error(status: object, resource_type: object) -> bool:
    return isinstance(status, int) and status >= 400 and resource_type in HTTP_ERROR_RESOURCE_TYPES


def request_frame(request: Any) -> Any:
    """The frame that made *request*, or None: a service worker's request has none and raises."""
    try:
        return request.frame
    except Exception:
        return None


class NetworkLedger:
    """The running counts ``expect_network_clean`` judges, and the requests it waits for.

    Counters rather than a read-back of a bounded request log: a failure the
    log evicted must still count. A window is a snapshot of the counters --
    (request failures, page errors, HTTP errors, untracked requests) -- taken at
    the start of a macro run (``mark_run``) or at a ``mark_network_clean`` step
    (``mark``), and judged by subtraction.

    In flight: each request started and not yet finished or failed, mapped to
    (page, frame, is-navigation). Bounded at ``INFLIGHT_REQUEST_LIMIT``, oldest
    dropped and counted in ``evicted``, because a request whose end event never
    arrives would otherwise stay forever. Indexed by frame and by page too, so
    a detach, commit or close touches only its own requests rather than
    copying the whole map.

    Event methods take Playwright's event payloads as they arrive, so the
    exported CLI can hand them to ``page.on`` directly.
    """

    def __init__(self) -> None:
        self.failed_requests = 0
        self.page_errors = 0
        self.http_errors = 0
        self.evicted = 0
        self.run_mark: tuple[int, int, int, int] = (0, 0, 0, 0)
        self.explicit_mark: tuple[int, int, int, int] | None = None
        self.inflight: OrderedDict[Any, tuple[Any, Any, bool]] = OrderedDict()
        self._by_frame: dict[Any, dict[Any, None]] = {}
        self._by_page: dict[Any, dict[Any, None]] = {}
        # Frames with a navigation request since their last commit, to their
        # page: what tells a cross-document commit from a same-document one.
        self._navigating: dict[Any, Any] = {}

    # --- counters and windows ---------------------------------------------------------

    def counts(self) -> tuple[int, int, int, int]:
        return self.failed_requests, self.page_errors, self.http_errors, self.evicted

    def mark_run(self) -> None:
        self.run_mark = self.counts()

    def mark(self) -> None:
        self.explicit_mark = self.counts()

    def window(self, since: str) -> tuple[int, int, int, int]:
        """The counts at the start of the *since* window; refused before anything waits."""
        if since == "run":
            return self.run_mark
        if since != "mark":
            raise ValueError(f'unknown since={since!r}; expected "run" or "mark"')
        if self.explicit_mark is None:
            raise RuntimeError(
                'expect_network_clean(since="mark") needs an earlier mark_network_clean step in this session'
            )
        return self.explicit_mark

    def since(self, window: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        now = self.counts()
        return now[0] - window[0], now[1] - window[1], now[2] - window[2], now[3] - window[3]

    def judge(self, window: tuple[int, int, int, int], http_errors: bool) -> dict[str, int]:
        """The window's counts; raises if it is not clean. Counts only: a URL can carry a credential."""
        failed, page_errors, http_error_count, _evicted = self.since(window)
        counts = {"failed_requests": failed, "page_errors": page_errors}
        if http_errors:
            counts["http_errors"] = http_error_count
        if any(counts.values()):
            detail = f"{failed} failed request(s), {page_errors} page error(s)"
            if http_errors:
                detail += f", {http_error_count} HTTP error(s)"
            raise RuntimeError(f"network not clean: {detail}")
        return counts

    def response(self, response: Any) -> None:
        if is_http_error(response.status, response.request.resource_type):
            self.http_errors += 1

    def request_failed(self, request: Any) -> None:
        self._forget(request)
        if request.failure and request.failure not in ABORTED_REQUEST_FAILURES:
            self.failed_requests += 1

    def page_error(self, _error: object = None) -> None:
        self.page_errors += 1

    # --- in flight --------------------------------------------------------------------

    def pending(self) -> int:
        """Requests still in flight; a closed page's are dropped by ``page_closed``."""
        return len(self.inflight)

    def request_started(self, request: Any, page: Any) -> None:
        if request.resource_type in LONG_LIVED_RESOURCE_TYPES:
            return
        frame = request_frame(request)
        navigation = frame is not None and request.is_navigation_request() is True
        if navigation:
            self._navigating[frame] = page
        if len(self.inflight) >= INFLIGHT_REQUEST_LIMIT:
            self._forget(next(iter(self.inflight)))
            # Still running, just no longer watched: reported, so a settle that
            # returns early is not mistaken for one that saw everything end.
            self.evicted += 1
        self._forget(request)
        self.inflight[request] = (page, frame, navigation)
        self._by_page.setdefault(page, {})[request] = None
        if frame is not None:
            self._by_frame.setdefault(frame, {})[request] = None

    def request_finished(self, request: Any) -> None:
        self._forget(request)

    def frame_navigated(self, frame: Any, page: Any) -> None:
        """Forget the requests of a document this commit replaced.

        They can no longer finish observably: measured on Chromium, a fetch
        cancelled by navigating away fires neither ``requestfinished`` nor
        ``requestfailed``, so without this every later settle wait ran to its
        timeout. Only a cross-document commit -- one preceded by a navigation
        request for the frame -- replaces anything; ``history.pushState`` and a
        fragment change fire this event too, and that document's fetches are
        still live. The navigation request itself is kept (its body may still
        be streaming), and a main-frame commit replaces every frame of the page.
        """
        if self._navigating.pop(frame, None) is None:
            return
        try:
            whole_page = frame is page.main_frame
        except Exception:
            whole_page = False
        index = self._by_page if whole_page else self._by_frame
        for request in list(index.get(page if whole_page else frame, ())):
            owner, owner_frame, navigation = self.inflight[request]
            if owner is page and not (navigation and owner_frame is frame):
                self._forget(request)

    def frame_detached(self, frame: Any) -> None:
        """A detached frame's requests will never finish; stop waiting for them."""
        self._navigating.pop(frame, None)
        for request in list(self._by_frame.get(frame, ())):
            self._forget(request)

    def page_closed(self, page: Any) -> None:
        """A closed page's requests will never finish; stop waiting for them."""
        for request in list(self._by_page.get(page, ())):
            self._forget(request)
        for frame, owner in list(self._navigating.items()):
            if owner is page:
                del self._navigating[frame]

    def clear_inflight(self) -> None:
        self.inflight.clear()
        self._by_frame.clear()
        self._by_page.clear()
        self._navigating.clear()

    def _forget(self, request: Any) -> None:
        if not self.inflight:
            return  # the common case while tracking is off: nothing to look up
        entry = self.inflight.pop(request, None)
        if entry is None:
            return
        page, frame, _navigation = entry
        for index, key in ((self._by_page, page), (self._by_frame, frame)):
            bucket = index.get(key)
            if bucket is not None:
                bucket.pop(request, None)
                if not bucket:
                    del index[key]


async def settle_network(pending: Callable[[], int], timeout_ms: int) -> int:
    """Wait until nothing is in flight for a quiet interval; return what is still pending.

    Bounded: a page that long-polls never settles, and the check then judges
    what has happened so far instead of hanging or failing on a request that
    has not failed.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if pending() == 0:
            await asyncio.sleep(min(NETWORK_QUIET_SECONDS, max(0.0, deadline - time.monotonic())))
            if pending() == 0:
                return 0
        else:
            await asyncio.sleep(min(NETWORK_SETTLE_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    return pending()
