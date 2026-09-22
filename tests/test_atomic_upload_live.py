# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live proof that atomic upload leaves no native chooser behind.

The upload trigger opens a native file chooser from JavaScript.  Arming the
chooser listener and clicking the trigger in one session operation must select
the staged file, preserve subsequent page interaction, close cleanly, and
record one semantic upload action rather than a click-plus-input sequence.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest

from octowright import defaults, session_manifest
from octowright.browser_pool import BrowserPool
from tests.test_engine_matrix_live import _maybe_skip_live_engine


@pytest.mark.asyncio
@pytest.mark.live_browser
async def test_atomic_upload_selects_file_and_closes_cleanly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("playwright")
    html = """<!doctype html>
<html lang="en">
<body>
  <input id="file-input" type="file" hidden>
  <button id="upload" type="button">Upload files</button>
  <output id="selected"></output>
  <button id="after" type="button">after</button>
  <script>
    const input = document.querySelector('#file-input');
    document.querySelector('#upload').addEventListener('click', () => input.click());
    input.addEventListener('change', () => {
      document.querySelector('#selected').textContent = input.files[0]?.name ?? '';
    });
    document.querySelector('#after').addEventListener('click', event => {
      event.currentTarget.textContent = 'worked';
    });
  </script>
</body>
</html>
"""
    page_url = f"data:text/html;charset=utf-8,{quote(html, safe='')}"
    report = tmp_path / "report.txt"
    report.write_text("atomic upload", encoding="utf-8")
    manifest_path = tmp_path / "session-manifest.json"
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    monkeypatch.setattr(defaults, "SESSION_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(session_manifest, "SESSION_MANIFEST_PATH", manifest_path)

    pool = BrowserPool(recordings_dir=tmp_path / "recordings")
    try:
        try:
            launched = await pool.launch(kind="chromium", headed=False, url=page_url)
        except Exception as exc:
            _maybe_skip_live_engine(exc)
            raise

        instance_id = launched["instance_id"]
        assert instance_id in session_manifest.read_manifest(manifest_path)["sessions"]
        session = pool.get(instance_id)
        await session.upload_files(
            paths=[str(report)],
            role="button",
            role_name="Upload files",
            role_exact=True,
        )
        assert await session.page.locator("#selected").text_content() == "report.txt"

        await session.click("#after")
        assert await session.page.locator("#after").text_content() == "worked"

        closed = await pool.close(instance_id)
        assert closed["closed"] is True
        assert instance_id not in session_manifest.read_manifest(manifest_path)["sessions"]

        entries = [json.loads(line) for line in Path(launched["log_path"]).read_text(encoding="utf-8").splitlines()]
        upload_actions = [entry for entry in entries if entry.get("action") == "upload_files"]
        assert len(upload_actions) == 1
        assert upload_actions[0]["role"] == "button"
        assert upload_actions[0]["role_name"] == "Upload files"
        assert upload_actions[0]["role_exact"] is True

        forbidden = [
            entry
            for entry in entries
            if entry.get("action") in {"click_by", "set_input_files"}
            or (entry.get("action") == "click" and entry.get("selector") != "#after")
        ]
        assert forbidden == []
    finally:
        await pool.shutdown()
