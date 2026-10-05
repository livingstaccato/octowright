# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live: a failed run holding a credential writes scrubbed HTML and no screenshot (#248).

Real headless Chromium. The page echoes what is typed into its password field
into the DOM and the console, and the macro then fails, so the producer's
HTML file and console tail would both hold the password if they were not
scrubbed -- and its screenshot would show it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octowright.macros import execution

PW_FIXTURE = "Fixture-Not-A-Real-Secret-Diag"  # pragma: allowlist secret
_PAGE = (
    "<input type=password id=pw><p id=echo></p><script>"
    "document.getElementById('pw').addEventListener('input', e => {"
    "document.getElementById('echo').textContent = 'typed ' + e.target.value;"
    "console.log('typed ' + e.target.value); });</script>"
)
_MACRO = {
    "name": "m",
    "parameters": ["password"],
    "actions": [
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
        {"action": "wait_for", "selector": "#never", "timeout_ms": 300},
    ],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.live_browser
@pytest.mark.anyio
async def test_live_failure_html_is_scrubbed_and_no_screenshot_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.browser_pool.lifecycle import shutdown_pool
    from octowright.browser_pool.pool import BrowserPool

    # The page is about:blank's own content; nothing here is an offsite login.
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    monkeypatch.setattr(execution, "load_macro", lambda _name: _MACRO)
    pool = BrowserPool(recordings_dir=tmp_path / "rec")
    try:
        try:
            launched = await pool.launch(kind="chromium", headed=False, url="about:blank")
        except Exception as exc:  # pragma: no cover - host without chromium
            pytest.skip(f"chromium unavailable: {exc}")
        session = pool.get(launched["instance_id"])
        async with session.operation("test_setup"):
            await session.page.set_content(_PAGE)

        with pytest.raises(RuntimeError) as caught:
            await execution.run_macro(session, "m", {"password": PW_FIXTURE})

        payload: dict[str, Any] = caught.value.args[0]
        bundle = payload["bundle"]
        html = Path(bundle["html_path"]).read_text(encoding="utf-8")
        assert "typed <redacted>" in html
        assert PW_FIXTURE not in html and PW_FIXTURE.lower() not in html.lower()
        assert bundle["screenshot"] is None and bundle["screenshot_suppressed"] is True
        assert PW_FIXTURE not in json.dumps(payload)
        assert any("<redacted>" in str(message.get("text")) for message in bundle["console_tail"])
        assert list((tmp_path / "rec").rglob("*-fail-*.png")) == []
    finally:
        await shutdown_pool(pool)
