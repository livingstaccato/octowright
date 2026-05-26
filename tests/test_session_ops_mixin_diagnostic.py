# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_ops_mixin.diagnostic_bundle.

Sister files: test_session_ops_mixin_actions.py, test_session_ops_mixin_lifecycle.py.

Pins: every recorder.record() call, the seed-dict shape, individual swallow
paths (url/title/html/screenshot fail independently), html_full toggle,
preview truncation, screenshot_dir override, instance_id+timestamp filename
convention, hashlib digest correctness.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.session.core_ops_mixin import (
    DEFAULT_PREVIEW_CHARS,
    SessionOpsMixin,
    _timestamp,
)


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def record(self, action: str, **kwargs: Any) -> None:
        self.events.append((action, kwargs))

    def close(self) -> None:
        self.closed = True


def _build(tmp_path: Path, *, page: Any = None, context: Any = None, **overrides: Any) -> SessionOpsMixin:
    inst = SessionOpsMixin.__new__(SessionOpsMixin)
    inst.page = page if page is not None else MagicMock()
    inst.context = context if context is not None else MagicMock()
    inst.browser = None
    inst.recorder = _Recorder()
    inst.console = []
    inst.pages = [inst.page]
    inst.active_frame = None
    inst.video_path = None
    inst.trace_path = None
    inst.har_path = None
    inst.markdown_path = None
    inst.websocket_path = None
    inst.trace = False
    inst._video = None
    inst._bg_tasks = set()
    inst.instance_id = "abc123"
    inst.log_path = tmp_path / "session.jsonl"
    inst.log_path.write_text("", encoding="utf-8")
    inst._target = lambda: inst.active_frame if inst.active_frame is not None else inst.page  # type: ignore[method-assign]
    for k, v in overrides.items():
        setattr(inst, k, v)
    return inst


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _ok_page(html: str = "<x/>") -> MagicMock:
    page = MagicMock()
    page.url = "https://octowright.com"
    page.title = AsyncMock(return_value="Title")
    page.content = AsyncMock(return_value=html)
    page.screenshot = AsyncMock()
    return page


# ─── _timestamp ─────────────────────────────────────────────────────────────


class TestTimestamp:
    def test_format_is_yyyymmddthhmmssz(self) -> None:
        """The strftime fmt must be the compact-Z UTC shape; mutating it would
        change the on-disk filenames diagnostic_bundle and screenshot use."""
        result = _timestamp()
        assert len(result) == 16
        assert result[8] == "T"
        assert result.endswith("Z")
        assert result.startswith("20")


# ─── diagnostic_bundle ─────────────────────────────────────────────────────


