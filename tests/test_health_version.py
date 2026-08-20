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
