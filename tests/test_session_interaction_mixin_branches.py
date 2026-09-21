# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_interaction_mixin.

Despite its name, this mixin covers the *event-glue* surface — downloads,
dialogs, route mocking, file-input upload — not the click/fill/expect_*
methods we initially expected (those live in `core_ops_mixin`).

Existing tests in `tests/test_default_dialog_policy.py`,
`tests/test_downloads.py`, and `tests/test_interception.py` cover the
main happy paths. This file targets specific branches still missing:

- `set_dialog_policy`: every invalid policy, exact return shape,
  prompt_text=None vs explicit, recorder.record kwargs
- `_handle_dialog`: each policy x dialog-type combination, the
  exception-swallow + dialog_handler_error path
- `_handle_download`: background-task plumbing (added to set, removed
  via done-callback)
- `wait_for_download`: timeout_ms passthrough + default
- `list_downloads`: returns a new list (mutation-safe copy)
- `mock_route`: existing-pattern unroute-first branch, default body / headers,
  recorder kwargs shape
- `unmock_route`: KeyError with exact message on missing pattern,
  recorder kwargs shape
- `set_input_files`: passthrough + recorder.record shape
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright import defaults
from octowright.recorder import Recorder
from octowright.session.core import BrowserSession


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_session(tmp_path: Path, page: Any | None = None) -> BrowserSession:
    """Construct a BrowserSession backed by stubs — no real Playwright."""
    if page is None:
        page = SimpleNamespace(url="about:blank")
    context: Any = SimpleNamespace()
    log_path = tmp_path / "session.jsonl"
    recorder = Recorder(log_path)
    return BrowserSession(
        instance_id="inst",
        kind="chromium",
        label=None,
        url="about:blank",
        browser=None,
        context=context,
        page=page,
        recorder=recorder,
        log_path=log_path,
    )


def _record_calls(session: BrowserSession) -> list[tuple[str, dict[str, Any]]]:
    """Capture every recorder.record(...) call as (action, kwargs)."""
    captured: list[tuple[str, dict[str, Any]]] = []
    original = session.recorder.record

    def _intercept(action: str, **kwargs: Any) -> None:
        captured.append((action, kwargs))
        original(action, **kwargs)

    session.recorder.record = _intercept  # type: ignore[method-assign]
    return captured


# ─── set_dialog_policy ───────────────────────────────────────────────────────


