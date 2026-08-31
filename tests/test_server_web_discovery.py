# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.server import web as _web

HTML = """
<!doctype html>
<html>
  <head>
    <title>Example Product</title>
    <link rel="canonical" href="https://example.com/home">
  </head>
  <body>
    <nav>
      <a href="/docs">Documentation</a>
      <a href="/pricing" aria-label="Plans and pricing">Pricing</a>
      <a href="https://blog.example.com/">Blog</a>
    </nav>
    <main>
      <h1>Build faster</h1>
      <h2>Developer tools</h2>
      <form action="/signup"><button>Start trial</button></form>
    </main>
  </body>
</html>
"""


@pytest.mark.anyio
async def test_web_page_outline_returns_compact_page_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str) -> tuple[str, str]:
        return "https://example.com/", HTML

    monkeypatch.setattr(_web, "_fetch_html", fake_fetch)

    out = await _web.web_page_outline("https://example.com/", limit=4)

    assert out["url"] == "https://example.com/"
    assert out["title"] == "Example Product"
    assert out["canonical"] == "https://example.com/home"
    assert out["headings"] == ["Build faster", "Developer tools"]
    assert [link["href"] for link in out["links"]] == [
        "https://example.com/docs",
        "https://example.com/pricing",
        "https://blog.example.com/",
        "https://example.com/signup",
    ]
    assert out["links"][0]["action"] == {
        "tool": "browser_launch",
        "args": {"url": "https://example.com/docs", "response_mode": "outline"},
    }
    assert out["links"][0]["actions"] == [
        {"tool": "web_page_outline", "args": {"url": "https://example.com/docs", "limit": 25}},
        {"tool": "browser_launch", "args": {"url": "https://example.com/docs", "response_mode": "outline"}},
    ]
    assert out["next_actions"] == [
        {"tool": "web_find_links", "args": {"url": "https://example.com/", "query": "<intent>", "limit": 8}},
        {"tool": "web_site_links", "args": {"url": "https://example.com/", "query": "<intent>", "limit": 12}},
        {"tool": "browser_launch", "args": {"url": "https://example.com/", "response_mode": "outline"}},
    ]
    assert out["truncated"] is False


@pytest.mark.anyio
async def test_web_find_links_scores_query_without_page_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str) -> tuple[str, str]:
        return "https://example.com/", HTML

    monkeypatch.setattr(_web, "_fetch_html", fake_fetch)

    out = await _web.web_find_links("https://example.com/", "pricing", limit=1)

    assert out["query"] == "pricing"
    assert out["links"][0]["href"] == "https://example.com/pricing"
    assert out["links"][0]["text"] == "Pricing"
    assert out["links"][0]["action"] == {
        "tool": "browser_launch",
        "args": {"url": "https://example.com/pricing", "response_mode": "outline"},
    }
    assert out["next_actions"] == [
        {"tool": "web_find_links", "args": {"url": "https://example.com/", "query": "pricing", "limit": 8}},
        {"tool": "web_page_outline", "args": {"url": "https://example.com/pricing", "limit": 25}},
        {"tool": "browser_launch", "args": {"url": "https://example.com/pricing", "response_mode": "outline"}},
    ]
    assert out["links"][0]["score"] > 0
    assert "label contains query" in out["links"][0]["reason"]


@pytest.mark.anyio
async def test_web_page_outline_rejects_non_public_literal_hosts() -> None:
    with pytest.raises(ValueError, match="non-public host"):
        await _web.web_page_outline("http://169.254.169.254/latest/meta-data/")


def test_web_link_candidate_omits_actions_for_unsafe_internal_urls() -> None:
    out = _web._with_link_action({"href": "http://127.0.0.1/private", "text": "Private"})

    assert out == {"href": "http://127.0.0.1/private", "text": "Private"}


def test_check_discovery_url_rejects_hostname_resolving_to_private_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_web, "_resolve_host_ips", lambda host: ["10.0.0.7"])

    with pytest.raises(ValueError, match="resolves to non-public address"):
        _web._check_discovery_url("https://internal.example/", resolve_host=True)