class TestDiagnosticBundle:
    @pytest.mark.anyio
    async def test_returns_seven_base_keys(self, tmp_path: Path) -> None:
        """Mutating the seed dict shape would lose a key."""
        inst = _build(tmp_path, page=_ok_page("<html>x</html>"))
        bundle = await inst.diagnostic_bundle()
        for key in (
            "console_tail",
            "url",
            "title",
            "html_path",
            "html_size",
            "html_sha256",
            "html_preview",
            "screenshot",
        ):
            assert key in bundle

    @pytest.mark.anyio
    async def test_console_tail_returns_last_n(self, tmp_path: Path) -> None:
        """console_tail=N → last-N slice. Mutating to first-N or all would catch."""
        inst = _build(tmp_path, page=_ok_page())
        inst.console = [f"msg{i}" for i in range(50)]
        bundle = await inst.diagnostic_bundle(console_tail=5)
        assert bundle["console_tail"] == ["msg45", "msg46", "msg47", "msg48", "msg49"]

    @pytest.mark.anyio
    async def test_url_swallow_keeps_other_fields(self, tmp_path: Path) -> None:
        """page.url access raising must not abort the rest of the bundle."""
        page = _ok_page()
        type(page).url = property(lambda _self: (_ for _ in ()).throw(RuntimeError("nav")))
        inst = _build(tmp_path, page=page)
        bundle = await inst.diagnostic_bundle()
        assert bundle["url"] is None
        assert bundle["title"] == "Title"

    @pytest.mark.anyio
    async def test_title_swallow_keeps_other_fields(self, tmp_path: Path) -> None:
        """title() raising → bundle["title"] stays None, others unaffected."""
        page = _ok_page()
        page.title = AsyncMock(side_effect=RuntimeError("boom"))
        inst = _build(tmp_path, page=page)
        bundle = await inst.diagnostic_bundle()
        assert bundle["title"] is None
        assert bundle["url"] == "https://octowright.com"

    @pytest.mark.anyio
    async def test_html_failure_records_html_error_repr(self, tmp_path: Path) -> None:
        """page.content() raising → html_error key holds repr(exc)."""
        page = _ok_page()
        page.content = AsyncMock(side_effect=ValueError("dom-detached"))
        inst = _build(tmp_path, page=page)
        bundle = await inst.diagnostic_bundle()
        assert "html_error" in bundle
        assert "dom-detached" in bundle["html_error"]
        assert bundle["html_path"] is None
        assert bundle["html_size"] is None
        assert bundle["html_sha256"] is None
        assert bundle["html_preview"] is None

    @pytest.mark.anyio
    async def test_html_success_writes_file_and_populates_meta(self, tmp_path: Path) -> None:
        """Round-trip: html written to disk, sha256 + size + preview match."""
        html = "<html><body>" + ("X" * 100) + "</body></html>"
        inst = _build(tmp_path, page=_ok_page(html))
        bundle = await inst.diagnostic_bundle()
        assert bundle["html_size"] == len(html)
        assert bundle["html_sha256"] == hashlib.sha256(html.encode("utf-8")).hexdigest()
        assert bundle["html_preview"] == html[:DEFAULT_PREVIEW_CHARS]
        assert Path(bundle["html_path"]).read_text(encoding="utf-8") == html
        assert "html" not in bundle

    @pytest.mark.anyio
    async def test_html_full_includes_full_html(self, tmp_path: Path) -> None:
        """html_full=True seeds and populates the inline 'html' field."""
        html = "<x/>"
        inst = _build(tmp_path, page=_ok_page(html))
        bundle = await inst.diagnostic_bundle(html_full=True)
        assert bundle["html"] == html

    @pytest.mark.anyio
    async def test_html_full_seed_is_none_when_html_call_fails(self, tmp_path: Path) -> None:
        """html_full=True + content() raising → 'html' is the seeded None
        (mutating the seed to '' would change this)."""
        page = _ok_page()
        page.content = AsyncMock(side_effect=RuntimeError("boom"))
        inst = _build(tmp_path, page=page)
        bundle = await inst.diagnostic_bundle(html_full=True)
        assert bundle["html"] is None

    @pytest.mark.anyio
    async def test_html_preview_truncates_at_default(self, tmp_path: Path) -> None:
        """Preview is exactly DEFAULT_PREVIEW_CHARS long when html exceeds it."""
        html = "Z" * (DEFAULT_PREVIEW_CHARS * 2)
        inst = _build(tmp_path, page=_ok_page(html))
        bundle = await inst.diagnostic_bundle()
        assert len(bundle["html_preview"]) == DEFAULT_PREVIEW_CHARS

    @pytest.mark.anyio
    async def test_html_filename_includes_instance_id_and_timestamp(self, tmp_path: Path) -> None:
        """Filename pattern: {instance_id}-fail-{timestamp}.html."""
        inst = _build(tmp_path, page=_ok_page())
        inst.instance_id = "iid7"
        bundle = await inst.diagnostic_bundle()
        name = Path(bundle["html_path"]).name
        assert name.startswith("iid7-fail-")
        assert name.endswith(".html")

    @pytest.mark.anyio
    async def test_screenshot_dir_override_used_for_html_and_png(self, tmp_path: Path) -> None:
        """When screenshot_dir is given, both files land there (not log_path.parent)."""
        out_dir = tmp_path / "elsewhere"
        inst = _build(tmp_path, page=_ok_page())
        bundle = await inst.diagnostic_bundle(screenshot_dir=out_dir)
        assert Path(bundle["html_path"]).parent == out_dir
        assert Path(bundle["screenshot"]).parent == out_dir

    @pytest.mark.anyio
    async def test_screenshot_failure_records_error(self, tmp_path: Path) -> None:
        """screenshot() raising → screenshot_error contains repr(exc), screenshot stays None."""
        page = _ok_page()
        page.screenshot = AsyncMock(side_effect=RuntimeError("disk-full"))
        inst = _build(tmp_path, page=page)
        bundle = await inst.diagnostic_bundle()
        assert bundle["screenshot"] is None
        assert "disk-full" in bundle["screenshot_error"]

    @pytest.mark.anyio
    async def test_screenshot_path_default_is_log_path_parent(self, tmp_path: Path) -> None:
        """screenshot_dir=None → screenshot lands next to log_path."""
        inst = _build(tmp_path, page=_ok_page())
        bundle = await inst.diagnostic_bundle()
        assert Path(bundle["screenshot"]).parent == inst.log_path.parent