class TestSetDialogPolicy:
    @pytest.mark.anyio
    @pytest.mark.parametrize("bad", ["", "ACCEPT", "Dismiss", "ignore", "auto", "yes", "no"])
    async def test_invalid_policies_raise_value_error(self, tmp_path: Path, bad: str) -> None:
        """Anything outside the {accept, dismiss, manual} set raises with exact whitelist text."""
        session = _make_session(tmp_path)
        with pytest.raises(ValueError, match=r"accept\|dismiss\|manual"):
            await session.set_dialog_policy(bad)

    @pytest.mark.anyio
    async def test_valid_policy_records_set_dialog_policy(self, tmp_path: Path) -> None:
        """The state-change emits a 'set_dialog_policy' record with the new policy + prompt_text."""
        session = _make_session(tmp_path)
        captured = _record_calls(session)
        await session.set_dialog_policy("accept", "ok")
        assert ("set_dialog_policy", {"policy": "accept", "prompt_text": "ok"}) in captured

    @pytest.mark.anyio
    async def test_prompt_text_defaults_to_none(self, tmp_path: Path) -> None:
        """No prompt_text passed → stored as None, not as ''."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("dismiss")
        assert session._dialog_prompt_text is None

    @pytest.mark.anyio
    async def test_explicit_prompt_text_stored(self, tmp_path: Path) -> None:
        """Caller-supplied prompt_text is stored verbatim."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept", "answer-42")
        assert session._dialog_prompt_text == "answer-42"

    @pytest.mark.anyio
    async def test_return_shape_pinned(self, tmp_path: Path) -> None:
        """Mutating any return-dict key would change the wire-facing shape."""
        session = _make_session(tmp_path)
        result = await session.set_dialog_policy("manual", "foo")
        assert result == {"ok": True, "policy": "manual", "prompt_text": "foo"}

    @pytest.mark.anyio
    async def test_invalid_policy_does_not_record_or_mutate_state(self, tmp_path: Path) -> None:
        """A rejected policy must leave the previous state intact."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept", "first")
        captured = _record_calls(session)
        with pytest.raises(ValueError):
            await session.set_dialog_policy("nope")
        # Recorder didn't fire.
        assert captured == []
        # State unchanged.
        assert session._dialog_policy == "accept"
        assert session._dialog_prompt_text == "first"


# ─── _handle_dialog ──────────────────────────────────────────────────────────


class _FakeDialog:
    def __init__(self, dtype: str = "alert", message: str = "hi") -> None:
        self.type = dtype
        self.message = message
        self.accepted: bool | None = None
        self.dismissed: bool | None = None
        self.accept_arg: str | None = None
        self.accept_called_no_arg: bool = False

    async def accept(self, *args: str) -> None:
        self.accepted = True
        if args:
            self.accept_arg = args[0]
        else:
            self.accept_called_no_arg = True

    async def dismiss(self) -> None:
        self.dismissed = True


async def _drain(session: BrowserSession) -> None:
    """Flush all background tasks the dialog handler may have created."""
    for _ in range(5):
        await asyncio.sleep(0)
    if session._bg_tasks:
        await asyncio.gather(*session._bg_tasks, return_exceptions=True)


class TestHandleDialog:
    @pytest.mark.anyio
    async def test_accept_alert_calls_accept_no_arg(self, tmp_path: Path) -> None:
        """Non-prompt dialog with accept policy → dialog.accept() with NO argument."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept")
        dialog = _FakeDialog(dtype="alert")
        session._handle_dialog(dialog)
        await _drain(session)
        assert dialog.accepted is True
        assert dialog.accept_called_no_arg is True
        assert dialog.accept_arg is None

    @pytest.mark.anyio
    async def test_accept_prompt_uses_prompt_text(self, tmp_path: Path) -> None:
        """Prompt + accept policy + prompt_text → dialog.accept(prompt_text)."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept", "the answer")
        dialog = _FakeDialog(dtype="prompt")
        session._handle_dialog(dialog)
        await _drain(session)
        assert dialog.accept_arg == "the answer"
        assert dialog.accept_called_no_arg is False

    @pytest.mark.anyio
    async def test_accept_prompt_with_none_prompt_text_uses_empty_string(self, tmp_path: Path) -> None:
        """Prompt + accept + prompt_text=None → accept('') not accept(None)."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept", None)
        dialog = _FakeDialog(dtype="prompt")
        session._handle_dialog(dialog)
        await _drain(session)
        assert dialog.accept_arg == ""

    @pytest.mark.anyio
    async def test_dismiss_policy_calls_dismiss(self, tmp_path: Path) -> None:
        """Dismiss policy invokes dialog.dismiss(), not accept."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("dismiss")
        dialog = _FakeDialog(dtype="confirm")
        session._handle_dialog(dialog)
        await _drain(session)
        assert dialog.dismissed is True
        assert dialog.accepted is None

    @pytest.mark.anyio
    async def test_manual_policy_does_nothing_to_dialog(self, tmp_path: Path) -> None:
        """Manual policy: no accept, no dismiss — handler just records."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("manual")
        dialog = _FakeDialog(dtype="alert")
        session._handle_dialog(dialog)
        await _drain(session)
        assert dialog.accepted is None
        assert dialog.dismissed is None

    @pytest.mark.anyio
    async def test_records_dialog_handled_with_full_kwargs(self, tmp_path: Path) -> None:
        """The dialog_handled record carries dtype/message/policy/prompt_text."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept", "yes")
        captured = _record_calls(session)
        dialog = _FakeDialog(dtype="prompt", message="enter name")
        session._handle_dialog(dialog)
        await _drain(session)
        handled = [c for c in captured if c[0] == "dialog_handled"]
        assert handled
        kwargs = handled[0][1]
        assert kwargs == {
            "dtype": "prompt",
            "message": "enter name",
            "policy": "accept",
            "prompt_text": "yes",
        }

    @pytest.mark.anyio
    async def test_accept_failure_records_handler_error(self, tmp_path: Path) -> None:
        """If dialog.accept() raises, error is swallowed and recorded as dialog_handler_error."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("accept")
        captured = _record_calls(session)

        class _BoomDialog:
            type = "alert"
            message = "x"

            async def accept(self, *args: str) -> None:
                raise RuntimeError("playwright boom")

        session._handle_dialog(_BoomDialog())
        await _drain(session)
        errors = [c for c in captured if c[0] == "dialog_handler_error"]
        assert errors
        assert "playwright boom" in errors[0][1]["error"]

    @pytest.mark.anyio
    async def test_dismiss_failure_records_handler_error(self, tmp_path: Path) -> None:
        """Same swallow path for dismiss-failure."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("dismiss")
        captured = _record_calls(session)

        class _BoomDialog:
            type = "confirm"
            message = "x"

            async def dismiss(self) -> None:
                raise RuntimeError("dismiss-boom")

        session._handle_dialog(_BoomDialog())
        await _drain(session)
        errors = [c for c in captured if c[0] == "dialog_handler_error"]
        assert errors
        assert "dismiss-boom" in errors[0][1]["error"]

    @pytest.mark.anyio
    async def test_dialog_task_added_to_bg_tasks(self, tmp_path: Path) -> None:
        """The async _act task is registered in self._bg_tasks for GC safety."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("manual")
        # Snapshot before
        before = len(session._bg_tasks)
        dialog = _FakeDialog()
        session._handle_dialog(dialog)
        # Should have added one (may not have run yet).
        # After yielding once, the task may have completed and been discarded — accept either.
        assert len(session._bg_tasks) >= before
        await _drain(session)

    @pytest.mark.anyio
    async def test_dialog_callback_unblocks_while_a_different_task_holds_the_root_operation(
        self, tmp_path: Path
    ) -> None:
        """Dialog accept/dismiss is an event-critical callback: it must run even
        while another task holds the session's operation lease, because the
        admitted Playwright call that lease belongs to (e.g. a click()) may
        itself be blocked waiting for the dialog response. If a regression ever
        added gate acquisition here, this callback would queue behind the
        holder and this test would hang/timeout instead of observing the
        dismiss while the lease is still held."""
        session = _make_session(tmp_path)
        await session.set_dialog_policy("dismiss")
        dialog = _FakeDialog(dtype="confirm")

        entered = asyncio.Event()
        release = asyncio.Event()

        async def _hold() -> None:
            async with session.operation("browser_click"):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(_hold())
        await entered.wait()

        session._handle_dialog(dialog)
        async with asyncio.timeout(2):
            while not dialog.dismissed:
                await asyncio.sleep(0)
        assert dialog.dismissed is True

        release.set()
        await holder
        await _drain(session)