def test_check_discovery_url_raises_on_resolution_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve(host: str) -> list[str]:
        raise OSError("dns unavailable")

    monkeypatch.setattr(_web, "_resolve_host_ips", fake_resolve)

    with pytest.raises(ValueError, match="could not resolve host"):
        _web._check_discovery_url("https://missing.example/", resolve_host=True)


@pytest.mark.anyio
async def test_fetch_text_rejects_redirects_to_non_public_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    requested_urls: list[str] = []
    monkeypatch.setattr(_web, "_resolve_host_ips", lambda host: ["93.184.216.34"])

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def stream(self, method: str, url: str) -> object:
            requested_urls.append(url)
            return _RedirectResponse(url)

    class _RedirectResponse:
        def __init__(self, url: str) -> None:
            self.url = url
            self.status_code = 302
            self.headers = {"location": "http://127.0.0.1/secret"}
            self.encoding = "utf-8"

        def raise_for_status(self) -> None:
            pass

        async def __aenter__(self) -> _RedirectResponse:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def aiter_bytes(self) -> object:
            raise AssertionError("redirect response body should not be read")

    monkeypatch.setattr(_web.httpx2, "AsyncClient", FakeClient)

    with pytest.raises(ValueError, match="non-public host"):
        await _web._fetch_text("https://example.com/")
    assert requested_urls == ["https://example.com/"]


