# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that ``set_input_files`` honours the active frame.

It called ``self.page.set_input_files`` whatever frame was selected, so after
``switch_frame`` the selector was looked up in the top document: an input that
exists only in the iframe timed out, and one that exists in both documents got
the file in the wrong one. ``upload_files`` and every element action already
resolve through ``_target()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from octowright import defaults
from octowright.browser_pool import BrowserPool
from tests.test_engine_matrix_live import _maybe_skip_live_engine

pytestmark = [pytest.mark.live_browser, pytest.mark.asyncio]

_FRAME = '<input id="f" type="file">'
# The top document has an input of the same id: the file must land in the frame's.
_PAGE = f"""<!doctype html><body><input id="f" type="file"><iframe name="inner" srcdoc='{_FRAME}'></iframe></body>"""


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def session(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path / "recordings")
    try:
        try:
            inst = await pool.launch(
                kind=request.param, headed=False, url=f"data:text/html;charset=utf-8,{quote(_PAGE, safe='')}"
            )
        except Exception as exc:
            _maybe_skip_live_engine(exc)
            raise
        yield pool.get(inst["instance_id"])
    finally:
        await pool.shutdown()


async def test_the_file_lands_in_the_selected_frame(
    session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    upload = tmp_path / "in-frame.txt"
    upload.write_text("x", encoding="utf-8")
    frame = session.page.frame(name="inner")
    await frame.wait_for_selector("#f", state="attached")

    await session.switch_frame(name="inner")
    await session.set_input_files("#f", [str(upload)])

    count = "() => document.querySelector('#f').files.length"
    assert await frame.evaluate(count) == 1
    assert await session.page.evaluate(count) == 0