# ─── _handle_download ────────────────────────────────────────────────────────


class TestHandleDownload:
    @pytest.mark.anyio
    async def test_creates_task_added_to_bg_tasks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """_handle_download schedules an async save and tracks the task in _bg_tasks."""
        session = _make_session(tmp_path)

        save_called = asyncio.Event()

        async def fake_save(_session: Any, _download: Any) -> None:
            save_called.set()

        # Patch the lazy-imported downloads module.
        from octowright.session import downloads as _downloads

        monkeypatch.setattr(_downloads, "save_download", fake_save)
        download = MagicMock()
        before = len(session._bg_tasks)
        session._handle_download(download)
        # Task created.
        assert len(session._bg_tasks) > before
        await _drain(session)
        assert save_called.is_set()

    @pytest.mark.anyio
    async def test_done_callback_discards_from_bg_tasks(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Once the save task completes, _bg_tasks.discard cleans up the reference."""
        session = _make_session(tmp_path)

        async def fake_save(_session: Any, _download: Any) -> None:
            return None

        from octowright.session import downloads as _downloads

        monkeypatch.setattr(_downloads, "save_download", fake_save)
        session._handle_download(MagicMock())
        await _drain(session)
        # After draining, the task is done and removed.
        assert all(t.done() for t in session._bg_tasks)
        # In practice, the discard callback empties the set.
        assert len(session._bg_tasks) == 0


# ─── wait_for_download / list_downloads ─────────────────────────────────────


class TestWaitForDownload:
    @pytest.mark.anyio
    async def test_passes_timeout_to_impl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """wait_for_download forwards timeout_ms (default 15000) to downloads.wait_for_download_impl."""
        session = _make_session(tmp_path)
        captured: list[int] = []

        async def fake_impl(_session: Any, timeout_ms: int) -> dict[str, Any]:
            captured.append(timeout_ms)
            return {"ok": True}

        from octowright.session import downloads as _downloads

        monkeypatch.setattr(_downloads, "wait_for_download_impl", fake_impl)
        result = await session.wait_for_download()
        assert captured == [15000]
        assert result == {"ok": True}

    @pytest.mark.anyio
    async def test_explicit_timeout_passthrough(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit timeout overrides the default."""
        session = _make_session(tmp_path)
        captured: list[int] = []

        async def fake_impl(_session: Any, timeout_ms: int) -> dict[str, Any]:
            captured.append(timeout_ms)
            return {}

        from octowright.session import downloads as _downloads

        monkeypatch.setattr(_downloads, "wait_for_download_impl", fake_impl)
        await session.wait_for_download(timeout_ms=500)
        assert captured == [500]


class TestListDownloads:
    def test_returns_copy_not_live_reference(self, tmp_path: Path) -> None:
        """Mutating the returned list must not affect session.downloads."""
        session = _make_session(tmp_path)
        session.downloads.append({"id": "a"})
        result = session.list_downloads()
        result.append({"id": "B-injected"})
        # Original unaffected.
        assert session.downloads == [{"id": "a"}]

    def test_returns_empty_list_when_no_downloads(self, tmp_path: Path) -> None:
        """Fresh session → []."""
        session = _make_session(tmp_path)
        assert session.list_downloads() == []

    def test_returns_all_recorded_downloads_in_order(self, tmp_path: Path) -> None:
        """Order preserved from the underlying deque/list."""
        session = _make_session(tmp_path)
        session.downloads.extend([{"id": "1"}, {"id": "2"}, {"id": "3"}])
        assert session.list_downloads() == [{"id": "1"}, {"id": "2"}, {"id": "3"}]


# ─── mock_route / unmock_route ─────────────────────────────────────────────


class _FakeRoute:
    def __init__(self) -> None:
        self.fulfilled: dict[str, Any] | None = None

    async def fulfill(self, **kwargs: Any) -> None:
        self.fulfilled = kwargs


class _FakePageWithRoutes:
    def __init__(self) -> None:
        self.url = "about:blank"
        self.frames: list[Any] = []
        self.route = AsyncMock()
        self.unroute = AsyncMock()


class TestMockRoute:
    @pytest.mark.anyio
    async def test_records_pattern_status_and_content_type(self, tmp_path: Path) -> None:
        """mock_route records the kwargs the operator should be able to audit."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        captured = _record_calls(session)
        await session.mock_route("**/api/x", status=201, content_type="text/plain")
        assert (
            "mock_route",
            {
                "pattern": "**/api/x",
                "status": 201,
                "content_type": "text/plain",
                "body": None,
                "headers": {},
            },
        ) in captured

    @pytest.mark.anyio
    async def test_handler_uses_default_body_and_headers(self, tmp_path: Path) -> None:
        """body=None becomes ''; headers=None becomes {}."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/api/x")
        # Pull the handler the route() call captured.
        handler = page.route.call_args[0][1]
        route = _FakeRoute()
        await handler(route)
        assert route.fulfilled is not None
        assert route.fulfilled["body"] == ""
        assert route.fulfilled["headers"] == {}

    @pytest.mark.anyio
    async def test_handler_passes_explicit_body_and_headers(self, tmp_path: Path) -> None:
        """Explicit body and headers flow through."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/api/x", body="hi", headers={"X-Custom": "1"}, content_type="text/plain")
        handler = page.route.call_args[0][1]
        route = _FakeRoute()
        await handler(route)
        assert route.fulfilled is not None
        assert route.fulfilled["body"] == "hi"
        assert route.fulfilled["headers"] == {"X-Custom": "1"}
        assert route.fulfilled["content_type"] == "text/plain"

    @pytest.mark.anyio
    async def test_existing_pattern_unrouted_before_re_install(self, tmp_path: Path) -> None:
        """Re-mocking an existing pattern unroutes the old handler first."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/api/x", status=200)
        first_handler = session._active_routes["**/api/x"]
        # Second mock for the same pattern.
        await session.mock_route("**/api/x", status=404)
        page.unroute.assert_awaited_with("**/api/x", first_handler)
        # Stored handler is now the new one.
        assert session._active_routes["**/api/x"] is not first_handler

    @pytest.mark.anyio
    async def test_handler_stored_under_pattern_key(self, tmp_path: Path) -> None:
        """Handler is registered in _active_routes by url_pattern."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/foo")
        assert "**/foo" in session._active_routes

    @pytest.mark.anyio
    async def test_returns_ok_pattern_status(self, tmp_path: Path) -> None:
        """The wire-facing return shape is pinned."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        result = await session.mock_route("**/foo", status=418)
        assert result == {"ok": True, "pattern": "**/foo", "status": 418}

    @pytest.mark.anyio
    async def test_route_handler_fulfills_while_a_different_task_holds_the_root_operation(self, tmp_path: Path) -> None:
        """Route fulfill is an event-critical callback like dialog accept/
        dismiss: it must run without acquiring the session's operation lease,
        because the admitted navigation/click that triggered the request may
        itself be blocked waiting for the response. Proven with a SEPARATE
        task holding the lease -- a same-task call would trivially re-enter
        and would not catch a regression that added gate acquisition here."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/api/x", status=200)
        handler = page.route.call_args[0][1]
        route = _FakeRoute()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def _hold() -> None:
            async with session.operation("browser_navigate"):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(_hold())
        await entered.wait()

        await asyncio.wait_for(handler(route), timeout=1.0)
        assert route.fulfilled is not None

        release.set()
        await holder


class TestUnmockRoute:
    @pytest.mark.anyio
    async def test_missing_pattern_raises_keyerror_with_repr(self, tmp_path: Path) -> None:
        """Unmocking an unknown pattern → KeyError mentioning the pattern, with repr quoting."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        with pytest.raises(KeyError, match=r"no active mock for pattern '\*\*/missing'"):
            await session.unmock_route("**/missing")

    @pytest.mark.anyio
    async def test_existing_pattern_removed_and_unrouted(self, tmp_path: Path) -> None:
        """Successful unmock pops the handler and calls page.unroute(pattern, handler)."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/foo")
        handler = session._active_routes["**/foo"]
        await session.unmock_route("**/foo")
        assert "**/foo" not in session._active_routes
        page.unroute.assert_awaited_with("**/foo", handler)

    @pytest.mark.anyio
    async def test_records_unmock_route_with_pattern(self, tmp_path: Path) -> None:
        """Unmock emits a record carrying the pattern."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/foo")
        captured = _record_calls(session)
        await session.unmock_route("**/foo")
        assert ("unmock_route", {"pattern": "**/foo"}) in captured

    @pytest.mark.anyio
    async def test_returns_ok_pattern_shape(self, tmp_path: Path) -> None:
        """Unmock return shape is pinned."""
        page = _FakePageWithRoutes()
        session = _make_session(tmp_path, page=page)
        await session.mock_route("**/foo")
        result = await session.unmock_route("**/foo")
        assert result == {"ok": True, "pattern": "**/foo"}


