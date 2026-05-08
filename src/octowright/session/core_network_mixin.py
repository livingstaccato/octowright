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

from octowright.session._protocols import SessionLike


def _matches_url(url_filter: str) -> Callable[[dict[str, Any]], bool]:
    return lambda r: url_filter in r.get("url", "")


def _matches_method(method_filter: str) -> Callable[[dict[str, Any]], bool]:
    target = method_filter.upper()
    return lambda r: r.get("method", "").upper() == target


def _matches_resource_type(resource_type_filter: str) -> Callable[[dict[str, Any]], bool]:
    return lambda r: r.get("resource_type") == resource_type_filter


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
    ) -> dict[str, Any]:
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
        sliced = [r for r in retained[start:] if all(p(r) for p in predicates)]
        return {
            "requests": sliced,
            "next_cursor": retained_base + len(retained),
            "total": len(retained),
            "total_retained": len(retained),
            "dropped": self._network_requests_dropped,
        }
