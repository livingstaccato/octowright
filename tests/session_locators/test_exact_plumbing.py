# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``text_exact`` / ``label_exact`` must reach the session from every layer.

``build_locator`` accepting the flags is necessary but not sufficient: an LLM
sets them on the MCP tool, a macro carries them as recorded fields, and an
exported script has to reproduce them. A flag that is silently dropped at any
of those seams is worse than no flag at all -- the caller believes matching is
exact while it stays substring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.export import _py_locator
from octowright.export_ts import _ts_locator
from octowright.macros.substitution import SEMANTIC_LOCATOR_KEYS
from octowright.server.browser import input as _input
from tests._operation_gate_fakes import OperationAwareFake


@pytest.fixture(autouse=True)
def _patch_pool_input(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    monkeypatch.delenv("OCTOWRIGHT_PROFILE", raising=False)
    fake_pool = MagicMock()
    monkeypatch.setattr(_input, "pool", fake_pool)
    return fake_pool


class _FakeSession(OperationAwareFake):
    pass


@pytest.mark.anyio
async def test_browser_click_forwards_text_exact(_patch_pool_input: MagicMock) -> None:
    s = _FakeSession()
    _patch_pool_input.get.return_value = s
    s.click_by = AsyncMock(return_value={"ok": True})

    await _input.browser_click("i", text="Ada Lovelace", text_exact=True)

    assert s.click_by.await_args.kwargs["text_exact"] is True


@pytest.mark.anyio
async def test_browser_fill_forwards_label_exact(_patch_pool_input: MagicMock) -> None:
    s = _FakeSession()
    _patch_pool_input.get.return_value = s
    s.fill_by = AsyncMock(return_value={"ok": True})

    await _input.browser_fill("i", value="v", label="Email", label_exact=True)

    assert s.fill_by.await_args.kwargs["label_exact"] is True


@pytest.mark.anyio
async def test_browser_get_text_by_forwards_text_exact(_patch_pool_input: MagicMock) -> None:
    s = _FakeSession()
    _patch_pool_input.get.return_value = s
    s.get_text_by = AsyncMock(return_value={"ok": True, "text": "x"})

    await _input.browser_get_text_by("i", text="Ada", text_exact=True)

    assert s.get_text_by.await_args.kwargs["text_exact"] is True


@pytest.mark.anyio
async def test_truncated_get_text_by_next_action_preserves_text_exact(_patch_pool_input: MagicMock) -> None:
    """A truncated result's suggested retry must keep the exact flag the caller

    set -- otherwise following next_actions silently widens the match back to
    substring, the exact opposite of what the caller asked for.
    """
    s = _FakeSession()
    _patch_pool_input.get.return_value = s
    s.get_text_by = AsyncMock(return_value={"ok": True, "text": "x" * 20})

    out = await _input.browser_get_text_by("i", text="Ada", text_exact=True, max_chars=5)

    assert out["next_actions"] == [
        {
            "tool": "browser_get_text_by",
            "args": {"instance_id": "i", "full": True, "text": "Ada", "text_exact": True},
        }
    ]


def test_macro_substitution_treats_exact_flags_as_semantic_locator_keys() -> None:
    """Otherwise a recorded exact flag is stripped on macro save/replay."""
    assert "text_exact" in SEMANTIC_LOCATOR_KEYS
    assert "label_exact" in SEMANTIC_LOCATOR_KEYS


def test_python_export_emits_exact_for_text() -> None:
    assert _py_locator({"text": "Ada", "text_exact": True}) == "page.get_by_text('Ada', exact=True)"


def test_python_export_emits_exact_for_label() -> None:
    assert _py_locator({"label": "Email", "label_exact": True}) == "page.get_by_label('Email', exact=True)"


def test_python_export_omits_exact_when_substring() -> None:
    """Keep exported scripts byte-identical to today's for every existing recording."""
    assert _py_locator({"text": "Ada"}) == "page.get_by_text('Ada')"
    assert _py_locator({"label": "Email"}) == "page.get_by_label('Email')"


def test_ts_export_emits_exact_for_text() -> None:
    assert _ts_locator({"text": "Ada", "text_exact": True}) == 'page.getByText("Ada", { exact: true })'


def test_ts_export_omits_exact_when_substring() -> None:
    assert _ts_locator({"text": "Ada"}) == 'page.getByText("Ada")'


def test_browser_fill_exposes_role_exact_like_every_other_locator_tool() -> None:
    """`browser_fill` gained `label_exact` in 0.14.4 but not `role_exact`,
    while `browser_click` and `browser_get_text_by` got both.

    `text`/`text_exact` are correctly absent — a fill targets one field, it
    does not search page text — but `role`/`role_name` ARE accepted, so the
    modifier that disambiguates them has to be too, or a role name that is a
    prefix of another cannot be pinned on a fill at all.
    """
    import inspect

    from octowright.server.browser.input import browser_fill

    params = inspect.signature(browser_fill).parameters
    assert "role_exact" in params, "role/role_name are accepted, so their exact modifier must be too"
    assert "text" not in params, "a fill targets a field, not page text"