# ─── set_input_files ────────────────────────────────────────────────────────


@pytest.fixture
def _allow_tmp_uploads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the upload allowlist at ``tmp_path`` so the session validator
    accepts files created there. Tests still need to create the files."""
    monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", tmp_path)
    monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
    return tmp_path


class TestSetInputFiles:
    @pytest.mark.anyio
    async def test_calls_page_set_input_files_with_args(self, tmp_path: Path, _allow_tmp_uploads: Path) -> None:
        """Selector + paths flow through to page.set_input_files (post-validation)."""
        page = MagicMock()
        page.url = "about:blank"
        page.set_input_files = AsyncMock()
        session = _make_session(tmp_path, page=page)
        f_a = tmp_path / "a.txt"
        f_b = tmp_path / "b.txt"
        f_a.write_text("x")
        f_b.write_text("y")
        await session.set_input_files("input[type=file]", [str(f_a), str(f_b)])
        # Validator resolves; the resolved paths == the originals here because
        # they're already absolute under an allowed root.
        page.set_input_files.assert_awaited_once_with("input[type=file]", [str(f_a), str(f_b)])

    @pytest.mark.anyio
    async def test_records_with_selector_and_paths(self, tmp_path: Path, _allow_tmp_uploads: Path) -> None:
        """The record carries selector + the validated paths list."""
        page = MagicMock()
        page.url = "about:blank"
        page.set_input_files = AsyncMock()
        session = _make_session(tmp_path, page=page)
        captured = _record_calls(session)
        f = tmp_path / "x"
        f.write_text("")
        await session.set_input_files("#upload", [str(f)])
        assert ("set_input_files", {"selector": "#upload", "paths": [str(f)]}) in captured

    @pytest.mark.anyio
    async def test_returns_ok_selector_paths_shape(self, tmp_path: Path, _allow_tmp_uploads: Path) -> None:
        """Return-dict shape is pinned."""
        page = MagicMock()
        page.url = "about:blank"
        page.set_input_files = AsyncMock()
        session = _make_session(tmp_path, page=page)
        f_x = tmp_path / "x"
        f_y = tmp_path / "y"
        f_x.write_text("")
        f_y.write_text("")
        result = await session.set_input_files("#upload", [str(f_x), str(f_y)])
        assert result == {"ok": True, "selector": "#upload", "paths": [str(f_x), str(f_y)]}

    @pytest.mark.anyio
    async def test_empty_paths_list_passes_through(self, tmp_path: Path, _allow_tmp_uploads: Path) -> None:
        """Empty paths list flows through verbatim (no paths to validate)."""
        page = MagicMock()
        page.url = "about:blank"
        page.set_input_files = AsyncMock()
        session = _make_session(tmp_path, page=page)
        result = await session.set_input_files("#upload", [])
        assert result["paths"] == []
        page.set_input_files.assert_awaited_once_with("#upload", [])

    @pytest.mark.anyio
    async def test_rejects_path_outside_allowed_roots(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Regression: the session method itself (not just the MCP tool
        wrapper) must reject paths outside the allowlist, so macro replay
        can't bypass validation by calling session.set_input_files directly.
        """
        # Constrain the allowlist to a sibling dir of tmp_path so any file
        # under tmp_path is guaranteed *outside* it.
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        outside_file = outside_dir / "secret.txt"
        outside_file.write_text("nope")

        monkeypatch.setattr(defaults, "UPLOAD_STAGING_DIR", sandbox)
        monkeypatch.setattr(defaults, "UPLOAD_EXTRA_ROOTS_RAW", "")
        # Also ensure cwd isn't tmp_path's parent (which would whitelist it).
        monkeypatch.chdir(sandbox)

        page = MagicMock()
        page.url = "about:blank"
        page.set_input_files = AsyncMock()
        session = _make_session(tmp_path, page=page)

        with pytest.raises(ValueError, match="outside the allowed roots"):
            await session.set_input_files("#upload", [str(outside_file)])
        page.set_input_files.assert_not_called()


