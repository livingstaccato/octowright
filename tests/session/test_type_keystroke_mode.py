# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``type_text(key_mode="keys")`` -- typing that actually holds Shift.

The default path sends Playwright's ``type()``, which carries the right
``key``/``text`` payload but never holds the modifier, so a target reading
``code`` + ``shiftKey`` (a canvas KVM console) receives every shifted
character as its unshifted twin. These pin that keystroke mode presses real
keys with Shift genuinely down, and that the default path is untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.defaults import DEFAULT_ACTION_TIMEOUT_MS
from octowright.session.core_page_mixin import SessionPageMixin
from tests._aria_stubs import stub_credential_scan
from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _Subject(OperationAwareFake, SessionPageMixin):
    """A page mixin with a real operation gate, so type_text runs decorated."""


def _make_subject(tmp_path: Path) -> _Subject:
    subj = _Subject()
    page = MagicMock()
    page.url = "https://octowright.com"
    # One keyboard mock records the whole ordering: down/press/up interleave
    # on it, and method_calls preserves the sequence across all three.
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.down = AsyncMock()
    page.keyboard.up = AsyncMock()
    page.keyboard.type = AsyncMock()
    subj.page = page
    subj.pages = [page]
    subj.instance_id = "keystroke-subject"
    subj.url = None
    subj.recorder = MagicMock()
    subj.log_path = tmp_path / "rec.jsonl"
    subj.active_frame = None
    return subj


def _make_target() -> MagicMock:
    target = MagicMock()
    target.type = AsyncMock()
    target.focus = AsyncMock()
    loc = MagicMock()
    loc.aria_snapshot = AsyncMock(return_value="")
    stub_credential_scan(loc)
    target.locator = MagicMock(return_value=loc)
    return target


def _keyboard_sequence(subj: _Subject) -> list[tuple[str, Any]]:
    """(method, first-arg) for every keyboard call, in order."""
    return [(name, args[0] if args else None) for name, args, _kw in subj.page.keyboard.method_calls]


class TestKeystrokeMode:
    @pytest.mark.anyio
    async def test_shifted_character_holds_shift_across_the_press(self, tmp_path: Path) -> None:
        """Shift is DOWN before the key and UP after -- the whole point.

        Playwright's type() sends the same character with no modifier held,
        which is what a canvas target reads as unshifted.
        """
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        await subj.type_text("#console", "A", None, key_mode="keys")
        assert _keyboard_sequence(subj) == [("down", "Shift"), ("press", "KeyA"), ("up", "Shift")]

    @pytest.mark.anyio
    async def test_unshifted_character_never_touches_shift(self, tmp_path: Path) -> None:
        """A plain key must not latch a modifier it does not need."""
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        await subj.type_text("#console", "a", None, key_mode="keys")
        assert _keyboard_sequence(subj) == [("press", "KeyA")]

    @pytest.mark.anyio
    async def test_field_reported_string_sends_every_shifted_key_shifted(self, tmp_path: Path) -> None:
        """The exact string from the bug report.

        `echo TYPE=Ab*:` arrived as `echo type=ab8;` -- T/A shifted letters,
        `*` = Shift+Digit8, `:` = Shift+Semicolon. Each must press its key
        with Shift held; dropping it produces the corruption verbatim.
        """
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        await subj.type_text("#console", "TYPE=Ab*:", None, key_mode="keys")
        assert _keyboard_sequence(subj) == [
            ("down", "Shift"),
            ("press", "KeyT"),
            ("up", "Shift"),
            ("down", "Shift"),
            ("press", "KeyY"),
            ("up", "Shift"),
            ("down", "Shift"),
            ("press", "KeyP"),
            ("up", "Shift"),
            ("down", "Shift"),
            ("press", "KeyE"),
            ("up", "Shift"),
            ("press", "Equal"),
            ("down", "Shift"),
            ("press", "KeyA"),
            ("up", "Shift"),
            ("press", "KeyB"),
            ("down", "Shift"),
            ("press", "Digit8"),
            ("up", "Shift"),
            ("down", "Shift"),
            ("press", "Semicolon"),
            ("up", "Shift"),
        ]

    @pytest.mark.anyio
    async def test_focuses_the_selector_before_typing(self, tmp_path: Path) -> None:
        """Keystrokes go to the page keyboard, so the element must be focused
        first or they land wherever focus happened to be."""
        subj = _make_subject(tmp_path)
        target = _make_target()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.type_text("#console", "a", None, key_mode="keys")
        target.focus.assert_awaited_once_with("#console", timeout=DEFAULT_ACTION_TIMEOUT_MS)
        target.type.assert_not_awaited()

    @pytest.mark.anyio
    async def test_shift_is_released_even_when_the_press_raises(self, tmp_path: Path) -> None:
        """A latched Shift would corrupt every later keystroke on the page,
        including another tool's, so release is in a finally."""
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        subj.page.keyboard.press = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(RuntimeError, match="boom"):
            await subj.type_text("#console", "A", None, key_mode="keys")
        assert ("up", "Shift") in _keyboard_sequence(subj)

    @pytest.mark.anyio
    async def test_unmappable_character_falls_back_to_text_insertion(self, tmp_path: Path) -> None:
        """No physical key means no scancode; guessing one would be worse."""
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        await subj.type_text("#console", "é", None, key_mode="keys")
        subj.page.keyboard.type.assert_awaited_once_with("é")
        subj.page.keyboard.press.assert_not_awaited()


class TestDefaultModeUnchanged:
    @pytest.mark.anyio
    @pytest.mark.parametrize("key_mode", [None, "text"])
    async def test_default_still_uses_playwright_type(self, tmp_path: Path, key_mode: str | None) -> None:
        """Every pre-existing caller must keep the one-shot type() path --
        it is correct for DOM inputs and carries no layout assumption."""
        subj = _make_subject(tmp_path)
        target = _make_target()
        subj._target = lambda: target  # type: ignore[attr-defined]
        await subj.type_text("#name", "Ab*", 0, key_mode=key_mode)
        target.type.assert_awaited_once_with("#name", "Ab*", delay=0, timeout=DEFAULT_ACTION_TIMEOUT_MS)
        assert _keyboard_sequence(subj) == []

    @pytest.mark.anyio
    async def test_default_mode_records_no_key_mode_field(self, tmp_path: Path) -> None:
        """An ordinary type row stays byte-identical to existing recordings."""
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        await subj.type_text("#name", "hi", None)
        _name, _args, kwargs = subj.recorder.record.mock_calls[0]
        assert "key_mode" not in kwargs

    @pytest.mark.anyio
    async def test_keystroke_mode_is_recorded_so_replay_reproduces_it(self, tmp_path: Path) -> None:
        """A macro replayed without key_mode would silently corrupt input the
        recorded run got right."""
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        await subj.type_text("#console", "A", None, key_mode="keys")
        _name, _args, kwargs = subj.recorder.record.mock_calls[0]
        assert kwargs["key_mode"] == "keys"

    @pytest.mark.anyio
    async def test_unknown_key_mode_is_refused(self, tmp_path: Path) -> None:
        """Silently treating a typo as the default is how the original bug
        stayed invisible; refuse instead."""
        subj = _make_subject(tmp_path)
        subj._target = lambda: _make_target()  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="key_mode"):
            await subj.type_text("#console", "A", None, key_mode="scancode")
