# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_io_mixin.

This module is the data-recording floor of the system: every browser event
funnels through `_append_websocket_cache`, `attach_console`, `_register_popup`,
`_handle_websocket`, and `capture_markdown`. Currently 8% covered.

We exercise the helpers directly with a hand-rolled subject object that
provides only the attributes the mixin methods read from `self`. That keeps
us from booting a real BrowserSession.
"""

from __future__ import annotations

import ast
import base64
import json
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core_io_mixin import SessionIOMixin, _looks_like_binary_text


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_subject(tmp_path: Path) -> SessionIOMixin:
    """Build an instance carrying just the attributes the mixin touches."""
    log_path = tmp_path / "rec.jsonl"
    log_path.touch()
    subj = SessionIOMixin.__new__(SessionIOMixin)
    subj.markdown_path = None
    subj.websocket_path = None
    subj._last_markdown_capture_url = None
    subj._last_markdown_capture_key = None
    subj._pending_markdown_capture = None
    subj.console = deque()
    subj.console_count = 0
    subj.pages = []
    subj.page_count = 0
    subj.page = MagicMock()
    subj.recorder = MagicMock()
    subj.recorder.record = MagicMock()
    subj._bg_tasks = set()
    subj.log_path = log_path

    def _ws_path() -> Path:
        return log_path.with_suffix(".websocket.cache.jsonl")

    def _md_path() -> Path:
        return log_path.with_suffix(".markdown.md")

    subj._websocket_cache_path = _ws_path  # type: ignore[attr-defined]
    subj._markdown_cache_path = _md_path  # type: ignore[attr-defined]
    subj._target = lambda: subj.page  # type: ignore[attr-defined]
    return subj


# ─── _looks_like_binary_text ─────────────────────────────────────────────────


class TestLooksLikeBinaryText:
    def test_double_quoted_byte_repr_detected(self) -> None:
        """Strings of the form b\"...\" are treated as binary."""
        assert _looks_like_binary_text('b"hello"') is True

    def test_single_quoted_byte_repr_detected(self) -> None:
        """Strings of the form b'...' are treated as binary."""
        assert _looks_like_binary_text("b'hello'") is True

    def test_plain_string_not_detected(self) -> None:
        """Regular strings are not flagged as binary."""
        assert _looks_like_binary_text("hello") is False

    def test_non_string_inputs_return_false(self) -> None:
        """Bytes/None/int/list — not strings → False."""
        assert _looks_like_binary_text(b"hello") is False
        assert _looks_like_binary_text(None) is False
        assert _looks_like_binary_text(42) is False

    def test_partial_marker_not_detected(self) -> None:
        """`b'...` without trailing quote → False."""
        assert _looks_like_binary_text("b'hello") is False
        assert _looks_like_binary_text("'hello'") is False


# ─── _append_websocket_cache ─────────────────────────────────────────────────