@pytest.mark.anyio
async def test_fetch_text_stops_reading_after_byte_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    chunks_read = 0
    monkeypatch.setattr(_web, "_resolve_host_ips", lambda host: ["93.184.216.34"])

    class FakeClient:
        def __init__(self, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def stream(self, method: str, url: str) -> object:
            return _StreamResponse()

    class _StreamResponse:
        url = "https://example.com/"
        status_code = 200
        headers = {"content-type": "text/html"}
        encoding = "utf-8"

        async def __aenter__(self) -> _StreamResponse:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

        async def aiter_bytes(self) -> object:
            nonlocal chunks_read
            chunk = b"x" * (_web._MAX_HTML_BYTES // 2)
            for _ in range(5):
                chunks_read += 1
                yield chunk

    monkeypatch.setattr(_web.httpx2, "AsyncClient", FakeClient)

    _, text = await _web._fetch_text("https://example.com/")

    assert len(text) == _web._MAX_HTML_BYTES
    assert chunks_read == 2


@pytest.mark.anyio
async def test_fetch_text_pins_connection_to_checked_dns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    connected_hosts: list[str] = []
    monkeypatch.setattr(_web, "_resolve_host_ips", lambda host: ["93.184.216.34"])

    class FakeBackend:
        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: object | None = None,
        ) -> object:
            del port, timeout, local_address, socket_options
            connected_hosts.append(host)
            raise RuntimeError("stop after backend host check")

        async def sleep(self, seconds: float) -> None:
            del seconds

    monkeypatch.setattr(_web, "AutoBackend", lambda: FakeBackend())

    with pytest.raises(RuntimeError, match="stop after backend host check"):
        await _web._fetch_text("https://example.com/")

    assert connected_hosts == ["93.184.216.34"]


@pytest.mark.anyio
async def test_web_site_links_combines_page_robots_sitemap_and_common_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch_text(url: str, *, require_html: bool = False) -> tuple[str, str]:
        responses = {
            "https://example.com/": ("https://example.com/", HTML),
            "https://example.com/robots.txt": (
                "https://example.com/robots.txt",
                "User-agent: *\nSitemap: https://example.com/sitemap.xml\n",
            ),
            "https://example.com/sitemap.xml": (
                "https://example.com/sitemap.xml",
                """
                <urlset>
                  <url><loc>https://example.com/docs/api</loc></url>
                  <url><loc>https://example.com/account/login</loc></url>
                </urlset>
                """,
            ),
        }
        if url not in responses:
            raise RuntimeError(f"unexpected fetch: {url}")
        return responses[url]

    monkeypatch.setattr(_web, "_fetch_text", fake_fetch_text)

    out = await _web.web_site_links("https://example.com/", "login", limit=20)

    hrefs = [link["href"] for link in out["links"]]
    assert hrefs[0] == "https://example.com/account/login"
    assert out["links"][0]["action"] == {
        "tool": "browser_launch",
        "args": {"url": "https://example.com/account/login", "response_mode": "outline"},
    }
    assert out["next_actions"] == [
        {"tool": "web_find_links", "args": {"url": "https://example.com/", "query": "login", "limit": 8}},
        {"tool": "web_page_outline", "args": {"url": "https://example.com/account/login", "limit": 25}},
        {"tool": "browser_launch", "args": {"url": "https://example.com/account/login", "response_mode": "outline"}},
    ]
    assert "https://example.com/pricing" in hrefs
    assert "https://example.com/login" in hrefs
    assert out["sources"] == ["page", "robots", "sitemap", "common"]
    assert out["truncated"] is False


@pytest.mark.anyio
async def test_web_site_links_ignores_unsafe_sitemap_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch_text(url: str, *, require_html: bool = False) -> tuple[str, str]:
        responses = {
            "https://example.com/": ("https://example.com/", HTML),
            "https://example.com/robots.txt": (
                "https://example.com/robots.txt",
                "Sitemap: https://example.com/sitemap.xml\n",
            ),
            "https://example.com/sitemap.xml": (
                "https://example.com/sitemap.xml",
                """
                <urlset>
                  <url><loc>http://127.0.0.1/private</loc></url>
                  <url><loc>ftp://example.com/archive</loc></url>
                  <url><loc>https://example.com/docs/api</loc></url>
                </urlset>
                """,
            ),
        }
        return responses[url]

    monkeypatch.setattr(_web, "_fetch_text", fake_fetch_text)

    out = await _web.web_site_links("https://example.com/", "api", limit=20)

    hrefs = {link["href"] for link in out["links"]}
    assert "https://example.com/docs/api" in hrefs
    assert "http://127.0.0.1/private" not in hrefs
    assert "ftp://example.com/archive" not in hrefs


@pytest.mark.anyio
async def test_web_site_links_empty_query_still_surfaces_sitemap_and_common_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    many_nav_links = "".join(f'<a href="/nav-{index}">Nav {index}</a>' for index in range(20))
    html = f"<html><head><title>Example</title></head><body>{many_nav_links}</body></html>"

    async def fake_fetch_text(url: str, *, require_html: bool = False) -> tuple[str, str]:
        responses = {
            "https://example.com/": ("https://example.com/", html),
            "https://example.com/robots.txt": ("https://example.com/robots.txt", ""),
            "https://example.com/sitemap.xml": (
                "https://example.com/sitemap.xml",
                "<urlset><url><loc>https://example.com/docs/api</loc></url></urlset>",
            ),
        }
        return responses[url]

    monkeypatch.setattr(_web, "_fetch_text", fake_fetch_text)

    out = await _web.web_site_links("https://example.com/", limit=12)

    hrefs = {link["href"] for link in out["links"]}
    assert "https://example.com/docs/api" in hrefs
    assert "https://example.com/pricing" in hrefs
    assert out["next_actions"][0] == {
        "tool": "web_find_links",
        "args": {"url": "https://example.com/", "query": "<intent>", "limit": 8},
    }


def test_top_level_server_exports_web_discovery_tools() -> None:
    from octowright import server

    assert hasattr(server, "web_page_outline")
    assert hasattr(server, "web_find_links")
    assert hasattr(server, "web_site_links")


def test_web_link_candidate_actions_are_profile_aware(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_PROFILE", "advanced")

    out = _web._with_link_action({"href": "https://example.com/docs", "text": "Docs"})

    assert out["actions"] == [
        {
            "tool": "web_page_outline",
            "args": {"url": "https://example.com/docs", "limit": 25},
            "available": False,
            "requires_profile": "core",
            "available_profiles": ["core"],
        },
        {
            "tool": "browser_launch",
            "args": {"url": "https://example.com/docs", "response_mode": "outline"},
            "available": False,
            "requires_profile": "core",
            "available_profiles": ["core"],
        },
    ]


def test_sitemap_links_decode_xml_entities() -> None:
    sitemap = """
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/docs?q=a&amp;b=c</loc></url>
    </urlset>
    """

    assert _web._sitemap_links(sitemap) == ["https://example.com/docs?q=a&b=c"]
