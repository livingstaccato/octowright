# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A failed run's diagnostics are split by sink kind (#248, Part 0).

A run holding classified values used to suppress the whole diagnostic bundle,
so its failure carried no console tail, page URL or page HTML at all. Text can
be scrubbed and pixels cannot, so the split is by sink:

- the diagnostic **screenshot** is suppressed whenever the run or the session
  ledger holds a value;
- the diagnostic **HTML** file and the **console tail** are always produced,
  scrubbed of every value the run and the session hold;
- automatic artifact evidence is still decided per run (unchanged here).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros import execution, failure_context, privacy
from octowright.macros.privacy import admit_redacted_input
from tests._operation_gate_fakes import OperationAwareFake
from tests.test_session_ops_mixin_diagnostic import _build

SECRET = "Diag-Secret-8Kq2"  # pragma: allowlist secret


def _spellings(value: str) -> tuple[str, ...]:
    return (value, value.lower(), value.upper(), json.dumps(value)[1:-1], value.replace("-", "%2D"))


def _clean(text: str) -> bool:
    return not any(spelling in text for spelling in _spellings(SECRET))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# -- the session producer --------------------------------------------------------


def _page(html: str) -> MagicMock:
    page = MagicMock()
    page.url = f"https://app.example.test/?q={SECRET}"
    page.title = AsyncMock(return_value=f"Hello {SECRET}")
    page.content = AsyncMock(return_value=html)
    page.screenshot = AsyncMock()
    return page


def _scrub(value: Any) -> Any:
    return privacy.scrub_sensitive_values(value, (SECRET,))


@pytest.mark.anyio
async def test_the_producer_scrubs_the_html_it_writes_and_skips_the_screenshot(tmp_path: Path) -> None:
    html = f"<p>signed in as {SECRET}</p><input value='{SECRET}'>"
    session = _build(tmp_path, page=_page(html), console=[{"type": "log", "text": f"token {SECRET}"}])

    bundle = await session.diagnostic_bundle(console_tail=5, html_preview_chars=200, scrub=_scrub, screenshot=False)

    written = Path(bundle["html_path"]).read_text(encoding="utf-8")
    assert _clean(written) and "<redacted>" in written
    assert bundle["html_sha256"] == hashlib.sha256(written.encode("utf-8")).hexdigest()
    assert bundle["html_size"] == len(written)
    assert _clean(json.dumps(bundle))
    session.page.screenshot.assert_not_awaited()
    assert bundle["screenshot"] is None
    assert bundle["screenshot_suppressed"] is True
    assert list(tmp_path.rglob("*.png")) == []


@pytest.mark.anyio
async def test_the_producer_is_unchanged_without_a_scrub(tmp_path: Path) -> None:
    session = _build(tmp_path, page=_page("<p>plain</p>"))

    bundle = await session.diagnostic_bundle(console_tail=0)

    session.page.screenshot.assert_awaited_once()
    assert "screenshot_suppressed" not in bundle


# -- a failed macro run ------------------------------------------------------------


class _Session(OperationAwareFake):
    instance_id = "diag-split"
    kind = "chromium"

    def __init__(self, bundle: dict[str, Any] | Exception) -> None:
        super().__init__()
        self.page = MagicMock()
        self.page.evaluate = AsyncMock()
        self.recorder = MagicMock()
        self.durable_text_scrubber = None
        if isinstance(bundle, Exception):
            self.diagnostic_bundle = AsyncMock(side_effect=bundle)
        else:
            self.diagnostic_bundle = AsyncMock(return_value=bundle)


@pytest.fixture
def failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(execution, "load_macro", lambda _n: {"actions": [{"action": "click", "selector": "#a"}]})

    async def boom(*_a: Any, **_kw: Any) -> tuple[int, int]:
        raise RuntimeError("step failed")

    monkeypatch.setattr(execution, "_dispatch_one", boom)
    monkeypatch.setattr(execution, "_suggest_fix", AsyncMock(return_value=None))
    monkeypatch.setattr(execution, "_push_status", AsyncMock())
    monkeypatch.setattr(failure_context, "failed_requests_tail", lambda _s: [])


async def _fail(session: _Session, args: dict[str, Any]) -> dict[str, Any]:
    with pytest.raises(RuntimeError) as caught:
        await execution.run_macro(session, "m", args)
    payload = caught.value.args[0]
    assert isinstance(payload, dict)
    return payload


def _call_kwargs(session: _Session) -> dict[str, Any]:
    session.diagnostic_bundle.assert_awaited_once()
    return dict(session.diagnostic_bundle.await_args.kwargs)


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing")
async def test_a_classified_run_keeps_a_scrubbed_bundle_without_a_screenshot() -> None:
    session = _Session({"url": f"https://x.test/?p={SECRET}", "console_tail": [{"text": f"pw={SECRET}"}]})

    payload = await _fail(session, {"password": SECRET})

    kwargs = _call_kwargs(session)
    assert kwargs["screenshot"] is False
    assert kwargs["scrub"](f"a {SECRET} b") == "a <redacted> b"
    assert payload["bundle"]["url"] == "https://x.test/?p=<redacted>"
    assert "diagnostic_suppressed" not in payload["bundle"]
    assert _clean(json.dumps(payload))


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing")
async def test_a_later_unclassified_run_on_the_same_session_scrubs_what_the_session_holds() -> None:
    """The session ledger outlives the run that admitted the value."""
    session = _Session({"console_tail": [{"text": f"echo {SECRET}"}]})
    admit_redacted_input(session, SECRET)

    payload = await _fail(session, {"note": "plain"})

    kwargs = _call_kwargs(session)
    assert kwargs["screenshot"] is False
    assert kwargs["scrub"](f"x {SECRET} y") == "x <redacted> y"
    assert _clean(json.dumps(payload))


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing")
async def test_an_unclassified_run_on_a_clean_session_keeps_its_screenshot() -> None:
    session = _Session({"url": "https://x.test/"})

    await _fail(session, {"note": "plain"})

    kwargs = _call_kwargs(session)
    assert kwargs.get("screenshot", True) is True
    assert kwargs.get("scrub") is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing")
async def test_the_console_is_scrubbed_before_it_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cutting first could leave the first characters of a value past the scrub."""
    pad = "x" * (failure_context.MACRO_FAILURE_CONSOLE_TEXT_CHARS - 6)
    session = _Session({"console_tail": [{"text": pad + SECRET}]})

    payload = await _fail(session, {"password": SECRET})

    text = payload["bundle"]["console_tail"][0]["text"]
    assert SECRET[:6] not in text
    assert text.startswith(pad)


@pytest.mark.asyncio
@pytest.mark.usefixtures("failing")
async def test_a_producer_error_is_scrubbed_too() -> None:
    session = _Session(OSError(f"could not write page holding {SECRET}"))

    payload = await _fail(session, {"password": SECRET})

    assert "diagnostic_error" in payload["bundle"]
    assert _clean(json.dumps(payload))
