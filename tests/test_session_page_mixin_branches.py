# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Branch-targeted tests for octowright.session.core_page_mixin.

Existing `tests/test_multitab.py` covers list_pages / switch_page /
close_page basics. This file pins everything else in the mixin:

- navigate: _last_mcp_navigation set, page.goto called with default
  timeout, title fetched, URL stored, markdown capture scheduled, recorder.
- _resolve_semantic_metadata: aria-snapshot parse with name, without name,
  malformed snapshot, locator failure.
- click / type_text / fill / press_key: recorder.record kwargs include
  meta + selector + delay_ms (including None default).
- screenshot: parent-dir creation, page.screenshot called with str(path).
- snapshot: aria_snapshot on html locator, return shape.
- evaluate: result returned, recorder records expression.
- wait_for: selector / text / network-idle branches + timeout fallback.
- expect_url / expect_text / expect_selector / expect_js: three modes,
  exact error formats, unknown-mode → ValueError, recorder emit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS, DEFAULT_NAV_TIMEOUT_MS
from octowright.session.core_page_mixin import SessionPageMixin


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_subject(tmp_path: Path) -> SessionPageMixin:
    """Build a SessionPageMixin instance with the attributes navigate/* read."""
    subj = SessionPageMixin.__new__(SessionPageMixin)
    subj._last_mcp_navigation = None
    page = MagicMock()
    page.url = "https://octowright.com"
    page.goto = AsyncMock()
    page.title = AsyncMock(return_value="Example")
    page.content = AsyncMock(return_value="<html></html>")
    page.screenshot = AsyncMock()
    page.evaluate = AsyncMock(return_value=None)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.locator = MagicMock()
    page.wait_for_selector = AsyncMock()
    page.query_selector = AsyncMock()
    page.wait_for_function = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    subj.page = page
    subj.pages = [page]
    subj.url = None
    subj.recorder = MagicMock()
    subj.recorder.record = MagicMock()
    subj._target = lambda: subj.page  # type: ignore[attr-defined]
    subj._schedule_markdown_capture = MagicMock()  # type: ignore[attr-defined]
    return subj


# ─── navigate ────────────────────────────────────────────────────────────────