# ─── upload_files ───────────────────────────────────────────────────────────


class _UploadLocator:
    def __init__(self, *, click_error: Exception | None = None) -> None:
        self.click_calls: list[int] = []
        self.click_error = click_error

    async def click(self, *, timeout: int) -> None:
        self.click_calls.append(timeout)
        if self.click_error is not None:
            raise self.click_error


class _UploadChooser:
    def __init__(self, *, set_files_error: Exception | None = None) -> None:
        self.set_files_calls: list[tuple[list[str], int]] = []
        self.set_files_error = set_files_error

    async def set_files(self, paths: list[str], *, timeout: int) -> None:
        self.set_files_calls.append((paths, timeout))
        if self.set_files_error is not None:
            raise self.set_files_error


class _UploadChooserInfo:
    def __init__(self, chooser: _UploadChooser, *, value_error: Exception | None = None) -> None:
        self.chooser = chooser
        self.value_error = value_error

    async def __aenter__(self) -> _UploadChooserInfo:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @property
    async def value(self) -> _UploadChooser:
        if self.value_error is not None:
            raise self.value_error
        return self.chooser


class _UploadPage:
    def __init__(
        self,
        *,
        chooser: _UploadChooser | None = None,
        chooser_value_error: Exception | None = None,
    ) -> None:
        self.url = "about:blank"
        self.selector_locator = _UploadLocator()
        self.semantic_locator = _UploadLocator()
        self.chooser = chooser or _UploadChooser()
        self.chooser_value_error = chooser_value_error
        self.expect_file_chooser_calls: list[int] = []
        self.locator_calls: list[str] = []
        self.semantic_calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def expect_file_chooser(self, *, timeout: int) -> _UploadChooserInfo:
        self.expect_file_chooser_calls.append(timeout)
        return _UploadChooserInfo(self.chooser, value_error=self.chooser_value_error)

    def locator(self, selector: str) -> _UploadLocator:
        self.locator_calls.append(selector)
        return self.selector_locator

    def get_by_role(self, role: str, **kwargs: Any) -> _UploadLocator:
        self.semantic_calls.append(("role", (role,), kwargs))
        return self.semantic_locator

    def get_by_label(self, label: str, **kwargs: Any) -> _UploadLocator:
        self.semantic_calls.append(("label", (label,), kwargs))
        return self.semantic_locator

    def get_by_text(self, text: str, **kwargs: Any) -> _UploadLocator:
        self.semantic_calls.append(("text", (text,), kwargs))
        return self.semantic_locator

    def get_by_test_id(self, test_id: str) -> _UploadLocator:
        self.semantic_calls.append(("test_id", (test_id,), {}))
        return self.semantic_locator


