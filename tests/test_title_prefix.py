# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Title-prefix injection regression test.

Originally the prefix-injection JS doubled the prefix on pages whose titles
got trimmed by the browser (httpbin's plain-JSON pages, empty-title docs).
The fix is to compare against PREFIX-without-trailing-space so the re-check
recognises an already-prefixed title.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_title_does_not_double_when_browser_strips_trailing_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Page with no <title> + injected prefix should yield a single prefix, not double."""
    pytest.importorskip("playwright")
    from octowright import defaults as _defaults
    from octowright import personas as _personas
    from octowright import pool as _pool
    from octowright import profiles as _profiles
    from octowright.pool import BrowserPool

    rec = tmp_path / "rec"
    profiles = tmp_path / "profiles"
    rec.mkdir()
    profiles.mkdir()
    monkeypatch.setattr(_defaults, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_pool, "RECORDINGS_DIR", rec)
    monkeypatch.setattr(_defaults, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_personas, "PROFILES_DIR", profiles)
    monkeypatch.setattr(_profiles, "PROFILES_DIR", profiles)

    pool = BrowserPool()
    try:
        result = await pool.launch(
            kind="chromium",
            url="data:text/html,<html><body><h1>no title here</h1></body></html>",
            headed=False,
            label="acct",
            viewport_w=400,
            viewport_h=300,
            ephemeral=True,
        )
        session = pool.get(result["instance_id"])
        title = await session.page.title()
        # Should appear EXACTLY once — not "(emoji emoji) [acct] (emoji emoji) [acct]".
        assert title.count("[acct]") == 1, f"prefix doubled: {title!r}"
    finally:
        await pool.shutdown()
