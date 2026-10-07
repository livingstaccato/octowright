# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Simple replay dispatch: screenshots, misrouted and unknown kinds, and the fill_by fallback."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from octowright import defaults
from octowright.macros.runtime import dispatch_simple

SEMANTIC_KEYS = ("role", "name", "label", "text", "placeholder", "test_id")


class _Session:
    instance_id = "inst-1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def screenshot(self, path: Path) -> None:
        self.calls.append(("screenshot", path))

    async def fill_by(self, **kwargs: Any) -> None:
        self.calls.append(("fill_by", kwargs))
        raise RuntimeError("no element with that label")

    async def fill(self, **kwargs: Any) -> None:
        self.calls.append(("fill", kwargs))

    async def click(self, **kwargs: Any) -> None:
        self.calls.append(("click", kwargs))


async def _dispatch(session: _Session, action: dict[str, Any]) -> tuple[int, int]:
    return await dispatch_simple(
        session,  # type: ignore[arg-type]
        action,
        semantic_keys=SEMANTIC_KEYS,
        strip_non_aria_noise=lambda kind, kwargs: kwargs,
        action_kwargs=lambda action: {k: v for k, v in action.items() if k != "action"},
    )


async def test_a_contained_screenshot_counts_one_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path.resolve())
    session = _Session()
    target = tmp_path.resolve() / "shot.png"

    assert await _dispatch(session, {"action": "screenshot", "path": str(target)}) == (1, 0)
    assert session.calls == [("screenshot", target)]


async def test_a_screenshot_outside_the_recordings_is_refused_by_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / "recordings").resolve()
    root.mkdir()
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", root)
    outside = tmp_path.resolve() / "shot.png"

    with pytest.raises(ValueError) as excinfo:
        await _dispatch(_Session(), {"action": "screenshot", "path": str(outside)})

    assert str(excinfo.value) == f"screenshot path {str(outside)!r} resolves outside {str(root)!r}"


async def test_a_macro_call_reaching_simple_dispatch_is_refused_loudly() -> None:
    with pytest.raises(RuntimeError) as excinfo:
        await _dispatch(_Session(), {"action": "macro_call", "name": "child"})

    assert str(excinfo.value) == (
        "macro_call requires _dispatch_one with invocation_stack; reached simple dispatch incorrectly"
    )


async def test_an_unknown_kind_is_skipped_with_a_diagnosable_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)

    assert await _dispatch(_Session(), {"action": "teleport"}) == (0, 1)

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1, messages
    assert " octowright.macros.unknown_action_kind [" in messages[0]
    assert "kind=teleport" in messages[0]
    assert "instance_id=inst-1" in messages[0]
    assert "hint='kind is not in _ACTION_MAP, _REPLAY_SKIP, or _REPLAY_PASSIVE'" in messages[0]


async def test_fill_by_falls_back_to_the_css_selector_when_its_locator_fails() -> None:
    session = _Session()

    result = await _dispatch(session, {"action": "fill_by", "label": "Email", "selector": "#email", "value": "a"})

    assert result == (1, 0)
    assert session.calls == [
        ("fill_by", {"label": "Email", "value": "a"}),
        ("fill", {"selector": "#email", "value": "a"}),
    ]