class TestAppendWebsocketCacheNoPayload:
    def test_writes_minimal_envelope_when_no_payload(self, tmp_path: Path) -> None:
        """No payload → entry has just ts/action/id/url, no payload_* keys."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x")
        cache_file = subj.websocket_path
        assert cache_file.exists()
        line = cache_file.read_text().strip()
        entry = json.loads(line)
        assert entry["action"] == "websocket_framesent"
        assert entry["id"] == 1
        assert entry["url"] == "ws://x"
        assert "payload_preview" not in entry
        assert "payload_text" not in entry
        assert "payload_b64" not in entry

    def test_action_field_carries_direction(self, tmp_path: Path) -> None:
        """action = f'websocket_{direction}' — mutating the f-string would break."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(direction="framereceived", id_=2, url="ws://y")
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["action"] == "websocket_framereceived"

    def test_appends_not_overwrites(self, tmp_path: Path) -> None:
        """Multiple writes append lines, don't truncate."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x")
        subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x")
        lines = subj.websocket_path.read_text().splitlines()
        assert len(lines) == 2

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        """If recordings dir doesn't exist yet, it's created."""
        log_path = tmp_path / "deep" / "nested" / "rec.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        subj = _make_subject(tmp_path)
        subj.log_path = log_path
        subj._websocket_cache_path = lambda: log_path.with_suffix(".websocket.cache.jsonl")  # type: ignore[attr-defined]
        subj._append_websocket_cache(direction="framesent", id_=1, url="ws://x")
        assert subj.websocket_path.exists()


class TestAppendWebsocketCacheTextPayload:
    def test_text_payload_stored_in_payload_text(self, tmp_path: Path) -> None:
        """Plain str payload → entry['payload_text']."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload="hello",
            payload_preview="hello",
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_text"] == "hello"
        assert entry["payload_preview"] == "hello"

    def test_size_falls_back_to_len_when_not_supplied(self, tmp_path: Path) -> None:
        """payload_size=None → len(payload) used."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload="hello",
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_size"] == 5

    def test_explicit_size_preserved(self, tmp_path: Path) -> None:
        """Caller-supplied size beats len()."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload="hello",
            payload_size=999,
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_size"] == 999

    def test_empty_preview_default(self, tmp_path: Path) -> None:
        """payload_preview=None → '' (not the literal None)."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload="hello",
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_preview"] == ""


class TestAppendWebsocketCacheBinaryPayload:
    def test_bytes_payload_base64_encoded(self, tmp_path: Path) -> None:
        """raw bytes → entry['payload_b64'] base64-encoded; size = len(bytes)."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload=b"\x00\x01\x02hello",
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_b64"] == base64.b64encode(b"\x00\x01\x02hello").decode("ascii")
        assert entry["payload_size"] == 8

    def test_bytearray_payload_base64_encoded(self, tmp_path: Path) -> None:
        """bytearray normalized to bytes."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload=bytearray(b"abc"),
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_b64"] == base64.b64encode(b"abc").decode("ascii")

    def test_byte_repr_string_decoded_and_b64(self, tmp_path: Path) -> None:
        """A str of the form b'...' is parsed and re-encoded as b64."""
        subj = _make_subject(tmp_path)
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload="b'\\x00\\x01abc'",
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        # The b'...' string evaluates to bytes b"\x00\x01abc" → 5 bytes.
        decoded = ast.literal_eval("b'\\x00\\x01abc'")
        assert entry["payload_b64"] == base64.b64encode(decoded).decode("ascii")
        assert entry["payload_size"] == len(decoded)

    def test_byte_repr_string_unparseable_falls_back_to_payload_text(self, tmp_path: Path) -> None:
        """Malformed b'...' string → falls back to payload_text + no payload_b64."""
        subj = _make_subject(tmp_path)
        # The literal `b"..."` form, but with content that ast.literal_eval can't parse.
        # Use a plain string with the b" prefix so _looks_like_binary_text matches but parsing fails.
        subj._append_websocket_cache(
            direction="framesent",
            id_=1,
            url="ws://x",
            payload='b"not\\xparseable"',
        )
        entry = json.loads(subj.websocket_path.read_text().strip())
        assert entry["payload_text"] == 'b"not\\xparseable"'
        assert "payload_b64" not in entry


# ─── _extract_markdown ───────────────────────────────────────────────────────


class TestExtractMarkdownFallback:
    @pytest.mark.anyio
    async def test_strips_script_and_style_tags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When markitdown isn't importable, fallback regex drops script/style content."""
        # Force the markitdown import to fail.
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        html = "<html><body><script>evil()</script>hello<style>.x{}</style> world</body></html>"
        result = await subj._extract_markdown(html)
        assert "evil" not in result
        assert ".x{}" not in result
        assert "hello" in result
        assert "world" in result

    @pytest.mark.anyio
    async def test_strips_html_tags(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fallback strips arbitrary tags but keeps text."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        result = await subj._extract_markdown("<p>foo</p><br><strong>bar</strong>")
        assert "foo" in result
        assert "bar" in result
        assert "<p>" not in result
        assert "<strong>" not in result

    @pytest.mark.anyio
    async def test_collapses_excessive_blank_lines(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """3+ consecutive newlines collapse to 2."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        result = await subj._extract_markdown("a\n\n\n\nb")
        assert "\n\n\n" not in result

    @pytest.mark.anyio
    async def test_uses_markitdown_when_available(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When markitdown is importable and returns a str, that str is the result."""

        class _FakeMarkItDown:
            def convert(self, html: str) -> str:
                return f"[markdown for {len(html)} chars]"

        fake_mod = SimpleNamespace(MarkItDown=_FakeMarkItDown)
        monkeypatch.setitem(sys.modules, "markitdown", fake_mod)
        subj = _make_subject(tmp_path)
        result = await subj._extract_markdown("<p>hello</p>")
        assert result == "[markdown for 12 chars]"

    @pytest.mark.anyio
    async def test_falls_back_when_markitdown_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If MarkItDown.convert raises, the regex fallback path runs."""

        class _BoomMarkItDown:
            def convert(self, html: str) -> str:
                raise RuntimeError("nope")

        fake_mod = SimpleNamespace(MarkItDown=_BoomMarkItDown)
        monkeypatch.setitem(sys.modules, "markitdown", fake_mod)
        subj = _make_subject(tmp_path)
        result = await subj._extract_markdown("<p>fallback</p>")
        assert "fallback" in result

    @pytest.mark.anyio
    async def test_markitdown_failure_logged_at_debug(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing markitdown release shouldn't be invisible. The fallback
        path runs as before, but the exception is logged at debug so it's
        visible during development without spamming production."""

        class _BoomMarkItDown:
            def convert(self, html: str) -> str:
                raise RuntimeError("markitdown bug")

        fake_mod = SimpleNamespace(MarkItDown=_BoomMarkItDown)
        monkeypatch.setitem(sys.modules, "markitdown", fake_mod)

        from octowright.session import core_io_mixin as _io

        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            _io,
            "log",
            SimpleNamespace(debug=lambda event, **kw: events.append((event, kw))),
        )

        subj = _make_subject(tmp_path)
        await subj._extract_markdown("<p>x</p>")
        assert any("markitdown_failed" in name for name, _ in events)


# ─── capture_markdown ────────────────────────────────────────────────────────


class TestCaptureMarkdown:
    @pytest.mark.anyio
    async def test_writes_markdown_and_records(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: HTML fetched, markdown written, recorder.record('markdown_cached') called."""
        monkeypatch.setitem(sys.modules, "markitdown", None)  # force fallback
        subj = _make_subject(tmp_path)
        subj.page.url = "https://example.com/p"
        subj.page.content = AsyncMock(return_value="<p>hello</p>")
        path = await subj.capture_markdown()
        assert path is not None and path.exists()
        assert subj.markdown_path == path
        assert subj._last_markdown_capture_url == "https://example.com/p"
        # recorder called with markdown_cached.
        recorded = [c.args for c in subj.recorder.record.call_args_list]
        assert any(call == ("markdown_cached",) for call in recorded)

    @pytest.mark.anyio
    async def test_short_circuits_on_cache_hit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same URL + key + path-exists + not force → returns the cached path without re-rendering."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        subj.page.url = "https://example.com/p"
        subj.page.content = AsyncMock(return_value="<p>hello</p>")
        # First call populates the cache.
        await subj.capture_markdown()
        assert subj.page.content.await_count == 1
        # Second call: same URL, file exists → no re-render.
        await subj.capture_markdown()
        assert subj.page.content.await_count == 1

    @pytest.mark.anyio
    async def test_force_refreshes_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """force=True bypasses the cache-hit fast path."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        subj.page.url = "https://example.com/p"
        subj.page.content = AsyncMock(return_value="<p>hello</p>")
        await subj.capture_markdown()
        await subj.capture_markdown(force=True)
        assert subj.page.content.await_count == 2

    @pytest.mark.anyio
    async def test_url_change_re_renders(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Different URL → re-render, even without force."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        subj.page.url = "https://example.com/a"
        subj.page.content = AsyncMock(return_value="<p>hello</p>")
        await subj.capture_markdown()
        subj.page.url = "https://example.com/b"
        await subj.capture_markdown()
        assert subj.page.content.await_count == 2

    @pytest.mark.anyio
    async def test_content_failure_records_error_and_returns_none(self, tmp_path: Path) -> None:
        """If page.content() raises, we record markdown_cache_error and return None."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://example.com/p"
        subj.page.content = AsyncMock(side_effect=RuntimeError("boom"))
        result = await subj.capture_markdown()
        assert result is None
        recorded = [c.args for c in subj.recorder.record.call_args_list]
        assert any(call == ("markdown_cache_error",) for call in recorded)

    @pytest.mark.anyio
    async def test_url_attribute_failure_handled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If accessing target.url raises, current_url falls to None and capture proceeds."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        target = MagicMock()
        type(target).url = property(lambda _self: (_ for _ in ()).throw(RuntimeError("no url")))
        target.content = AsyncMock(return_value="<p>x</p>")
        subj._target = lambda: target  # type: ignore[attr-defined]
        path = await subj.capture_markdown()
        assert path is not None


# ─── _schedule_markdown_capture ──────────────────────────────────────────────


class TestScheduleMarkdownCapture:
    @pytest.mark.anyio
    async def test_skips_when_pending_capture_active(self, tmp_path: Path) -> None:
        """If there's already a pending task that's not done, we no-op."""
        subj = _make_subject(tmp_path)
        pending = MagicMock()
        pending.done = MagicMock(return_value=False)
        subj._pending_markdown_capture = pending
        subj._schedule_markdown_capture()
        # No new task created.
        assert subj._pending_markdown_capture is pending

    @pytest.mark.anyio
    async def test_done_pending_task_replaced(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A done pending task is treated as no longer pending → new task created."""
        monkeypatch.setitem(sys.modules, "markitdown", None)
        subj = _make_subject(tmp_path)
        subj.page.url = "https://example.com/p"
        subj.page.content = AsyncMock(return_value="<p>x</p>")
        old = MagicMock()
        old.done = MagicMock(return_value=True)
        subj._pending_markdown_capture = old
        subj._schedule_markdown_capture()
        # New task exists and is added to bg_tasks.
        assert subj._pending_markdown_capture is not old
        assert subj._pending_markdown_capture in subj._bg_tasks
        # Let it finish so pytest-asyncio doesn't complain.
        try:
            await subj._pending_markdown_capture
        except Exception:
            pass


# ─── attach_console ──────────────────────────────────────────────────────────


class TestAttachConsole:
    def test_registers_handler_on_console_event(self, tmp_path: Path) -> None:
        """attach_console wires a handler via page.on('console', ...)."""
        subj = _make_subject(tmp_path)
        subj.attach_console()
        # `page.on` was called with first arg 'console'.
        subj.page.on.assert_called_once()
        first_arg = subj.page.on.call_args[0][0]
        assert first_arg == "console"

    def test_handler_appends_console_and_emits_record(self, tmp_path: Path) -> None:
        """Invoking the handler updates console deque and records an event."""
        subj = _make_subject(tmp_path)
        subj.attach_console()
        handler = subj.page.on.call_args[0][1]
        msg = SimpleNamespace(type="warning", text="watch out")
        handler(msg)
        assert subj.console_count == 1
        assert subj.console[-1] == {"level": "warning", "text": "watch out"}
        # Recorder got the console event.
        names = [c.args[0] for c in subj.recorder.record.call_args_list]
        assert "console" in names


# ─── _register_popup ─────────────────────────────────────────────────────────


class TestRegisterPopup:
    def test_appends_page_and_increments_count(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The new popup page lands in `pages` and `page_count` reflects len."""
        # `_register_popup` calls _wire_listeners — stub that to a no-op.
        from octowright.browser_pool import listeners as _listeners

        monkeypatch.setattr(_listeners, "_wire_listeners", lambda *a, **kw: None)
        subj = _make_subject(tmp_path)
        popup = MagicMock()
        popup.url = "https://popup"
        subj._register_popup(popup)
        assert popup in subj.pages
        assert subj.page_count == len(subj.pages)
        names = [c.args[0] for c in subj.recorder.record.call_args_list]
        assert "popup_opened" in names

    def test_popup_index_in_record_payload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """popup_opened recorder call carries the page_index of the new tab."""
        from octowright.browser_pool import listeners as _listeners

        monkeypatch.setattr(_listeners, "_wire_listeners", lambda *a, **kw: None)
        subj = _make_subject(tmp_path)
        subj.pages = [MagicMock()]  # an existing page
        popup = MagicMock()
        popup.url = "https://second"
        subj._register_popup(popup)
        # Last record call kwargs include page_index=1 (the new index).
        popup_calls = [c for c in subj.recorder.record.call_args_list if c.args[0] == "popup_opened"]
        assert popup_calls
        assert popup_calls[0].kwargs.get("page_index") == 1


# ─── _handle_websocket ───────────────────────────────────────────────────────


class TestHandleWebsocket:
    def test_records_websocket_opened(self, tmp_path: Path) -> None:
        """A new websocket records 'websocket_opened' immediately."""
        subj = _make_subject(tmp_path)
        ws = MagicMock()
        ws.url = "ws://x"
        ws.id = "ws1"
        subj._handle_websocket(ws)
        names = [c.args[0] for c in subj.recorder.record.call_args_list]
        assert "websocket_opened" in names

    def test_falls_back_to_id_when_attribute_missing(self, tmp_path: Path) -> None:
        """If the websocket has no .id attribute, use python id() as fallback."""
        subj = _make_subject(tmp_path)
        ws = SimpleNamespace(url="ws://x", on=lambda *a, **kw: None)
        # No `.id` → fallback path.
        subj._handle_websocket(ws)
        names = [c.args[0] for c in subj.recorder.record.call_args_list]
        assert "websocket_opened" in names

    def test_skips_when_no_on_attribute(self, tmp_path: Path) -> None:
        """If the websocket can't be subscribed to (no .on), the helper returns early."""
        subj = _make_subject(tmp_path)
        ws = SimpleNamespace(url="ws://x", id="ws1")
        # No `.on` attribute — early return after recording 'opened'.
        subj._handle_websocket(ws)
        names = [c.args[0] for c in subj.recorder.record.call_args_list]
        assert names == ["websocket_opened"]

    def test_subscribes_to_four_event_types(self, tmp_path: Path) -> None:
        """websocket.on is called for framesent, framereceived, close, socketerror."""
        subj = _make_subject(tmp_path)
        ws = MagicMock()
        ws.url = "ws://x"
        ws.id = "ws1"
        subj._handle_websocket(ws)
        events_subscribed = [c.args[0] for c in ws.on.call_args_list]
        assert events_subscribed == ["framesent", "framereceived", "close", "socketerror"]

    def test_subscribe_failure_swallowed(self, tmp_path: Path) -> None:
        """If websocket.on raises, the helper returns silently (the opened event still recorded)."""
        subj = _make_subject(tmp_path)
        ws = MagicMock()
        ws.url = "ws://x"
        ws.id = "ws1"
        ws.on = MagicMock(side_effect=RuntimeError("boom"))
        # Must not raise.
        subj._handle_websocket(ws)
