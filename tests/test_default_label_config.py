# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Regression for the YAML-null config bug flagged in PR review.

PyYAML parses a bare ``label:`` (no value) as ``None``, and ``dict.get(key, "")``
returns that ``None`` (the key is present), so ``str(None)`` became the literal
string ``"None"``. The fix is ``... or ""``. Same bug class lives in
``server/browser/lifecycle.py`` for ``persona:`` / ``profile:``.
"""

from __future__ import annotations

import pytest

from octowright import defaults


@pytest.fixture(autouse=True)
def _isolate_label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_DEFAULT_LABEL", raising=False)
    defaults.get_default_label.cache_clear()
    yield  # type: ignore[misc]
    defaults.get_default_label.cache_clear()


def test_null_config_label_does_not_become_string_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "_read_project_config", lambda: {"label": None})
    label = defaults.get_default_label()
    assert label != "None"
    assert label  # falls through to git repo name / username


def test_empty_config_label_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "_read_project_config", lambda: {"label": ""})
    assert defaults.get_default_label() != "None"


def test_config_label_used_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults, "_read_project_config", lambda: {"label": "myproj"})
    assert defaults.get_default_label() == "myproj"
