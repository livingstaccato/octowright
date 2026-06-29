# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""HTTP-first web discovery tools.

These tools help an LLM find links and page structure before escalating to a
browser session. They intentionally return compact, structured candidates
instead of HTML, markdown, screenshots, or accessibility trees.
"""

from __future__ import annotations

import html
import ipaddress
import re
import socket
from collections.abc import Iterable
from typing import cast
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx
from defusedxml import ElementTree  # type: ignore[import-untyped]
from httpcore._backends.auto import AutoBackend
from httpx._config import DEFAULT_LIMITS, create_ssl_context

from octowright.mcp_types import (
    BrowserActionSuggestion,
    BrowserToolAction,
    WebFindLinksResult,
    WebLinkCandidate,
    WebPageOutlineResult,
    WebSiteLinksResult,
)
from octowright.server._state import mcp
from octowright.server.profiles import annotate_next_actions_for_profile
from octowright.server.web_parse import parse_page
from octowright.text_scoring import weighted_text_score

_MAX_HTML_BYTES = 1_000_000
_MAX_LINKS = 200
_MAX_HEADINGS = 20
_MAX_REDIRECTS = 5
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata", "metadata.google.internal"})
_COMMON_SITE_PATHS = (
    "/docs",
    "/docs/",
    "/documentation",
    "/api",
    "/pricing",
    "/login",
    "/signin",
    "/sign-in",
    "/signup",
    "/sign-up",
    "/contact",
    "/support",
)
_DEFAULT_SITE_TERMS = ("docs", "documentation", "api", "pricing", "login", "signin", "signup", "support", "contact")


class _PinnedDNSBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, host_to_ips: dict[str, list[str]]) -> None:
        self._host_to_ips = host_to_ips
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        pinned = self._host_to_ips.get(host.lower())
        connect_host = pinned[0] if pinned else host
        return await self._backend.connect_tcp(
            connect_host,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:  # pragma: nocover
        return await self._backend.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedDNSAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(self, host_to_ips: dict[str, list[str]]) -> None:
        ssl_context = create_ssl_context(verify=True, cert=None, trust_env=True)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=DEFAULT_LIMITS.max_connections,
            max_keepalive_connections=DEFAULT_LIMITS.max_keepalive_connections,
            keepalive_expiry=DEFAULT_LIMITS.keepalive_expiry,
            network_backend=_PinnedDNSBackend(host_to_ips),
        )


def _clean_limit(limit: int, *, default: int = 20, max_value: int = 100) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, max_value))


def _ip_is_non_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def _resolve_host_ips(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip = str(sockaddr[0])
        if ip not in seen:
            seen.add(ip)
            ips.append(ip)
    return ips


def _safe_resolved_ips(host: str) -> list[str]:
    try:
        resolved = _resolve_host_ips(host)
    except OSError as exc:
        raise ValueError(f"web discovery could not resolve host {host!r}") from exc
    for ip_text in resolved:
        try:
            resolved_ip = ipaddress.ip_address(ip_text)
        except ValueError:
            raise ValueError(f"web discovery got invalid address {ip_text!r} for host {host!r}") from None
        if _ip_is_non_public(resolved_ip):
            raise ValueError(
                f"web discovery refuses host {host!r}; resolves to non-public address {ip_text!r}"
            ) from None
    return resolved


def _check_resolved_ips(host: str) -> None:
    _safe_resolved_ips(host)


def _check_hostname(host: str, *, resolve_host: bool) -> list[str]:
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise ValueError(f"web discovery refuses non-public host {host!r}") from None
    if resolve_host:
        return _safe_resolved_ips(host)
    return []


def _check_ip_host(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if _ip_is_non_public(ip):
        raise ValueError(f"web discovery refuses non-public host {host!r}")
    return True


def _check_discovery_url(url: str, *, resolve_host: bool = False) -> None:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("web discovery only supports http(s) URLs")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("web discovery URL must include a host")
    if not _check_ip_host(host):
        _check_hostname(host, resolve_host=resolve_host)


def _checked_public_target(url: str) -> tuple[str, list[str]]:
    _check_discovery_url(url)
    host = (urlsplit(url).hostname or "").lower()
    if _check_ip_host(host):
        return host, [host]
    return host, _check_hostname(host, resolve_host=True)


def _redirect_target(response_url: str, response: httpx.Response) -> str | None:
    if response.status_code not in {301, 302, 303, 307, 308}:
        return None
    location = response.headers.get("location")
    if not location:
        response.raise_for_status()
    return urljoin(response_url, location)


def _check_content_type(response: httpx.Response, *, require_html: bool) -> None:
    content_type = response.headers.get("content-type", "")
    if require_html and "html" not in content_type.lower() and content_type:
        raise ValueError(f"web discovery expected HTML, got content-type {content_type!r}")


async def _read_limited_text(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        remaining = _MAX_HTML_BYTES - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if total >= _MAX_HTML_BYTES:
            break
    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")


async def _fetch_text(url: str, *, require_html: bool = False) -> tuple[str, str]:
    current_url = url
    pinned: dict[str, list[str]] = {}
    host, ips = _checked_public_target(current_url)
    pinned[host] = ips
    async with httpx.AsyncClient(
        transport=_PinnedDNSAsyncHTTPTransport(pinned),
        follow_redirects=False,
        timeout=httpx.Timeout(10.0, read=10.0),
        headers={"user-agent": "octowright-web-discovery/1.0"},
    ) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            async with client.stream("GET", current_url) as response:
                response_url = str(response.url)
                _check_discovery_url(response_url, resolve_host=True)
                redirect_url = _redirect_target(response_url, response)
                if redirect_url:
                    current_url = redirect_url
                    host, ips = _checked_public_target(current_url)
                    pinned[host] = ips
                    continue
                response.raise_for_status()
                _check_content_type(response, require_html=require_html)
                return response_url, await _read_limited_text(response)
    raise httpx.TooManyRedirects(f"Exceeded {_MAX_REDIRECTS} redirects while fetching {url!r}")


async def _fetch_html(url: str) -> tuple[str, str]:
    return await _fetch_text(url, require_html=True)


def _score_link(link: WebLinkCandidate, query: str) -> tuple[float, str]:
    fields = {
        "text": (link.get("text") or "").lower(),
        "label": (link.get("label") or "").lower(),
        "title": (link.get("title") or "").lower(),
        "href": (link.get("href") or "").lower(),
    }
    return weighted_text_score(fields, query, reduced_fields={"href"})


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def _link_key(link: WebLinkCandidate) -> str:
    return (link.get("href") or "").rstrip("/")


def _with_link_action(link: WebLinkCandidate) -> WebLinkCandidate:
    href = link.get("href")
    if not href:
        return link
    try:
        _check_discovery_url(href)
    except ValueError:
        return link
    out = cast("WebLinkCandidate", dict(link))
    browser_action: BrowserActionSuggestion = {
        "tool": "browser_launch",
        "args": {"url": href, "response_mode": "outline"},
    }
    out["action"] = browser_action
    out["actions"] = annotate_next_actions_for_profile(
        [
            {"tool": "web_page_outline", "args": {"url": href, "limit": 25}},
            {"tool": "browser_launch", "args": {"url": href, "response_mode": "outline"}},
        ]
    )
    return out


def _merge_link(target: list[WebLinkCandidate], seen: set[str], link: WebLinkCandidate) -> None:
    key = _link_key(link)
    if not key or key in seen:
        return
    try:
        _check_discovery_url(key)
    except ValueError:
        return
    seen.add(key)
    target.append(_with_link_action(link))


def _link_from_url(url: str, *, source: str) -> WebLinkCandidate:
    parts = urlsplit(url)
    text = parts.path.strip("/").replace("-", " ").replace("_", " ") or parts.netloc
    return _with_link_action(
        {
            "href": url,
            "text": text[:200],
            "tag": source,
        }
    )


def _web_next_actions(
    url: str, *, query: str | None = None, links: list[WebLinkCandidate] | None = None
) -> list[BrowserToolAction]:
    intent = query.strip() if query else "<intent>"
    actions: list[BrowserToolAction] = [
        {"tool": "web_find_links", "args": {"url": url, "query": intent, "limit": 8}},
    ]
    if query is None:
        actions.append({"tool": "web_site_links", "args": {"url": url, "query": intent, "limit": 12}})
    launch_url = None
    if links:
        launch_url = links[0].get("href")
    if launch_url:
        actions.append({"tool": "web_page_outline", "args": {"url": launch_url, "limit": 25}})
    actions.append(
        {
            "tool": "browser_launch",
            "args": {"url": launch_url or url, "response_mode": "outline"},
        }
    )
    return annotate_next_actions_for_profile(actions)


def _source_priority(link: WebLinkCandidate) -> float:
    source = link.get("tag")
    if source == "sitemap":
        return 50.0
    if source == "a":
        return 20.0
    if source in {"button", "input"}:
        return 10.0
    if source == "common":
        return -20.0
    return 0.0


def _default_site_score(link: WebLinkCandidate) -> tuple[float, str]:
    href = (link.get("href") or "").lower()
    text = (link.get("text") or "").lower()
    haystack = f"{href} {text}"
    matched = [term for term in _DEFAULT_SITE_TERMS if term in haystack]
    if not matched:
        return 0.0, "site candidate"
    return 45.0 + (5.0 * len(matched)), f"default site term: {matched[0]}"


def _robots_sitemaps(base_url: str, robots_text: str) -> list[str]:
    out: list[str] = []
    for line in robots_text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip().lower() == "sitemap" and value.strip():
            out.append(urljoin(base_url, value.strip()))
    return out[:10]


def _sitemap_links(sitemap_text: str) -> list[str]:
    try:
        root = ElementTree.fromstring(sitemap_text)
    except ElementTree.ParseError:
        return [
            html.unescape(match.strip())
            for match in re.findall(r"<(?:\w+:)?loc>\s*([^<]+?)\s*</(?:\w+:)?loc>", sitemap_text, flags=re.I)[
                :_MAX_LINKS
            ]
        ]

    urls: list[str] = []
    for element in root.iter():
        if element.tag.rpartition("}")[2].lower() != "loc" or not element.text:
            continue
        value = element.text.strip()
        if value:
            urls.append(value)
        if len(urls) >= _MAX_LINKS:
            break
    return urls


def _common_links(base_url: str) -> list[WebLinkCandidate]:
    origin = _origin(base_url)
    return [_link_from_url(urljoin(origin, path), source="common") for path in _COMMON_SITE_PATHS]


def _rank_links(links: list[WebLinkCandidate], query: str) -> list[WebLinkCandidate]:
    ranked: list[WebLinkCandidate] = []
    for link in links:
        score, reason = _score_link(link, query)
        if score <= 0:
            continue
        candidate = _with_link_action(link)
        candidate["score"] = score + _source_priority(link)
        candidate["reason"] = reason
        ranked.append(candidate)
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return ranked


def _rank_site_links(links: list[WebLinkCandidate], query: str) -> list[WebLinkCandidate]:
    ranked: list[WebLinkCandidate] = []
    for link in links:
        if query.strip():
            score, reason = _score_link(link, query)
        else:
            score, reason = _default_site_score(link)
        candidate = _with_link_action(link)
        candidate["score"] = score + _source_priority(link)
        candidate["reason"] = reason if score > 0 else "site candidate"
        ranked.append(candidate)
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return ranked


@mcp.tool(
    structured_output=False,
    description=(
        "Fetch a public HTML page and return a compact outline: title, canonical URL, "
        "headings, and top links. Use before launching a browser when you only need "
        "orientation or navigation candidates. Link candidates include a browser_launch "
        "action payload with response_mode='outline'."
    ),
)
async def web_page_outline(url: str, limit: int = 25) -> WebPageOutlineResult:
    cap = _clean_limit(limit)
    final_url, html = await _fetch_html(url)
    title, canonical, headings, links = parse_page(final_url, html)
    return {
        "url": final_url,
        "title": title,
        "canonical": canonical,
        "headings": headings[:_MAX_HEADINGS],
        "links": [_with_link_action(link) for link in links[:cap]],
        "total_links": len(links),
        "truncated": len(links) > cap,
        "next_actions": _web_next_actions(final_url),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Fetch a public HTML page and rank compact link candidates for an intent "
        "string. Use this before browser_snapshot/read_markdown when trying to find "
        "a docs, pricing, login, signup, or similar link. Link candidates include a "
        "browser_launch action payload with response_mode='outline'."
    ),
)
async def web_find_links(url: str, query: str, limit: int = 8) -> WebFindLinksResult:
    cap = _clean_limit(limit)
    final_url, html = await _fetch_html(url)
    title, canonical, headings, links = parse_page(final_url, html)
    ranked = _rank_links(links, query)
    return {
        "query": query,
        "url": final_url,
        "title": title,
        "canonical": canonical,
        "headings": headings[:_MAX_HEADINGS],
        "links": ranked[:cap],
        "total_links": len(ranked),
        "truncated": len(ranked) > cap,
        "next_actions": _web_next_actions(final_url, query=query, links=ranked),
    }


@mcp.tool(
    structured_output=False,
    description=(
        "Discover important site links using the home page, robots.txt sitemap hints, "
        "sitemap URLs, and common paths. Returns compact ranked candidates; use this "
        "before launching a browser when looking for docs, pricing, login, signup, "
        "support, API, or similar site-level destinations. Link candidates include a "
        "browser_launch action payload with response_mode='outline'."
    ),
)
async def web_site_links(url: str, query: str = "", limit: int = 12) -> WebSiteLinksResult:
    cap = _clean_limit(limit)
    final_url, html = await _fetch_html(url)
    title, canonical, headings, page_links = parse_page(final_url, html)
    links: list[WebLinkCandidate] = []
    seen: set[str] = set()
    sources = ["page"]
    for link in page_links:
        _merge_link(links, seen, link)

    origin = _origin(final_url)
    sitemap_urls: list[str] = []
    try:
        _, robots = await _fetch_text(urljoin(origin, "/robots.txt"))
    except (httpx.HTTPError, ValueError):
        robots = ""
    if robots:
        sources.append("robots")
        sitemap_urls.extend(_robots_sitemaps(origin, robots))
    if not sitemap_urls:
        sitemap_urls.append(urljoin(origin, "/sitemap.xml"))

    sitemap_added = False
    for sitemap_url in sitemap_urls[:5]:
        try:
            _, sitemap = await _fetch_text(sitemap_url)
        except (httpx.HTTPError, ValueError):
            continue
        for loc in _sitemap_links(sitemap):
            _merge_link(links, seen, _link_from_url(loc, source="sitemap"))
            sitemap_added = True
    if sitemap_added:
        sources.append("sitemap")

    for link in _common_links(final_url):
        _merge_link(links, seen, link)
    sources.append("common")

    ranked = _rank_site_links(links, query)
    return {
        "query": query,
        "url": final_url,
        "title": title,
        "canonical": canonical,
        "headings": headings[:_MAX_HEADINGS],
        "links": ranked[:cap],
        "total_links": len(ranked),
        "truncated": len(ranked) > cap,
        "sources": sources,
        "next_actions": _web_next_actions(final_url, query=query, links=ranked),
    }
