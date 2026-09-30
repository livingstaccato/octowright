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

from octowright.macros.privacy import REDACTED, DurableTextScrubber, PrivacyLedger, install_sensitive_recorder
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
    s.durable_text_scrubber = DurableTextScrubber(PrivacyLedger([SECRET]))
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


# --- an installed scrubber with nothing to scrub costs nothing ---------------------------


def test_an_empty_ledger_does_not_scrub_binary_frames(session: BrowserSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Once any macro ran, the scrubber stays installed; with no values it must not touch every frame."""
    ledger = install_sensitive_recorder(session, [])
    scrubber = session.durable_text_scrubber
    calls: list[str] = []

    class _Counting:
        active = property(lambda _self: scrubber.active)  # type: ignore[union-attr]

        def __call__(self, text: str) -> str:
            calls.append(text)
            return scrubber(text)  # type: ignore[misc]

    session.durable_text_scrubber = _Counting()  # type: ignore[assignment]
    entry: dict[str, object] = {"url": "wss://x.test/"}
    session._scrub_websocket_entry(entry, b"\x00\x01\x02\x03")
    assert calls == [] and entry == {"url": "wss://x.test/", "payload_b64": "AAECAw=="}

    # ...and the same scrubber checks the bytes once the ledger holds a value.
    ledger.add([SECRET])
    entry = {"url": "wss://x.test/"}
    session._scrub_websocket_entry(entry, SECRET.encode())
    assert entry.get("payload_redacted") is True and "payload_b64" not in entry


def test_a_binary_frame_holding_a_value_is_never_encoded_or_decoded(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The scrub check runs on the bytes: no base64 round trip for a frame that is then dropped."""
    from octowright.session import core_io_mixin

    install_sensitive_recorder(session, [SECRET])
    seen: list[str] = []
    for name in ("b64encode", "b64decode"):
        real = getattr(core_io_mixin.base64, name)
        monkeypatch.setattr(
            core_io_mixin.base64, name, lambda data, *a, _n=name, _r=real: seen.append(_n) or _r(data, *a)
        )
    session._append_websocket_cache(direction="received", id_="1", url="wss://x.test/", payload=SECRET.encode())
    assert seen == []
    assert json.loads(_sidecar(session).splitlines()[-1])["payload_redacted"] is True
