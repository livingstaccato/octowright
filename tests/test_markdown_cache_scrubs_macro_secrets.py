# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The page-text (markdown) cache never persists a macro's credential values.

The recorder has scrubbed them through the session privacy ledger since the
ledger existed; the markdown cache is a second durable write of page content
and bypassed it, so a page that rendered the password -- exactly what
``expect_no_text`` exists to catch -- put it on disk in cleartext.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.privacy import REDACTED, install_sensitive_recorder
from octowright.session.core import BrowserSession

SECRET = "hunter2-Correct-Horse!"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def session(tmp_path: Path) -> BrowserSession:
    page = AsyncMock()
    page.url = "https://octowright.com/"
    page.content = AsyncMock(return_value=f"<html><body><h1>Your password is {SECRET}</h1></body></html>")
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


@pytest.mark.anyio
async def test_a_macro_credential_is_scrubbed_from_the_markdown_cache(session: BrowserSession) -> None:
    install_sensitive_recorder(session, [SECRET])
    path = await session.capture_markdown(force=True)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert SECRET not in text
    assert REDACTED in text


@pytest.mark.anyio
async def test_a_value_admitted_later_is_scrubbed_from_the_next_capture(session: BrowserSession) -> None:
    """The scrubber reads the ledger live, like the recorder wrapper does."""
    ledger = install_sensitive_recorder(session, [])
    ledger.add([SECRET])
    path = await session.capture_markdown(force=True)
    assert path is not None and SECRET not in path.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_without_a_macro_run_the_cache_is_unchanged(session: BrowserSession) -> None:
    path = await session.capture_markdown(force=True)
    assert path is not None and SECRET in path.read_text(encoding="utf-8")


ENTITY_SECRET = "Tr0ub4dor&3<x>\"'"  # pragma: allowlist secret -- a fixture, never a real credential


@pytest.fixture
def no_markitdown(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "markitdown", None)  # import raises ImportError


@pytest.mark.anyio
@pytest.mark.usefixtures("no_markitdown")
async def test_the_fallback_writes_decoded_text(session: BrowserSession) -> None:
    assert await session._extract_markdown("<p>fish &amp; chips &lt;3</p>") == "fish & chips <3"


@pytest.mark.anyio
@pytest.mark.parametrize("markitdown_present", [False, True])
async def test_an_html_escaped_credential_is_scrubbed(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch, markitdown_present: bool
) -> None:
    import html
    import sys
    from types import SimpleNamespace

    if markitdown_present:

        class _StreamInfo:
            def __init__(self, **kw: object) -> None:
                self.kw = kw

        class _MarkItDown:
            def convert_stream(self, stream: object, stream_info: object = None) -> SimpleNamespace:
                return SimpleNamespace(text_content=html.unescape(stream.read().decode()))  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "markitdown", SimpleNamespace(MarkItDown=_MarkItDown, StreamInfo=_StreamInfo))
    else:
        monkeypatch.setitem(sys.modules, "markitdown", None)
    session.page.content = AsyncMock(return_value=f"<h1>Your password is {html.escape(ENTITY_SECRET)}</h1>")
    install_sensitive_recorder(session, [ENTITY_SECRET])
    path = await session.capture_markdown(force=True)
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert ENTITY_SECRET not in text and html.escape(ENTITY_SECRET) not in text
    assert REDACTED in text


def test_the_scrubber_knows_html_escaped_spellings() -> None:
    import html

    from octowright.macros.privacy import scrub_sensitive_values

    for spelling in (html.escape(ENTITY_SECRET), html.escape(ENTITY_SECRET, quote=False)):
        assert ENTITY_SECRET not in scrub_sensitive_values(f"x {spelling} y", (ENTITY_SECRET,))
        assert spelling not in scrub_sensitive_values(f"x {spelling} y", (ENTITY_SECRET,))


@pytest.mark.anyio
async def test_markitdown_is_given_the_html_as_a_stream(
    session: BrowserSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """convert(str) treats the string as a path or URI (measured on markitdown 0.1.8), so it always failed."""
    import sys
    from types import SimpleNamespace

    seen: dict[str, object] = {}

    class _StreamInfo:
        def __init__(self, **kw: object) -> None:
            seen["info"] = kw

    class _MarkItDown:
        def convert(self, source: str) -> None:
            raise FileNotFoundError(source)

        def convert_stream(self, stream: object, stream_info: object = None) -> SimpleNamespace:
            seen["bytes"] = stream.read()  # type: ignore[attr-defined]
            return SimpleNamespace(text_content="# converted")

    monkeypatch.setitem(sys.modules, "markitdown", SimpleNamespace(MarkItDown=_MarkItDown, StreamInfo=_StreamInfo))
    assert await session._extract_markdown("<h1>converted</h1>") == "# converted"
    assert seen["bytes"] == b"<h1>converted</h1>" and seen["info"] == {"extension": ".html"}


def test_scrub_patterns_are_compiled_once_per_ledger_state() -> None:
    """The durable scrubber runs on every capture; variants and regexes are derived once."""
    from octowright.macros import privacy

    privacy._scrub_patterns.cache_clear()
    values = (ENTITY_SECRET, SECRET)
    for _ in range(5):
        privacy.scrub_sensitive_values(f"a {SECRET} b", values)
    info = privacy._scrub_patterns.cache_info()
    assert info.misses == 1 and info.hits == 4