class TestNavigate:
    @pytest.mark.anyio
    async def test_sets_last_mcp_navigation_first(self, tmp_path: Path) -> None:
        """`_last_mcp_navigation = url` runs BEFORE page.goto so the framenavigated
        listener can deduplicate."""
        subj = _make_subject(tmp_path)

        async def _check_set_before_goto(*_args: Any, **_kw: Any) -> None:
            assert subj._last_mcp_navigation == "https://target.com"

        subj.page.goto = AsyncMock(side_effect=_check_set_before_goto)
        await subj.navigate("https://target.com")

    @pytest.mark.anyio
    async def test_calls_page_goto_with_default_timeout(self, tmp_path: Path) -> None:
        """page.goto is called with timeout=DEFAULT_NAV_TIMEOUT_MS."""
        subj = _make_subject(tmp_path)
        await subj.navigate("https://target.com")
        subj.page.goto.assert_awaited_once_with("https://target.com", timeout=DEFAULT_NAV_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_returns_url_and_title(self, tmp_path: Path) -> None:
        """Return dict contains url + title fetched from page."""
        subj = _make_subject(tmp_path)
        subj.page.title = AsyncMock(return_value="Target")
        result = await subj.navigate("https://target.com")
        assert result == {"url": "https://target.com", "title": "Target"}

    @pytest.mark.anyio
    async def test_records_navigate_event(self, tmp_path: Path) -> None:
        """recorder.record('navigate', url=...) emitted."""
        subj = _make_subject(tmp_path)
        await subj.navigate("https://target.com")
        subj.recorder.record.assert_any_call("navigate", url="https://target.com")

    @pytest.mark.anyio
    async def test_schedules_markdown_capture(self, tmp_path: Path) -> None:
        """_schedule_markdown_capture called after navigation succeeds."""
        subj = _make_subject(tmp_path)
        await subj.navigate("https://target.com")
        subj._schedule_markdown_capture.assert_called_once()

    @pytest.mark.anyio
    async def test_self_url_assigned(self, tmp_path: Path) -> None:
        """self.url stores the navigated URL (drives later browser_list reporting)."""
        subj = _make_subject(tmp_path)
        await subj.navigate("https://target.com")
        assert subj.url == "https://target.com"

    @pytest.mark.anyio
    async def test_goto_failure_propagates(self, tmp_path: Path) -> None:
        """page.goto raising propagates — caller can wrap with diagnostic info."""
        subj = _make_subject(tmp_path)
        subj.page.goto = AsyncMock(side_effect=RuntimeError("nav failed"))
        with pytest.raises(RuntimeError, match=r"nav failed"):
            await subj.navigate("https://x")


# ─── _resolve_semantic_metadata ─────────────────────────────────────────────


class TestResolveSemanticMetadata:
    @pytest.mark.anyio
    async def test_parses_role_and_role_name(self, tmp_path: Path) -> None:
        """`- button "Save"` → {role: 'button', role_name: 'Save'}."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value='- button "Save"')
        subj._target = lambda: MagicMock(locator=lambda _s: loc)  # type: ignore[attr-defined]
        meta = await subj._resolve_semantic_metadata("#submit")
        assert meta == {"role": "button", "role_name": "Save"}

    @pytest.mark.anyio
    async def test_no_name_yields_empty_role_name(self, tmp_path: Path) -> None:
        """`- button` (no quoted name) → role only, role_name=''."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value="- button")
        subj._target = lambda: MagicMock(locator=lambda _s: loc)  # type: ignore[attr-defined]
        meta = await subj._resolve_semantic_metadata("#submit")
        assert meta == {"role": "button", "role_name": ""}

    @pytest.mark.anyio
    async def test_aria_snapshot_failure_returns_empty(self, tmp_path: Path) -> None:
        """Playwright errors during aria_snapshot → {} (never raises)."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(side_effect=RuntimeError("playwright"))
        subj._target = lambda: MagicMock(locator=lambda _s: loc)  # type: ignore[attr-defined]
        assert await subj._resolve_semantic_metadata("#submit") == {}

    @pytest.mark.anyio
    async def test_empty_snapshot_returns_empty(self, tmp_path: Path) -> None:
        """Empty aria_snapshot → no parse, returns {}."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value="")
        subj._target = lambda: MagicMock(locator=lambda _s: loc)  # type: ignore[attr-defined]
        assert await subj._resolve_semantic_metadata("#submit") == {}

    @pytest.mark.anyio
    async def test_no_dash_prefix_returns_empty(self, tmp_path: Path) -> None:
        """Snapshot without `- ` prefix → unparsable, {}."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value='button "Save"')
        subj._target = lambda: MagicMock(locator=lambda _s: loc)  # type: ignore[attr-defined]
        assert await subj._resolve_semantic_metadata("#submit") == {}

    @pytest.mark.anyio
    async def test_strips_trailing_quote(self, tmp_path: Path) -> None:
        """Trailing `"` is stripped from the role_name."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value='- link "Click here"')
        subj._target = lambda: MagicMock(locator=lambda _s: loc)  # type: ignore[attr-defined]
        meta = await subj._resolve_semantic_metadata("a#x")
        assert meta == {"role": "link", "role_name": "Click here"}


# ─── click / type_text / fill / press_key ─────────────────────────────────


class TestActions:
    @pytest.mark.anyio
    async def test_click_calls_target_click_with_default_timeout(self, tmp_path: Path) -> None:
        """click → target.click(selector, timeout=DEFAULT_ACTION_TIMEOUT_MS)."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.click = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator())
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.click("#submit")
        target.click.assert_awaited_once_with("#submit", timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_click_records_meta_merged_into_kwargs(self, tmp_path: Path) -> None:
        """click recorder call merges selector + role/role_name from meta."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.click = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator('- button "Save"'))
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.click("#submit")
        subj.recorder.record.assert_called_once_with("click", selector="#submit", role="button", role_name="Save")

    @pytest.mark.anyio
    async def test_type_text_delay_none_passes_zero(self, tmp_path: Path) -> None:
        """`delay=delay_ms or 0` — None → 0."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.type = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator())
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.type_text("#input", "hello", None)
        target.type.assert_awaited_once_with("#input", "hello", delay=0, timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_type_text_explicit_delay_preserved(self, tmp_path: Path) -> None:
        """Explicit delay_ms passes through."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.type = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator())
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.type_text("#input", "hello", 50)
        target.type.assert_awaited_once_with("#input", "hello", delay=50, timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_type_text_records_delay_ms(self, tmp_path: Path) -> None:
        """Recorder kwargs contain delay_ms exactly as passed (None preserved)."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.type = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator())
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.type_text("#input", "hi", None)
        subj.recorder.record.assert_called_once_with("type", selector="#input", text="hi", delay_ms=None)

    @pytest.mark.anyio
    async def test_fill_calls_target_fill_with_value(self, tmp_path: Path) -> None:
        """fill → target.fill(selector, value, timeout=...)."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.fill = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator())
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.fill("#email", "x@y.z")
        target.fill.assert_awaited_once_with("#email", "x@y.z", timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_fill_records_value(self, tmp_path: Path) -> None:
        """Recorder records selector + value."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.fill = AsyncMock()
        target.locator = MagicMock(return_value=_make_action_locator())
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.fill("#email", "x@y.z")
        subj.recorder.record.assert_called_once_with("fill", selector="#email", value="x@y.z")

    @pytest.mark.anyio
    async def test_press_key_uses_page_keyboard(self, tmp_path: Path) -> None:
        """press_key → page.keyboard.press(key) (NOT _target — keyboard is page-level)."""
        subj = _make_subject(tmp_path)
        await subj.press_key("Enter")
        subj.page.keyboard.press.assert_awaited_once_with("Enter")

    @pytest.mark.anyio
    async def test_press_key_records(self, tmp_path: Path) -> None:
        """Recorder records the key name."""
        subj = _make_subject(tmp_path)
        await subj.press_key("Tab")
        subj.recorder.record.assert_called_once_with("press_key", key="Tab")


# ─── input redaction (passwords) ───────────────────────────────────────────


def _make_input_target(input_type: str | None) -> MagicMock:
    """Build a _target() mock whose locator(...).first.evaluate() returns input_type.

    None simulates locator.evaluate() raising (e.g. element detached); the
    redaction policy must default to NOT redacting in that case so a flaky
    DOM lookup never accidentally falsifies non-secret recordings.
    """
    target = MagicMock()
    target.type = AsyncMock()
    target.fill = AsyncMock()
    # The locator(selector) call has to satisfy:
    #   - _resolve_semantic_metadata: ``.aria_snapshot()``
    #   - _is_password_input:         ``.first.evaluate(js)``
    locator_mock = MagicMock()
    locator_mock.aria_snapshot = AsyncMock(return_value="")
    first_mock = MagicMock()
    if input_type is None:
        first_mock.evaluate = AsyncMock(side_effect=RuntimeError("locator gone"))
    else:
        first_mock.evaluate = AsyncMock(return_value=input_type)
    locator_mock.first = first_mock
    target.locator = MagicMock(return_value=locator_mock)
    return target


def _make_action_locator(snapshot: str = "", input_type: str = "text") -> MagicMock:
    locator = MagicMock()
    locator.aria_snapshot = AsyncMock(return_value=snapshot)
    locator.first = MagicMock()
    locator.first.evaluate = AsyncMock(return_value=input_type)
    return locator


def _make_redaction_subject(tmp_path: Path, input_type: str | None) -> SessionPageMixin:
    """Reusable subject builder for redaction tests."""
    subj = SessionPageMixin.__new__(SessionPageMixin)
    subj._last_mcp_navigation = None
    subj.page = MagicMock()
    subj.pages = [subj.page]
    subj.recorder = MagicMock()
    subj.recorder.record = MagicMock()
    target = _make_input_target(input_type)
    subj._target = lambda: target  # type: ignore[attr-defined]
    return subj


class TestInputRedaction:
    """Passwords typed/filled into <input type=password> must NOT land in the
    JSONL record. The page must still receive the real value."""

    @pytest.mark.anyio
    async def test_default_redacts_password_field_for_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """type_text into a password field → recorder text is the placeholder, NOT the secret."""
        monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
        from octowright.defaults import REDACTED_INPUT_PLACEHOLDER

        subj = _make_redaction_subject(tmp_path, "password")
        await subj.type_text("#pw", "hunter2-secret!", None)
        target = subj._target()
        # Page got the real value:
        target.type.assert_awaited_once_with("#pw", "hunter2-secret!", delay=0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        # Recorder got the redacted placeholder:
        call = subj.recorder.record.call_args
        assert call.args == ("type",)
        assert call.kwargs["text"] == REDACTED_INPUT_PLACEHOLDER
        assert call.kwargs["text"] != "hunter2-secret!"

    @pytest.mark.anyio
    async def test_default_redacts_password_field_for_fill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fill into a password field → recorder value is the placeholder."""
        monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
        from octowright.defaults import REDACTED_INPUT_PLACEHOLDER

        subj = _make_redaction_subject(tmp_path, "password")
        await subj.fill("#pw", "hunter2-secret!")
        target = subj._target()
        target.fill.assert_awaited_once_with("#pw", "hunter2-secret!", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        call = subj.recorder.record.call_args
        assert call.args == ("fill",)
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_default_does_not_redact_text_field(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default mode 'passwords' must NOT redact regular text inputs."""
        monkeypatch.delenv("OCTOWRIGHT_REDACT_INPUTS", raising=False)
        subj = _make_redaction_subject(tmp_path, "text")
        await subj.type_text("#name", "alice", None)
        call = subj.recorder.record.call_args
        assert call.kwargs["text"] == "alice"

    @pytest.mark.anyio
    async def test_all_mode_redacts_text_field(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCTOWRIGHT_REDACT_INPUTS=all redacts even type=text fields."""
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "all")
        from octowright.defaults import REDACTED_INPUT_PLACEHOLDER

        subj = _make_redaction_subject(tmp_path, "text")
        await subj.fill("#name", "alice")
        target = subj._target()
        target.fill.assert_awaited_once_with("#name", "alice", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == REDACTED_INPUT_PLACEHOLDER

    @pytest.mark.anyio
    async def test_off_mode_disables_redaction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """OCTOWRIGHT_REDACT_INPUTS=off → password fields are NOT redacted (legacy)."""
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "off")
        subj = _make_redaction_subject(tmp_path, "password")
        await subj.fill("#pw", "hunter2-secret!")
        call = subj.recorder.record.call_args
        assert call.kwargs["value"] == "hunter2-secret!"

    @pytest.mark.anyio
    @pytest.mark.parametrize("mode", ["off", "passwords", "all"])
    async def test_page_receives_unredacted_value_always(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
    ) -> None:
        """No matter the policy, the page MUST receive the real value (typing works)."""
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", mode)
        subj = _make_redaction_subject(tmp_path, "password")
        await subj.fill("#pw", "real-secret-value")
        target = subj._target()
        target.fill.assert_awaited_once_with("#pw", "real-secret-value", timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_locator_evaluate_failure_does_not_break_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the DOM type lookup raises, fall back to NOT redacting (don't break typing)."""
        monkeypatch.setenv("OCTOWRIGHT_REDACT_INPUTS", "passwords")
        subj = _make_redaction_subject(tmp_path, None)  # evaluate raises
        # Must not raise — the page action still goes through.
        await subj.type_text("#x", "value", None)
        target = subj._target()
        target.type.assert_awaited_once()


# ─── screenshot / snapshot / evaluate ───────────────────────────────────────


class TestArtifactCalls:
    @pytest.mark.anyio
    async def test_screenshot_creates_parent_dir(self, tmp_path: Path) -> None:
        """parent.mkdir(parents=True, exist_ok=True) — nested path works."""
        subj = _make_subject(tmp_path)
        target_path = tmp_path / "deep" / "nested" / "shot.png"
        await subj.screenshot(target_path)
        assert target_path.parent.exists()

    @pytest.mark.anyio
    async def test_screenshot_writes_via_atomic_temp_then_replace(self, tmp_path: Path) -> None:
        """screenshot stages to a sibling ``.{name}.<rand>.tmp`` and os.replaces
        into the final path — defeats the symlink-swap window between the
        caller's containment check and Playwright's write."""
        subj = _make_subject(tmp_path)
        target_path = tmp_path / "shot.png"

        # Capture the path Playwright was asked to write to so we can verify
        # the stager handed it a temp sibling, not the final path.
        captured: dict[str, str] = {}

        async def _capture_screenshot(*, path: str) -> None:
            captured["path"] = path
            # Playwright would create the file at the requested path; mimic
            # that so the os.replace into the final target succeeds.
            Path(path).write_bytes(b"\x89PNG")

        subj.page.screenshot = AsyncMock(side_effect=_capture_screenshot)
        await subj.screenshot(target_path)

        # Final file landed at the intended path; no temp sibling remained.
        assert target_path.exists()
        assert target_path.read_bytes() == b"\x89PNG"
        leaked = list(tmp_path.glob(".shot.png.*.tmp"))
        assert leaked == [], f"screenshot leaked temp file(s): {leaked}"
        # Playwright was given the temp path, not the final path.
        assert captured["path"] != str(target_path)
        assert captured["path"].startswith(str(tmp_path / ".shot.png."))
        assert captured["path"].endswith(".tmp")

    @pytest.mark.anyio
    async def test_screenshot_cleans_up_temp_file_on_playwright_failure(self, tmp_path: Path) -> None:
        """If Playwright raises mid-write, the staging ``.tmp`` must not leak."""
        subj = _make_subject(tmp_path)
        target_path = tmp_path / "shot.png"

        async def _boom(**_kw: object) -> None:
            raise RuntimeError("playwright exploded")

        subj.page.screenshot = AsyncMock(side_effect=_boom)
        with pytest.raises(RuntimeError, match="playwright exploded"):
            await subj.screenshot(target_path)

        assert not target_path.exists()
        leaked = list(tmp_path.glob(".shot.png.*.tmp"))
        assert leaked == [], f"screenshot leaked temp file(s) on failure: {leaked}"

    @pytest.mark.anyio
    async def test_screenshot_returns_path(self, tmp_path: Path) -> None:
        """Returns the input Path unchanged."""
        subj = _make_subject(tmp_path)
        target_path = tmp_path / "shot.png"
        result = await subj.screenshot(target_path)
        assert result is target_path

    @pytest.mark.anyio
    async def test_screenshot_records_str_path(self, tmp_path: Path) -> None:
        """recorder.record('screenshot', path=str(path))."""
        subj = _make_subject(tmp_path)
        target_path = tmp_path / "shot.png"
        await subj.screenshot(target_path)
        subj.recorder.record.assert_called_once_with("screenshot", path=str(target_path))

    @pytest.mark.anyio
    async def test_snapshot_uses_html_locator(self, tmp_path: Path) -> None:
        """snapshot calls page.locator('html').aria_snapshot()."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value="- root")
        subj.page.locator = MagicMock(return_value=loc)
        result = await subj.snapshot()
        subj.page.locator.assert_called_with("html")
        assert result["aria"] == "- root"

    @pytest.mark.anyio
    async def test_snapshot_returns_url_and_title(self, tmp_path: Path) -> None:
        """Return dict carries aria + url + title."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value="- y")
        subj.page.locator = MagicMock(return_value=loc)
        subj.page.title = AsyncMock(return_value="Title")
        result = await subj.snapshot()
        assert result == {"aria": "- y", "url": "https://octowright.com", "title": "Title"}

    @pytest.mark.anyio
    async def test_snapshot_records_event(self, tmp_path: Path) -> None:
        """recorder.record('snapshot') with no kwargs."""
        subj = _make_subject(tmp_path)
        loc = MagicMock()
        loc.aria_snapshot = AsyncMock(return_value="")
        subj.page.locator = MagicMock(return_value=loc)
        await subj.snapshot()
        subj.recorder.record.assert_called_once_with("snapshot")

    @pytest.mark.anyio
    async def test_evaluate_returns_target_result(self, tmp_path: Path) -> None:
        """evaluate returns whatever _target().evaluate returned."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=42)
        subj._target = lambda: target  # type: ignore[attr-defined]
        assert await subj.evaluate("1+1") == 42

    @pytest.mark.anyio
    async def test_evaluate_records_expression(self, tmp_path: Path) -> None:
        """recorder.record('evaluate', expression=expr)."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=None)
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.evaluate("foo()")
        subj.recorder.record.assert_called_once_with("evaluate", expression="foo()")


# ─── wait_for: three branches ─────────────────────────────────────────────


class TestWaitFor:
    @pytest.mark.anyio
    async def test_selector_branch(self, tmp_path: Path) -> None:
        """selector= → target.wait_for_selector with timeout."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.wait_for("#x", None, 200)
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=200)
        subj.recorder.record.assert_called_once_with("wait_for", selector="#x", timeout_ms=200)

    @pytest.mark.anyio
    async def test_text_branch(self, tmp_path: Path) -> None:
        """text= polls body text without wait_for_function, so CSP cannot block it."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_function = AsyncMock(side_effect=AssertionError("wait_for_function should not be used"))
        body = MagicMock()
        body.inner_text = AsyncMock(return_value="well hello there")
        target.locator = MagicMock(return_value=body)
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.wait_for(None, "hello", 200)
        target.locator.assert_called_with("body")
        body.inner_text.assert_awaited_once()
        target.wait_for_function.assert_not_awaited()
        subj.recorder.record.assert_called_once_with("wait_for", text="hello", timeout_ms=200)

    @pytest.mark.anyio
    async def test_network_idle_branch(self, tmp_path: Path) -> None:
        """No selector + no text → page.wait_for_load_state('networkidle')."""
        subj = _make_subject(tmp_path)
        await subj.wait_for(None, None, 500)
        subj.page.wait_for_load_state.assert_awaited_once_with("networkidle", timeout=500)
        subj.recorder.record.assert_called_once_with("wait_for", timeout_ms=500)

    @pytest.mark.anyio
    async def test_default_timeout_when_none(self, tmp_path: Path) -> None:
        """timeout_ms=None → DEFAULT_ACTION_TIMEOUT_MS."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.wait_for("#x", None, None)
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_expression_branch(self, tmp_path: Path) -> None:
        """expression= polls with evaluate, not wait_for_function, so CSP cannot block it."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_function = AsyncMock(side_effect=AssertionError("wait_for_function should not be used"))
        target.evaluate = AsyncMock(side_effect=[False, True])
        subj._target = lambda: target  # type: ignore[attr-defined]
        expr = "() => document.querySelectorAll('tbody tr').length > 0"
        await subj.wait_for(None, None, 250, expression=expr)
        assert target.evaluate.await_count == 2
        target.wait_for_function.assert_not_awaited()
        subj.recorder.record.assert_called_once_with("wait_for", expression=expr, timeout_ms=250)

    @pytest.mark.anyio
    async def test_expression_default_timeout(self, tmp_path: Path) -> None:
        """expression= with timeout=None → DEFAULT_ACTION_TIMEOUT_MS."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=True)
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.wait_for(None, None, None, expression="true")
        target.evaluate.assert_awaited_once_with("true")

    @pytest.mark.anyio
    async def test_selector_and_text_both_set_raises(self, tmp_path: Path) -> None:
        """Conflicting inputs are a programmer error — surface immediately."""
        subj = _make_subject(tmp_path)
        with pytest.raises(ValueError, match="at most one of"):
            await subj.wait_for("#x", "hello", None)

    @pytest.mark.anyio
    async def test_selector_and_expression_both_set_raises(self, tmp_path: Path) -> None:
        subj = _make_subject(tmp_path)
        with pytest.raises(ValueError, match="at most one of"):
            await subj.wait_for("#x", None, None, expression="true")

    @pytest.mark.anyio
    async def test_text_and_expression_both_set_raises(self, tmp_path: Path) -> None:
        subj = _make_subject(tmp_path)
        with pytest.raises(ValueError, match="at most one of"):
            await subj.wait_for(None, "hello", None, expression="true")

    @pytest.mark.anyio
    async def test_all_three_set_raises(self, tmp_path: Path) -> None:
        subj = _make_subject(tmp_path)
        with pytest.raises(ValueError, match="at most one of"):
            await subj.wait_for("#x", "hello", None, expression="true")

    @pytest.mark.anyio
    async def test_empty_string_selector_treated_as_unprovided(self, tmp_path: Path) -> None:
        """An empty string selector from a hand-edited macro must not count
        as "selector provided" — the if/elif chain routes via truthiness,
        so an empty string with text=hello would otherwise raise a
        confusing "two values set" error where bool(selector) is False."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        body = MagicMock()
        body.inner_text = AsyncMock(return_value="hello")
        target.locator = MagicMock(return_value=body)
        subj._target = lambda: target  # type: ignore[attr-defined]
        # selector="" + text="hello": validation should treat "" as
        # unprovided and dispatch the text branch.
        await subj.wait_for("", "hello", 200)
        target.locator.assert_called_with("body")

    @pytest.mark.anyio
    async def test_timeout_ms_zero_means_wait_forever(self, tmp_path: Path) -> None:
        """Playwright treats timeout=0 as 'no timeout'. The old
        `timeout_ms or DEFAULT` collapsed both None and 0 to DEFAULT, so
        callers couldn't request 'wait forever'. timeout=0 must now reach
        Playwright as 0."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.wait_for("#x", None, 0)
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=0)


# ─── expect_url ─────────────────────────────────────────────────────────────


class TestExpectUrl:
    @pytest.mark.anyio
    async def test_equals_match_returns_actual(self, tmp_path: Path) -> None:
        """Equals success returns the actual URL string."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x"
        assert await subj.expect_url("https://x", "equals") == "https://x"

    @pytest.mark.anyio
    async def test_equals_mismatch_raises(self, tmp_path: Path) -> None:
        """Equals mismatch error format."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x"
        with pytest.raises(RuntimeError, match=r'expected "https://y" \(equals\)'):
            await subj.expect_url("https://y", "equals")

    @pytest.mark.anyio
    async def test_contains_substring_match(self, tmp_path: Path) -> None:
        """Substring match in 'contains' mode."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x/login?ok=1"
        assert await subj.expect_url("login", "contains") == "https://x/login?ok=1"

    @pytest.mark.anyio
    async def test_contains_mismatch_raises(self, tmp_path: Path) -> None:
        """Contains mismatch error format."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x"
        with pytest.raises(RuntimeError, match=r'substring "y" \(contains\)'):
            await subj.expect_url("y", "contains")

    @pytest.mark.anyio
    async def test_regex_match(self, tmp_path: Path) -> None:
        """Regex match returns actual."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x/abc123"
        assert await subj.expect_url(r"abc\d+", "regex") == "https://x/abc123"

    @pytest.mark.anyio
    async def test_regex_mismatch_raises(self, tmp_path: Path) -> None:
        """Regex mismatch error mentions pattern + (regex)."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x/no-digits"
        with pytest.raises(RuntimeError, match=r"\(regex\)"):
            await subj.expect_url(r"\d{4}", "regex")

    @pytest.mark.anyio
    async def test_unknown_mode_value_error(self, tmp_path: Path) -> None:
        """Unknown mode → ValueError."""
        subj = _make_subject(tmp_path)
        with pytest.raises(ValueError, match=r"unknown mode 'weird'"):
            await subj.expect_url("x", "weird")

    @pytest.mark.anyio
    async def test_records_pattern_and_mode(self, tmp_path: Path) -> None:
        """recorder.record('expect_url', pattern=..., mode=...) on success."""
        subj = _make_subject(tmp_path)
        subj.page.url = "https://x"
        await subj.expect_url("https://x", "equals")
        subj.recorder.record.assert_called_once_with("expect_url", pattern="https://x", mode="equals")


# ─── expect_text ─────────────────────────────────────────────────────────


class TestExpectText:
    def _setup(self, tmp_path: Path, *, inner_text: str = "hello") -> SessionPageMixin:
        subj = _make_subject(tmp_path)
        target = MagicMock()
        element = MagicMock()
        element.inner_text = AsyncMock(return_value=inner_text)
        target.wait_for_selector = AsyncMock(return_value=element)
        subj._target = lambda: target  # type: ignore[attr-defined]
        return subj

    @pytest.mark.anyio
    async def test_contains_default_mode(self, tmp_path: Path) -> None:
        """Mode default = 'contains'."""
        subj = self._setup(tmp_path, inner_text="hello world")
        result = await subj.expect_text("#x", "world")
        assert result == "hello world"

    @pytest.mark.anyio
    async def test_contains_mismatch_raises(self, tmp_path: Path) -> None:
        """Contains mismatch error format."""
        subj = self._setup(tmp_path, inner_text="foo")
        with pytest.raises(RuntimeError, match=r'expected to contain "bar"'):
            await subj.expect_text("#x", "bar")

    @pytest.mark.anyio
    async def test_equals_branch(self, tmp_path: Path) -> None:
        """Equals success / failure."""
        subj = self._setup(tmp_path, inner_text="exact")
        assert await subj.expect_text("#x", "exact", "equals") == "exact"
        with pytest.raises(RuntimeError, match=r"\(equals\)"):
            await subj.expect_text("#x", "different", "equals")

    @pytest.mark.anyio
    async def test_regex_branch(self, tmp_path: Path) -> None:
        """Regex mode matches with re.search."""
        subj = self._setup(tmp_path, inner_text="user-1234")
        assert await subj.expect_text("#x", r"\d+", "regex") == "user-1234"

    @pytest.mark.anyio
    async def test_regex_mismatch_raises(self, tmp_path: Path) -> None:
        """Regex no-match error includes (regex)."""
        subj = self._setup(tmp_path, inner_text="no-digits")
        with pytest.raises(RuntimeError, match=r"\(regex\)"):
            await subj.expect_text("#x", r"\d{4}", "regex")

    @pytest.mark.anyio
    async def test_unknown_mode_value_error(self, tmp_path: Path) -> None:
        """Unknown mode → ValueError."""
        subj = self._setup(tmp_path)
        with pytest.raises(ValueError, match=r"unknown mode"):
            await subj.expect_text("#x", "y", "weird")

    @pytest.mark.anyio
    async def test_default_timeout_when_none(self, tmp_path: Path) -> None:
        """timeout_ms=None → DEFAULT_ACTION_TIMEOUT_MS used in wait_for_selector."""
        subj = self._setup(tmp_path)
        await subj.expect_text("#x", "hello")
        target = subj._target()
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_explicit_timeout_passes_through(self, tmp_path: Path) -> None:
        """Explicit timeout is forwarded."""
        subj = self._setup(tmp_path)
        await subj.expect_text("#x", "hello", timeout_ms=500)
        target = subj._target()
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=500)

    @pytest.mark.anyio
    async def test_wait_for_selector_failure_raises_runtime_error(self, tmp_path: Path) -> None:
        """Underlying Playwright failure → RuntimeError 'element never appeared'."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock(side_effect=Exception("timeout"))
        subj._target = lambda: target  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match=r"element never appeared"):
            await subj.expect_text("#missing", "y")

    @pytest.mark.anyio
    async def test_wait_for_selector_returns_none_raises(self, tmp_path: Path) -> None:
        """If wait_for_selector returns None (no element), 'element never appeared'."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock(return_value=None)
        subj._target = lambda: target  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match=r"element never appeared"):
            await subj.expect_text("#missing", "y")

    @pytest.mark.anyio
    async def test_records_event_on_success(self, tmp_path: Path) -> None:
        """recorder.record('expect_text', selector, text, mode) emitted."""
        subj = self._setup(tmp_path, inner_text="hi")
        await subj.expect_text("#x", "hi", "equals")
        subj.recorder.record.assert_called_once_with("expect_text", selector="#x", text="hi", mode="equals")


# ─── expect_selector ──────────────────────────────────────────────────────


class TestExpectSelector:
    @pytest.mark.anyio
    async def test_present_calls_wait_for_selector(self, tmp_path: Path) -> None:
        """present=True (default) → wait_for_selector with timeout."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.expect_selector("#x", timeout_ms=300)
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=300)

    @pytest.mark.anyio
    async def test_present_default_timeout(self, tmp_path: Path) -> None:
        """timeout_ms=None → DEFAULT_ACTION_TIMEOUT_MS."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.expect_selector("#x")
        target.wait_for_selector.assert_awaited_once_with("#x", timeout=DEFAULT_ACTION_TIMEOUT_MS)

    @pytest.mark.anyio
    async def test_present_failure_raises(self, tmp_path: Path) -> None:
        """wait_for_selector raising → RuntimeError 'selector never appeared'."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock(side_effect=Exception("nope"))
        subj._target = lambda: target  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match=r"selector never appeared"):
            await subj.expect_selector("#missing")

    @pytest.mark.anyio
    async def test_absent_calls_query_selector(self, tmp_path: Path) -> None:
        """present=False → query_selector (no timeout) — single poll."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.query_selector = AsyncMock(return_value=None)
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.expect_selector("#nope", present=False)
        target.query_selector.assert_awaited_once_with("#nope")

    @pytest.mark.anyio
    async def test_absent_finds_element_raises(self, tmp_path: Path) -> None:
        """present=False but element exists → RuntimeError 'should be absent'."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.query_selector = AsyncMock(return_value=MagicMock())
        subj._target = lambda: target  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match=r"should be absent"):
            await subj.expect_selector("#bad", present=False)

    @pytest.mark.anyio
    async def test_records_present_kwarg(self, tmp_path: Path) -> None:
        """recorder.record('expect_selector', selector, present)."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.wait_for_selector = AsyncMock()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.expect_selector("#x")
        subj.recorder.record.assert_called_once_with("expect_selector", selector="#x", present=True)


# ─── expect_js ────────────────────────────────────────────────────────────


class TestExpectJs:
    @pytest.mark.anyio
    async def test_truthy_no_equals_returns_result(self, tmp_path: Path) -> None:
        """Truthy result + equals=None → returns result, no raise."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=42)
        subj._target = lambda: target  # type: ignore[attr-defined]
        assert await subj.expect_js("1+1") == 42

    @pytest.mark.anyio
    async def test_falsy_no_equals_raises(self, tmp_path: Path) -> None:
        """Falsy result + equals=None → 'not truthy'."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=False)
        subj._target = lambda: target  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match=r"not truthy"):
            await subj.expect_js("false")

    @pytest.mark.anyio
    async def test_equals_match_returns_result(self, tmp_path: Path) -> None:
        """Equals success returns result."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=10)
        subj._target = lambda: target  # type: ignore[attr-defined]
        assert await subj.expect_js("5+5", equals=10) == 10

    @pytest.mark.anyio
    async def test_equals_mismatch_raises_with_repr(self, tmp_path: Path) -> None:
        """Mismatch embeds expression!r, expected!r, got!r."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value="actual")
        subj._target = lambda: target  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match=r"expected='expected'"):
            await subj.expect_js("x", equals="expected")

    @pytest.mark.anyio
    async def test_records_expression_and_equals(self, tmp_path: Path) -> None:
        """recorder.record('expect_js', expression, equals)."""
        subj = _make_subject(tmp_path)
        target = MagicMock()
        target.evaluate = AsyncMock(return_value=True)
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.expect_js("isOK()")
        subj.recorder.record.assert_called_once_with("expect_js", expression="isOK()", equals=None)


# ─── list_pages / switch_page / close_page edge cases ───────────────────────


class TestListPagesEdgeCases:
    def test_list_pages_url_attribute_failure_yields_none(self, tmp_path: Path) -> None:
        """If page.url raises, that page's url is None (not propagated)."""
        subj = _make_subject(tmp_path)
        bad = MagicMock()
        type(bad).url = property(lambda _self: (_ for _ in ()).throw(RuntimeError("no url")))
        subj.pages = [subj.page, bad]
        result = subj.list_pages()
        assert result[0]["url"] == "https://octowright.com"
        assert result[1]["url"] is None

    def test_list_pages_marks_active_correctly(self, tmp_path: Path) -> None:
        """is_active is True iff p is self.page."""
        subj = _make_subject(tmp_path)
        p2 = MagicMock()
        p2.url = "https://second"
        subj.pages = [subj.page, p2]
        result = subj.list_pages()
        assert result[0]["is_active"] is True
        assert result[1]["is_active"] is False


class TestSwitchPageRecord:
    @pytest.mark.anyio
    async def test_records_switch_event(self, tmp_path: Path) -> None:
        """switch_page emits recorder.record('switch_page', index, url)."""
        subj = _make_subject(tmp_path)
        p2 = MagicMock()
        p2.url = "https://second"
        subj.pages = [subj.page, p2]
        await subj.switch_page(1)
        subj.recorder.record.assert_called_once_with("switch_page", index=1, url="https://second")

    @pytest.mark.anyio
    async def test_returns_index_url_and_count(self, tmp_path: Path) -> None:
        """Return shape: index, url, page_count."""
        subj = _make_subject(tmp_path)
        p2 = MagicMock()
        p2.url = "https://second"
        subj.pages = [subj.page, p2]
        result = await subj.switch_page(1)
        assert result == {"index": 1, "url": "https://second", "page_count": 2}

    @pytest.mark.anyio
    async def test_negative_index_raises(self, tmp_path: Path) -> None:
        """Negative index → IndexError (the `< 0` branch)."""
        subj = _make_subject(tmp_path)
        with pytest.raises(IndexError, match=r"out of range"):
            await subj.switch_page(-1)


class TestClosePageRecord:
    @pytest.mark.anyio
    async def test_records_close_event(self, tmp_path: Path) -> None:
        """close_page emits recorder.record('close_page', index, was_active)."""
        subj = _make_subject(tmp_path)
        p2 = MagicMock()
        p2.url = "https://second"
        p2.close = AsyncMock()
        subj.pages = [subj.page, p2]
        await subj.close_page(1)
        subj.recorder.record.assert_called_once_with("close_page", index=1, was_active=False)

    @pytest.mark.anyio
    async def test_returns_close_summary(self, tmp_path: Path) -> None:
        """Return shape: closed_index, was_active, active_index, page_count."""
        subj = _make_subject(tmp_path)
        p2 = MagicMock()
        p2.url = "https://second"
        p2.close = AsyncMock()
        subj.pages = [subj.page, p2]
        result = await subj.close_page(1)
        assert result == {"closed_index": 1, "was_active": False, "active_index": 0, "page_count": 1}

    @pytest.mark.anyio
    async def test_negative_index_raises(self, tmp_path: Path) -> None:
        """Negative index → IndexError (when more than one page exists)."""
        subj = _make_subject(tmp_path)
        p2 = MagicMock()
        p2.close = AsyncMock()
        subj.pages = [subj.page, p2]
        with pytest.raises(IndexError, match=r"out of range"):
            await subj.close_page(-1)

    @pytest.mark.anyio
    async def test_close_last_page_raises_runtime_error(self, tmp_path: Path) -> None:
        """Close-last-page guard: RuntimeError, not IndexError."""
        subj = _make_subject(tmp_path)
        with pytest.raises(RuntimeError, match=r"cannot close the last remaining page"):
            await subj.close_page(0)

    @pytest.mark.anyio
    async def test_popup_close_during_await_does_not_raise_value_error(self, tmp_path: Path) -> None:
        """Simulates a popup _on_page_close listener firing during the
        ``await target.close()`` and stripping the currently-active page out
        of ``self.pages``. The post-await ``self.pages.index(self.page)``
        previously raised ValueError; the fix must surface a valid
        active_index instead."""
        subj = _make_subject(tmp_path)
        sibling = MagicMock()
        sibling.url = "https://sibling"
        target = MagicMock()
        target.url = "https://target"

        async def _close_and_strip_sibling() -> None:
            # During the await, a synchronous popup-close listener mutates
            # self.pages and removes the page we just chose as active.
            if sibling in subj.pages:
                subj.pages.remove(sibling)

        target.close = AsyncMock(side_effect=_close_and_strip_sibling)
        subj.pages = [target, sibling]
        subj.page = target
        result = await subj.close_page(0)
        assert result["closed_index"] == 0
        assert result["was_active"] is True
        # active_index falls back cleanly even though self.page is no
        # longer in self.pages.
        assert result["active_index"] == 0
        assert result["page_count"] == 0

    @pytest.mark.anyio
    async def test_popup_close_leaves_other_pages_intact(self, tmp_path: Path) -> None:
        """If the popup-close listener trims a *non-active* page, the
        returned active_index still points at the surviving active page."""
        subj = _make_subject(tmp_path)
        survivor = MagicMock()
        survivor.url = "https://survivor"
        bystander = MagicMock()
        bystander.url = "https://bystander"
        target = MagicMock()
        target.url = "https://target"

        async def _close_and_strip_bystander() -> None:
            if bystander in subj.pages:
                subj.pages.remove(bystander)

        target.close = AsyncMock(side_effect=_close_and_strip_bystander)
        subj.pages = [target, survivor, bystander]
        subj.page = target
        result = await subj.close_page(0)
        # survivor was promoted to active before the await; it should still
        # be the active page after.
        assert subj.page is survivor
        assert result["active_index"] == subj.pages.index(survivor)
        assert result["page_count"] == len(subj.pages)
