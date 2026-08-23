# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

import pytest

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
    capabilities_of,
)
from octowright.scenario_adapters import BrowserScenarioAdapter


class _Page:
    url = "https://shop.test/orders"

    async def wait_for_url(self, url: str, timeout: int | None = None) -> None:
        self.waited = url


class _Operation:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Session:
    def __init__(self) -> None:
        self.page = _Page()
        self.dialog_policy: str | None = None
        self.mocked: list[dict[str, Any]] = []
        self.waited_for: dict[str, Any] | None = None

    def operation(self, name: str) -> _Operation:
        return _Operation()

    async def wait_for(self, *, selector=None, text=None, timeout_ms=None) -> None:
        self.waited_for = {"selector": selector, "text": text, "timeout_ms": timeout_ms}

    async def set_dialog_policy(self, policy: str) -> None:
        self.dialog_policy = policy

    async def mock_route(self, pattern, *, status=200, body=None, content_type=None, headers=None) -> None:
        self.mocked.append({"pattern": pattern, "status": status, "body": body, "content_type": content_type})


class _Pool:
    def __init__(self) -> None:
        self.sessions = {"br0wser01": _Session()}

    def get(self, instance_id: str) -> _Session:
        return self.sessions[instance_id]


@pytest.fixture
def adapter():
    pool = _Pool()
    return BrowserScenarioAdapter(pool), pool


def test_browser_adapter_satisfies_every_capability_protocol(adapter):
    """A browser supports all four, which is what makes it the reference shape."""
    ad, _ = adapter
    assert isinstance(ad, ScenarioAdapter)
    assert isinstance(ad, SupportsMacros)
    assert isinstance(ad, SupportsSync)
    assert isinstance(ad, SupportsDialogPolicy)
    assert isinstance(ad, SupportsMockRoutes)
    assert capabilities_of(ad) == {"macros", "sync", "dialog_policy", "mock_routes"}


async def test_wait_for_sync_by_selector_uses_the_session(adapter):
    ad, pool = adapter
    await ad.wait_for_sync("br0wser01", selector="#done", text=None, url=None, timeout_ms=500)
    assert pool.sessions["br0wser01"].waited_for == {"selector": "#done", "text": None, "timeout_ms": 500}


async def test_wait_for_sync_by_url_skips_the_wait_when_already_there(adapter):
    ad, pool = adapter
    # _Page.url already matches, so page.wait_for_url must not be called.
    await ad.wait_for_sync("br0wser01", selector=None, text=None, url=r"shop\.test/orders", timeout_ms=None)
    assert not hasattr(pool.sessions["br0wser01"].page, "waited")


async def test_wait_for_sync_by_url_waits_when_it_does_not_match(adapter):
    ad, pool = adapter
    await ad.wait_for_sync("br0wser01", selector=None, text=None, url=r"checkout", timeout_ms=1000)
    assert pool.sessions["br0wser01"].page.waited == "checkout"


async def test_set_dialog_policy_reaches_the_session(adapter):
    ad, pool = adapter
    await ad.set_dialog_policy("br0wser01", "accept")
    assert pool.sessions["br0wser01"].dialog_policy == "accept"


async def test_install_mock_routes_applies_every_route_with_its_defaults(adapter):
    ad, pool = adapter
    await ad.install_mock_routes(
        "br0wser01",
        [{"pattern": "**/api/x", "body": "{}"}, {"pattern": "**/api/y", "status": 404}],
    )
    mocked = pool.sessions["br0wser01"].mocked
    assert [m["pattern"] for m in mocked] == ["**/api/x", "**/api/y"]
    assert mocked[0]["status"] == 200, "status defaults to 200"
    assert mocked[0]["content_type"] == "application/json", "content_type defaults to JSON"
    assert mocked[1]["status"] == 404


async def test_run_macro_dispatches_to_the_macro_runner(adapter, monkeypatch):
    ad, pool = adapter
    seen: dict[str, Any] = {}

    async def _fake_run_macro(*, session, name, args):
        seen.update({"session": session, "name": name, "args": args})

    import octowright.macros as macros_mod

    monkeypatch.setattr(macros_mod, "run_macro", _fake_run_macro)
    await ad.run_macro("br0wser01", name="login", args={"user": "tanuki"})
    assert seen["name"] == "login"
    assert seen["args"] == {"user": "tanuki"}
    assert seen["session"] is pool.sessions["br0wser01"]


def test_resolve_participant_matches_the_existing_browser_resolver(adapter):
    """The floor method delegates -- it must not fork the resolution rules.

    Pinned against ``resolve_launch_kwargs`` itself rather than against a
    hand-written dict, because the value of delegating is precisely that the
    two cannot drift. A hand-written expectation would pass while the adapter
    quietly dropped the persona ``default_url`` fallback.
    """
    from octowright.scenarios import Participant, resolve_launch_kwargs

    ad, _ = adapter
    spec = Participant(persona="tanuki-tim", kind="chromium", role="player", url="https://shop.test")
    assert ad.resolve_participant(spec, None) == resolve_launch_kwargs(spec)
