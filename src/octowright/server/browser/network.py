# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Network/dialog tools: dialog policies + route mocking."""

from __future__ import annotations

from collections import Counter
from typing import Any
from urllib.parse import urlparse

from octowright.server._state import mcp, pool
from octowright.server.browser._operation import browser_operation
from octowright.server.profiles import annotate_next_actions_for_profile


def _sorted_counts(counter: Counter[str], *, limit: int = 20) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[: max(0, limit)]
    ]


def _host_for_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return "unknown-host"
    return parsed.netloc or "unknown-host"


def _parsed_url_parts(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(url)
    except Exception:
        return ("unknown-host", "/")
    host = parsed.netloc or "unknown-host"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return (host, path)


def _status_key(request: dict[str, Any]) -> str:
    status = request.get("status")
    return "failed" if status is None else str(status)


def _status_class_key(request: dict[str, Any]) -> str:
    status = request.get("status")
    if status is None:
        return "failed"
    if isinstance(status, int):
        if 200 <= status <= 299:
            return "2xx"
        if 300 <= status <= 399:
            return "3xx"
        if 400 <= status <= 499:
            return "4xx"
        if 500 <= status <= 599:
            return "5xx"
    return "other"


def _is_http_error(request: dict[str, Any]) -> bool:
    status = request.get("status")
    return isinstance(status, int) and status >= 400


def _is_network_error(request: dict[str, Any]) -> bool:
    return request.get("status") is None


def _ordered_status_class_counts(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(_status_class_key(request) for request in requests)
    order = ["2xx", "3xx", "4xx", "5xx", "failed", "other"]
    return [{"key": key, "count": counter[key]} for key in order if counter[key] > 0]


def _network_summary_action(instance_id: str, url_filter: str) -> dict[str, Any]:
    return {"tool": "browser_network_summary", "args": {"instance_id": instance_id, "url": url_filter}}


def _network_summary_next_actions(instance_id: str, next_cursor: Any) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if next_cursor is not None:
        actions.append({"tool": "browser_network_summary", "args": {"instance_id": instance_id, "since": next_cursor}})
    actions.extend(
        [
            {"tool": "browser_network_summary", "args": {"instance_id": instance_id, "url": "<url-or-host>"}},
            {
                "tool": "capture_create",
                "args": {"instance_id": instance_id, "source": "network", "response_mode": "summary"},
            },
        ]
    )
    return annotate_next_actions_for_profile(actions)


def _failure_record(instance_id: str, request: dict[str, Any]) -> dict[str, Any]:
    url = str(request.get("url") or "")
    host, path = _parsed_url_parts(url)
    out: dict[str, Any] = {
        "url": request.get("url"),
        "host": host,
        "path": path,
        "method": request.get("method"),
        "resource_type": request.get("resource_type"),
        "status": request.get("status"),
        "status_class": _status_class_key(request),
    }
    if request.get("status_text"):
        out["status_text"] = request.get("status_text")
    if request.get("failure"):
        out["failure"] = request.get("failure")
    if url:
        out["action"] = _network_summary_action(instance_id, url)
    return out


def _problem_hosts(instance_id: str, requests: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    by_host: dict[str, list[dict[str, Any]]] = {}
    for request in requests:
        if not _is_failed_request(request):
            continue
        host = _host_for_url(str(request.get("url") or ""))
        by_host.setdefault(host, []).append(request)
    rows: list[dict[str, Any]] = []
    for host, failed_requests in by_host.items():
        rows.append(_problem_host_row(instance_id, requests, host, failed_requests))
    rows.sort(key=lambda row: (-int(row["failure_count"]), str(row["host"])))
    return rows[: max(0, limit)]


def _is_failed_request(request: dict[str, Any]) -> bool:
    return _is_http_error(request) or _is_network_error(request)


def _requests_for_host(requests: list[dict[str, Any]], host: str) -> list[dict[str, Any]]:
    return [request for request in requests if _host_for_url(str(request.get("url") or "")) == host]


def _problem_host_row(
    instance_id: str,
    requests: list[dict[str, Any]],
    host: str,
    failed_requests: list[dict[str, Any]],
) -> dict[str, Any]:
    all_for_host = _requests_for_host(requests, host)
    return {
        "host": host,
        "total": len(all_for_host),
        "failure_count": len(failed_requests),
        "http_error_count": sum(1 for request in failed_requests if _is_http_error(request)),
        "network_error_count": sum(1 for request in failed_requests if _is_network_error(request)),
        "statuses": _sorted_counts(Counter(_status_key(request) for request in all_for_host)),
        "action": _network_summary_action(instance_id, host),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Set the dialog-handling policy for an instance. `policy` is 'accept', 'dismiss', "
        "or 'manual'. When 'accept' is used with a prompt dialog, `prompt_text` supplies "
        "the response string. Default policy is 'dismiss'."
    ),
)
async def browser_set_dialog_policy(
    instance_id: str,
    policy: str,
    prompt_text: str | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_set_dialog_policy") as session:
        return await session.set_dialog_policy(policy, prompt_text)


@mcp.tool(
    structured_output=False,
    description=(
        "Stub network responses for requests matching `url_pattern`. The browser will see "
        "your response instead of hitting the network. Use this to make tests deterministic "
        "(freeze a /api/time endpoint, return a fixed user list, simulate a 500 error). "
        "Don't use this to OBSERVE traffic — it short-circuits the request. "
        "`url_pattern` is a glob ('**/api/users') or regex (Playwright auto-detects)."
    ),
)
async def browser_mock_route(
    instance_id: str,
    url_pattern: str,
    status: int = 200,
    body: str | None = None,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_mock_route") as session:
        return await session.mock_route(
            url_pattern,
            status=status,
            body=body,
            content_type=content_type,
            headers=headers,
        )


@mcp.tool(
    structured_output=False,
    description=("Remove a previously-installed mock for `url_pattern`. Raises if no mock was active."),
)
async def browser_unmock_route(instance_id: str, url_pattern: str) -> dict[str, Any]:
    async with browser_operation(pool, instance_id, "browser_unmock_route") as session:
        return await session.unmock_route(url_pattern)


@mcp.tool(
    structured_output=False,
    description=(
        "Return network requests captured by this browser instance. All HTTP/HTTPS requests "
        "are recorded automatically — no setup needed. Each entry has {url, method, "
        "resource_type, status, status_text} (status is None for failed requests). "
        "Filter results with: url (substring match), method ('GET'/'POST'/…), "
        "resource_type ('fetch'/'xhr'/'document'/'script'/'image'/…). "
        "Pass `since` (a cursor from a prior call's next_cursor) to read only new requests — "
        "use this for incremental polling during a test. Pass response_mode='summary' "
        "to return browser_network_summary with the same filters instead of raw rows. "
        "To INTERCEPT and rewrite responses, use browser_mock_route instead."
    ),
)
def browser_network_requests(
    instance_id: str,
    url: str | None = None,
    method: str | None = None,
    resource_type: str | None = None,
    since: int | None = None,
    response_mode: str | None = None,
) -> dict[str, Any]:
    if response_mode == "summary":
        return browser_network_summary(
            instance_id,
            url=url,
            method=method,
            resource_type=resource_type,
            since=since,
        )
    return pool.get(instance_id).get_network_requests(
        url_filter=url,
        method_filter=method,
        resource_type_filter=resource_type,
        since=since,
    )


@mcp.tool(
    structured_output=False,
    description=(
        "Return a compact aggregate summary of captured network traffic without dumping every request. "
        "Use before browser_network_requests to inspect failures, problem hosts, status classes, "
        "dominant hosts, methods, and resource types."
    ),
)
def browser_network_summary(
    instance_id: str,
    url: str | None = None,
    method: str | None = None,
    resource_type: str | None = None,
    since: int | None = None,
    failure_limit: int = 8,
) -> dict[str, Any]:
    result = pool.get(instance_id).get_network_requests(
        url_filter=url,
        method_filter=method,
        resource_type_filter=resource_type,
        since=since,
    )
    requests = list(result.get("requests") or [])
    failures = [request for request in requests if _is_failed_request(request)]
    capped_failures = max(0, min(int(failure_limit), 25))
    summary = _network_summary_counts(requests, result)
    summary.update(
        {
            "has_failures": bool(failures),
            "problem_hosts": _problem_hosts(instance_id, requests),
            "failure_count": len(failures),
            "failures": [_failure_record(instance_id, request) for request in failures[:capped_failures]],
            "failure_limit": capped_failures,
            "next_actions": _network_summary_next_actions(instance_id, result.get("next_cursor")),
        }
    )
    return summary


def _network_summary_counts(requests: list[dict[str, Any]], result: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "total": len(requests),
        "count": len(requests),
        "total_retained": result.get("total_retained", len(requests)),
        "dropped": result.get("dropped", 0),
        "next_cursor": result.get("next_cursor"),
    }
    summary.update(_network_summary_scalar_counts(requests))
    summary.update(_network_summary_breakdowns(requests))
    return summary


def _network_summary_scalar_counts(requests: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "host_count": _host_count(requests),
        "ok_count": _status_class_count(requests, "2xx"),
        "redirect_count": _status_class_count(requests, "3xx"),
        "http_error_count": _http_error_count(requests),
        "network_error_count": _network_error_count(requests),
    }


def _host_count(requests: list[dict[str, Any]]) -> int:
    return len({_host_for_url(str(request.get("url") or "")) for request in requests})


def _status_class_count(requests: list[dict[str, Any]], status_class: str) -> int:
    return sum(1 for request in requests if _status_class_key(request) == status_class)


def _http_error_count(requests: list[dict[str, Any]]) -> int:
    return sum(1 for request in requests if _is_http_error(request))


def _network_error_count(requests: list[dict[str, Any]]) -> int:
    return sum(1 for request in requests if _is_network_error(request))


def _network_summary_breakdowns(requests: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "by_status": _sorted_counts(Counter(_status_key(request) for request in requests)),
        "by_status_class": _ordered_status_class_counts(requests),
        "by_resource_type": _sorted_counts(
            Counter(str(request.get("resource_type") or "unknown") for request in requests)
        ),
        "by_method": _sorted_counts(Counter(str(request.get("method") or "unknown").upper() for request in requests)),
        "by_host": _sorted_counts(Counter(_host_for_url(str(request.get("url") or "")) for request in requests)),
    }
