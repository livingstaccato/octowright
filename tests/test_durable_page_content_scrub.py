# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Every durable write of page content goes through the session's privacy ledger.

The recorder and the markdown cache were covered; the websocket sidecar and
``capture_create`` were not, so a credential the page echoed -- in a socket
frame, or in text a capture read -- reached disk in cleartext. A binary frame
cannot be edited byte-for-byte safely, so one that holds a macro value is not
stored at all and is marked ``payload_redacted``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.privacy import REDACTED, install_sensitive_recorder
from octowright.server import captures as _tools
from octowright.session.core import BrowserSession
from tests._operation_gate_fakes import OperationAwareFake

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    return BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url="https://octowright.com",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )


def _sidecar(session: BrowserSession) -> str:
    fh = session._websocket_fh
    if fh is not None:
        fh.flush()
    assert session.websocket_path is not None
    return session.websocket_path.read_text(encoding="utf-8")


def test_a_text_frame_is_scrubbed(session: BrowserSession) -> None:
    install_sensitive_recorder(session, [SECRET])
    session._append_websocket_cache(
        direction="received",
        id_="1",
        url=f"wss://x.test/?token={SECRET}",
        payload_preview=f'{{"ok": "{SECRET}"}}',
        payload=f'{{"ok": "{SECRET}"}}',
    )
    text = _sidecar(session)
    assert SECRET not in text and REDACTED in text


def test_a_binary_frame_holding_a_value_is_not_stored(session: BrowserSession) -> None:
    install_sensitive_recorder(session, [SECRET])
    session._append_websocket_cache(
        direction="received", id_="1", url="wss://x.test/", payload=b"\x00\x01" + SECRET.encode() + b"\xff"
    )
    row = json.loads(_sidecar(session).splitlines()[-1])
    assert "payload_b64" not in row and row["payload_redacted"] is True
    assert row["payload_size"] == len(SECRET) + 3


def test_frames_are_untouched_without_a_macro_run(session: BrowserSession) -> None:
    session._append_websocket_cache(direction="sent", id_="1", url="wss://x.test/", payload=f"plain {SECRET}")
    assert SECRET in _sidecar(session)


# --- capture_create ------------------------------------------------------------------


class _FakeCaptureSession(OperationAwareFake):
    pass


@pytest.mark.anyio
async def test_capture_create_scrubs_what_it_saves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s = _FakeCaptureSession()
    s.page = MagicMock()
    s.page.url = "https://x.test/"
    s.page.title = AsyncMock(return_value="x")
    s.page.locator.return_value.inner_text = AsyncMock(return_value=f"Your password is {SECRET}")
    s._target = MagicMock(return_value=s.page)
    s.durable_text_scrubber = lambda text: text.replace(SECRET, REDACTED)
    pool = MagicMock()
    pool.get.return_value = s
    monkeypatch.setattr(_tools, "pool", pool)
    saved: dict[str, object] = {}
    monkeypatch.setattr(
        _tools._captures,
        "save_capture",
        lambda **kw: saved.update(kw) or {"capture_id": "c", "preview": "", "truncated": False},
    )
    await _tools.capture_create("abc", source="text")
    assert SECRET not in str(saved["content"]) and REDACTED in str(saved["content"])
