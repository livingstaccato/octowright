# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A view transition Chrome still draws refuses the redacted screenshot, whatever root it is in."""

from __future__ import annotations

from pathlib import Path

import pytest

from octowright import defaults
from octowright.macros.page_devtools import view_transition_pseudo_elements
from tests.test_macro_redacted_screenshot import FakePage, _refused, _shoot


@pytest.fixture(autouse=True)
def _recordings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(defaults, "RECORDINGS_DIR", tmp_path)
    return tmp_path


_TRANSITION = {"pseudoType": "view-transition", "children": [{"pseudoType": "view-transition-group"}]}


def test_view_transition_pseudo_elements_are_found_in_the_document_and_every_shadow_root_but_not_in_frames() -> None:
    tree = {
        "children": [
            {"pseudoElements": [_TRANSITION]},
            {
                "shadowRoots": [
                    {"shadowRootType": "closed", "children": [{"pseudoElements": [{"pseudoType": "view-transition"}]}]}
                ]
            },
            {"contentDocument": {"children": [{"pseudoElements": [{"pseudoType": "view-transition"}]}]}},
            {"pseudoElements": [{"pseudoType": "before"}]},
        ]
    }
    assert sorted(view_transition_pseudo_elements(tree)) == ["view-transition", "view-transition"]


async def test_a_drawn_view_transition_refuses_the_screenshot(tmp_path: Path) -> None:
    document = {
        "children": [{"shadowRoots": [{"shadowRootType": "open", "children": [{"pseudoElements": [_TRANSITION]}]}]}]
    }
    page = FakePage(document=document)
    with pytest.raises(RuntimeError, match="view transition is drawn"):
        await _shoot(page, tmp_path / "shot.png")
    assert not (tmp_path / "shot.png").exists()
    assert page.calls == _refused("redact", "watch", "verify")


async def test_a_view_transition_that_cannot_be_ended_refuses_with_a_runtime_error(tmp_path: Path) -> None:
    page = FakePage()
    page.end_error = Exception("the page never finished its transition")  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="view transition could not be ended"):
        await _shoot(page, tmp_path / "shot.png")
    assert not (tmp_path / "shot.png").exists()
    assert page.calls == ["animations:0", "animations:1", "detach"]
