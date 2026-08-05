# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A persona's ``default_url`` becomes the context's Playwright ``base_url``.

A macro is the BEHAVIOUR; the persona is the WHERE. Octowright already splits
them that way -- ``resolve`` scores a persona against a URL on its
``default_url`` host and ``app.hosts``, and ``scenarios`` falls back to
``default_url`` for a participant with no URL of its own -- but contexts did not
honour it. So a macro that wanted to run against more than one deployment had to
bake an origin into every ``navigate``, and replaying the same behaviour
elsewhere meant editing the macro instead of choosing a different persona.

With ``base_url`` set, ``navigate("/orders")`` resolves per persona and
``expect_url`` takes the same relative form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from octowright import personas
from octowright.browser_pool import launch_helpers


def _write_persona(root: Path, name: str, doc: str) -> None:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "profile.yaml").write_text(doc, encoding="utf-8")


def test_persona_default_url_becomes_base_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write_persona(tmp_path, "buyer", "name: buyer\ndefault_url: https://proving.account.undef.games/\n")

    assert launch_helpers.persona_base_url_kwargs("buyer") == {"base_url": "https://proving.account.undef.games/"}


def test_a_persona_without_a_default_url_sets_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Absent is absent: Playwright must not receive base_url=None."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write_persona(tmp_path, "drifter", "name: drifter\n")

    assert launch_helpers.persona_base_url_kwargs("drifter") == {}


def test_a_profile_that_is_not_a_saved_persona_is_not_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A profile name need not be a saved persona, and launching must not care."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)

    assert launch_helpers.persona_base_url_kwargs("never-created") == {}
    assert launch_helpers.persona_base_url_kwargs(None) == {}


def test_a_malformed_persona_file_does_not_break_launching(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """load_persona raises ValueError on a bad document; a launch should still open."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write_persona(tmp_path, "broken", "name: broken\ndefault_url: [not, a, string]\n")

    assert launch_helpers.persona_base_url_kwargs("broken") == {}


class _FakeContext:
    def __init__(self) -> None:
        self.pages: list[Any] = []

    async def new_page(self) -> object:
        page = object()
        self.pages.append(page)
        return page


class _FakeBrowserType:
    """Records the kwargs each context-creation path was given."""

    def __init__(self) -> None:
        self.persistent_kwargs: dict[str, Any] | None = None
        self.context_kwargs: dict[str, Any] | None = None

    async def launch_persistent_context(self, user_data_dir: str, **kwargs: Any) -> _FakeContext:
        self.persistent_kwargs = kwargs
        return _FakeContext()

    async def launch(self, **_: Any) -> _FakeBrowser:
        return _FakeBrowser(self)


class _FakeBrowser:
    def __init__(self, owner: _FakeBrowserType) -> None:
        self._owner = owner

    async def new_context(self, **kwargs: Any) -> _FakeContext:
        self._owner.context_kwargs = kwargs
        return _FakeContext()


async def test_the_persistent_profile_path_receives_base_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The persona path is the one that matters: a named profile IS a persona."""
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)
    _write_persona(tmp_path, "operator", "name: operator\ndefault_url: https://proving.admin.undef.games/\n")

    browser_type = _FakeBrowserType()
    await launch_helpers._open_browser_context(
        browser_type=browser_type,
        kind="chromium",
        profile="operator",
        session_user_data_dir=None,
        headless=True,
        viewport_kwargs={},
        ctx_video_kwargs={},
        ctx_har_kwargs={},
        launch_kwargs={},
    )

    assert browser_type.persistent_kwargs is not None
    assert browser_type.persistent_kwargs["base_url"] == "https://proving.admin.undef.games/"


async def test_the_ephemeral_path_carries_no_base_url_without_a_persona(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(personas, "PROFILES_DIR", tmp_path)

    browser_type = _FakeBrowserType()
    await launch_helpers._open_browser_context(
        browser_type=browser_type,
        kind="chromium",
        profile=None,
        session_user_data_dir=None,
        headless=True,
        viewport_kwargs={},
        ctx_video_kwargs={},
        ctx_har_kwargs={},
        launch_kwargs={},
    )

    assert browser_type.context_kwargs is not None
    assert "base_url" not in browser_type.context_kwargs
