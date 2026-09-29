# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A fill whose target never appears fails within its own ``timeout_ms``.

Before the fill, the session reads the element's role (for the recording) and
asks whether it is a credential field (for redaction). The redaction probe
passed no timeout, so on a selector that never matches it waited Playwright's
30s default before the fill's own budget even started: ``timeout_ms=500``
took about 31s, with a credential or without. The whole step now shares one
deadline. Measured on all three engines.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool

pytestmark = pytest.mark.live_browser

SECRET = "hunter2-Budget-Check!"  # pragma: allowlist secret -- a fixture, never a real credential
BUDGET_MS = 500
#: Launch-free slack for driver round trips on a loaded host; far below the
#: 30s the unbounded probe cost.
ALLOWED_S = 6.0


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def session(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url="about:blank")
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


async def _fill(session: Any, kind: str) -> None:
    if kind == "fill":
        await session.fill("#no-such-field", SECRET, timeout_ms=BUDGET_MS)
    else:
        await session.fill_by(SECRET, label="No such field", timeout_ms=BUDGET_MS)


@pytest.mark.parametrize("kind", ["fill", "fill_by"])
async def test_a_fill_whose_target_never_appears_fails_within_its_timeout(session: Any, kind: str) -> None:
    started = time.monotonic()
    with pytest.raises(Exception, match="waiting for"):
        await _fill(session, kind)
    assert time.monotonic() - started < ALLOWED_S


@pytest.mark.parametrize("kind", ["fill", "fill_by"])
async def test_a_credential_fill_whose_target_never_appears_fails_within_its_timeout(session: Any, kind: str) -> None:
    from octowright.session.fill_origin import fill_origin_check

    started = time.monotonic()
    with fill_origin_check(lambda _url: None), pytest.raises(Exception, match="waiting for"):
        await _fill(session, kind)
    assert time.monotonic() - started < ALLOWED_S