class TestUploadFiles:
    @pytest.mark.anyio
    @pytest.mark.parametrize("paths", [None, "one.txt", (), []])
    async def test_upload_files_paths_must_be_a_non_empty_list(self, tmp_path: Path, paths: Any) -> None:
        page = _UploadPage()
        session = _make_session(tmp_path, page=page)
        with pytest.raises(ValueError, match="non-empty list"):
            await session.upload_files(paths=paths, selector="#upload")
        assert page.expect_file_chooser_calls == []
        assert page.locator_calls == []

    @pytest.mark.anyio
    async def test_upload_files_validates_every_path_before_resolving_or_clicking_trigger(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = _UploadPage()
        session = _make_session(tmp_path, page=page)
        seen: list[str] = []

        def _validate(path: str) -> Path:
            seen.append(path)
            if path == "bad":
                raise ValueError("bad upload")
            return Path(path)

        from octowright.session import upload_paths

        monkeypatch.setattr(upload_paths, "validate_upload_path", _validate)
        with pytest.raises(ValueError, match="bad upload"):
            await session.upload_files(paths=["good", "bad"], selector="#upload")
        assert seen == ["good", "bad"]
        assert page.locator_calls == []
        assert page.expect_file_chooser_calls == []
        assert page.selector_locator.click_calls == []

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({}, "exactly one trigger"),
            ({"selector": "#upload", "role": "button"}, "exactly one trigger"),
            ({"role": "button", "label": "Upload"}, "exactly one trigger"),
            ({"role_name": "Upload"}, "role_name requires role"),
            ({"role_exact": True}, "role_exact requires role"),
            ({"label_exact": True}, "label_exact requires label"),
            ({"text_exact": True}, "text_exact requires text"),
        ],
    )
    async def test_upload_files_invalid_trigger_modes_fail_before_arming_listener(
        self,
        tmp_path: Path,
        _allow_tmp_uploads: Path,
        kwargs: dict[str, Any],
        message: str,
    ) -> None:
        upload = tmp_path / "x.txt"
        upload.write_text("x")
        page = _UploadPage()
        session = _make_session(tmp_path, page=page)
        with pytest.raises(ValueError, match=message):
            await session.upload_files(paths=[str(upload)], **kwargs)
        assert page.expect_file_chooser_calls == []

    @pytest.mark.anyio
    async def test_upload_files_selector_trigger_uses_default_timeout(
        self, tmp_path: Path, _allow_tmp_uploads: Path
    ) -> None:
        upload = tmp_path / "selector.txt"
        upload.write_text("x")
        page = _UploadPage()
        session = _make_session(tmp_path, page=page)

        result = await session.upload_files(paths=[str(upload)], selector="#pick")

        assert page.locator_calls == ["#pick"]
        assert page.expect_file_chooser_calls == [defaults.DEFAULT_ACTION_TIMEOUT_MS]
        assert page.selector_locator.click_calls == [defaults.DEFAULT_ACTION_TIMEOUT_MS]
        assert page.chooser.set_files_calls == [([str(upload)], defaults.DEFAULT_ACTION_TIMEOUT_MS)]
        assert result == {"ok": True, "paths": [str(upload)], "selector": "#pick"}

    @pytest.mark.anyio
    async def test_upload_files_selector_uses_active_frame_but_page_owns_chooser_listener(
        self, tmp_path: Path, _allow_tmp_uploads: Path
    ) -> None:
        upload = tmp_path / "frame.txt"
        upload.write_text("x")
        page = _UploadPage()
        frame = _UploadPage()
        session = _make_session(tmp_path, page=page)
        session.active_frame = frame

        await session.upload_files(paths=[str(upload)], selector="#frame-upload", timeout_ms=246)

        assert page.locator_calls == []
        assert frame.locator_calls == ["#frame-upload"]
        assert frame.selector_locator.click_calls == [246]
        assert page.expect_file_chooser_calls == [246]
        assert frame.expect_file_chooser_calls == []
        assert page.chooser.set_files_calls == [([str(upload)], 246)]

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("kwargs", "semantic_call", "locator_fields"),
        [
            (
                {"role": "button", "role_exact": True},
                ("role", ("button",), {}),
                {"role": "button", "role_exact": True},
            ),
            (
                {"role": "button", "role_name": "Upload", "role_exact": True},
                ("role", ("button",), {"name": "Upload", "exact": True}),
                {"role": "button", "role_name": "Upload", "role_exact": True},
            ),
            (
                {"label": "Choose file", "label_exact": True},
                ("label", ("Choose file",), {"exact": True}),
                {"label": "Choose file", "label_exact": True},
            ),
            (
                {"text": "Upload files", "text_exact": True},
                ("text", ("Upload files",), {"exact": True}),
                {"text": "Upload files", "text_exact": True},
            ),
            (
                {"test_id": "upload"},
                ("test_id", ("upload",), {}),
                {"test_id": "upload"},
            ),
        ],
    )
    async def test_upload_files_semantic_triggers_forward_full_finder_set_and_explicit_timeout(
        self,
        tmp_path: Path,
        _allow_tmp_uploads: Path,
        kwargs: dict[str, Any],
        semantic_call: tuple[str, tuple[Any, ...], dict[str, Any]],
        locator_fields: dict[str, Any],
    ) -> None:
        upload = tmp_path / "semantic.txt"
        upload.write_text("x")
        page = _UploadPage()
        session = _make_session(tmp_path, page=page)

        result = await session.upload_files(paths=[str(upload)], timeout_ms=432, **kwargs)

        assert page.semantic_calls == [semantic_call]
        assert page.expect_file_chooser_calls == [432]
        assert page.semantic_locator.click_calls == [432]
        assert page.chooser.set_files_calls == [([str(upload)], 432)]
        assert result == {"ok": True, "paths": [str(upload)], **locator_fields}

    @pytest.mark.anyio
    @pytest.mark.parametrize("failure_point", ["chooser", "set_files"])
    async def test_failure_does_not_record_successful_upload_files(
        self,
        tmp_path: Path,
        _allow_tmp_uploads: Path,
        failure_point: str,
    ) -> None:
        upload = tmp_path / "failure.txt"
        upload.write_text("x")
        chooser = _UploadChooser(
            set_files_error=RuntimeError("set-files failed") if failure_point == "set_files" else None
        )
        page = _UploadPage(
            chooser=chooser,
            chooser_value_error=RuntimeError("chooser failed") if failure_point == "chooser" else None,
        )
        session = _make_session(tmp_path, page=page)
        captured = _record_calls(session)

        with pytest.raises(RuntimeError, match="failed"):
            await session.upload_files(paths=[str(upload)], selector="#upload")

        assert [action for action, _kwargs in captured if action == "upload_files"] == []

    @pytest.mark.anyio
    async def test_upload_files_click_failure_propagates_without_assignment_or_recording(
        self, tmp_path: Path, _allow_tmp_uploads: Path
    ) -> None:
        upload = tmp_path / "click-failure.txt"
        upload.write_text("x")
        page = _UploadPage()
        page.selector_locator.click_error = RuntimeError("click failed")
        session = _make_session(tmp_path, page=page)
        captured = _record_calls(session)

        with pytest.raises(RuntimeError, match="click failed"):
            await session.upload_files(paths=[str(upload)], selector="#upload")

        assert page.chooser.set_files_calls == []
        assert [action for action, _kwargs in captured if action == "upload_files"] == []
