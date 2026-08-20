# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``GET /api/health`` reports the version this PROCESS is running.

It used to read package metadata off disk on every request, so it reported
whatever was installed rather than what was executing -- meaning the one
question an operator asks it after a deploy ("is the daemon on the new version
yet?") was the one it could never answer correctly.
"""

from __future__ import annotations

import pytest

from octowright.http.routes import health
from octowright.version import VERSION


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _payload() -> dict:
    import json

    response = await health.health_endpoint(None)  # type: ignore[arg-type]
    return json.loads(bytes(response.body))


@pytest.mark.anyio
async def test_reports_the_running_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even when a newer package is installed on disk, which is exactly what a
    `uv sync` / `pip install -U` leaves behind before a restart."""
    monkeypatch.setattr(health, "_installed_version", lambda: "99.0.0")

    payload = await _payload()

    assert payload["ok"] is True
    assert payload["version"] == VERSION


@pytest.mark.anyio
async def test_a_pending_upgrade_is_surfaced_separately(monkeypatch: pytest.MonkeyPatch) -> None:
    """The original intent -- notice an upgrade -- kept, but honestly named:
    this is the "restart to pick it up" signal, not the running version."""
    monkeypatch.setattr(health, "_installed_version", lambda: "99.0.0")

    payload = await _payload()

    assert payload["installed_version"] == "99.0.0"


@pytest.mark.anyio
async def test_the_ordinary_response_shape_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """When disk and process agree -- the normal case -- no extra field appears,
    so existing consumers of {ok, version} see exactly what they always did."""
    monkeypatch.setattr(health, "_installed_version", lambda: VERSION)

    assert await _payload() == {"ok": True, "version": VERSION}


@pytest.mark.anyio
async def test_unreadable_metadata_never_breaks_the_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liveness probes depend on this endpoint answering."""
    monkeypatch.setattr(health, "_installed_version", lambda: None)

    assert await _payload() == {"ok": True, "version": VERSION}


# ─── the same class on the /new-tab status strip ─────────────────────────────


class TestNewTabIdentity:
    """The landing page's status strip had the same bug in two forms."""

    def test_the_strip_shows_the_running_version(self) -> None:
        """It read dist-info, so after an upgrade it advertised the newly
        INSTALLED version while the daemon went on running old code."""
        from octowright.http.routes import new_tab

        assert new_tab._version() == VERSION

    def test_the_commit_is_resolved_against_this_package_not_the_daemon_cwd(self) -> None:
        """It shelled out to git in the daemon's *current working directory* --
        wherever the process happened to be launched, which need not be this
        package at all -- and did so at request time, so switching branches
        under a running daemon changed the commit the strip claimed while the
        loaded modules did not change."""
        import subprocess
        from pathlib import Path

        from octowright.http.routes import new_tab

        new_tab._commit.cache_clear()
        seen: dict[str, object] = {}
        real = subprocess.run

        def _spy(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            seen.update(kwargs)
            return real(*args, **kwargs)  # type: ignore[arg-type]

        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(subprocess, "run", _spy)
                new_tab._commit()
        finally:
            new_tab._commit.cache_clear()

        assert Path(str(seen["cwd"])).resolve() == Path(new_tab.__file__).resolve().parent

    def test_an_unavailable_repository_never_breaks_the_page(self) -> None:
        """An installed (non-editable) package has no repository at all."""
        import subprocess

        from octowright.http.routes import new_tab

        new_tab._commit.cache_clear()
        try:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("git")))
                assert new_tab._commit() == "?"
        finally:
            new_tab._commit.cache_clear()
