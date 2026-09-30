# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A fill whose target never appears fails within its own ``timeout_ms``.

Before the fill, the session reads the element's role (for the recording) and
asks whether it is a credential field (for redaction). The redaction probe
passed no timeout, so on a selector that never matches it waited Playwright's
30s default before the fill's own budget even started: ``timeout_ms=500``
took about 31s, with a credential or without. Then the metadata read spent
the whole budget and the action got a fresh one, so a step took about twice
its timeout (hidden by a 6s allowance for a 500ms budget). The step now waits
for its element once, under the whole budget, and fails with Playwright's
``Locator.wait_for: Timeout <budget>ms exceeded ... waiting for ...``;
metadata, probe and action share what is left. Measured on all three engines.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS

pytestmark = pytest.mark.live_browser

SECRET = "hunter2-Budget-Check!"  # pragma: allowlist secret -- a fixture, never a real credential
#: Over the budget a missing target may take: driver round trips on a loaded
#: host. Measured at 20-40ms on all three engines (a 3000ms step took
#: 3.02-3.04s, the 15000ms type 15.02-15.04s); the doubling this guards
#: against cost a whole second budget, which even the 500ms case exceeds.
SLACK_S = 0.4


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


async def _step(session: Any, kind: str, budget_ms: int) -> None:
    if kind == "fill":
        await session.fill("#no-such-field", SECRET, timeout_ms=budget_ms)
    elif kind == "fill_by":
        await session.fill_by(SECRET, label="No such field", timeout_ms=budget_ms)
    else:  # a type takes no timeout_ms: its budget is the action timeout
        await session.type_text("#no-such-field", SECRET, None)


def _budget(kind: str, budget_ms: int) -> int:
    return DEFAULT_ACTION_TIMEOUT_MS if kind == "type" else budget_ms


_CASES = [("fill", 500), ("fill", 3000), ("fill_by", 500), ("fill_by", 3000), ("type", 0)]


@pytest.mark.parametrize(("kind", "budget_ms"), _CASES)
async def test_a_step_whose_target_never_appears_fails_within_its_timeout(
    session: Any, kind: str, budget_ms: int
) -> None:
    budget = _budget(kind, budget_ms)
    started = time.monotonic()
    with pytest.raises(Exception, match=f"Timeout {budget}ms exceeded[\\s\\S]*waiting for"):
        await _step(session, kind, budget_ms)
    elapsed = time.monotonic() - started
    assert elapsed < budget / 1000 + SLACK_S, elapsed


@pytest.mark.parametrize(("kind", "budget_ms"), _CASES)
async def test_a_credential_step_whose_target_never_appears_fails_within_its_timeout(
    session: Any, kind: str, budget_ms: int
) -> None:
    from octowright.session.fill_origin import fill_origin_check

    budget = _budget(kind, budget_ms)
    started = time.monotonic()
    with fill_origin_check(lambda _url: None), pytest.raises(Exception, match=f"Timeout {budget}ms exceeded"):
        await _step(session, kind, budget_ms)
    elapsed = time.monotonic() - started
    assert elapsed < budget / 1000 + SLACK_S, elapsed
