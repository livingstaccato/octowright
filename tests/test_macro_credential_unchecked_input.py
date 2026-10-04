# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential may reach the page only through an input that checks where it lands.

``fill``/``fill_by``/``type`` carry a credential under the fill-origin check,
bound to the document that receives the value (``octowright.credential_input``).
``press_key``, ``select_option`` and ``a11y_dragdrop``'s key fields put a value
into the page too, but nothing checked them: ``{"action": "press_key", "key":
"{{password}}"}`` on https://evil.example ran, and a key press goes to whatever
document has focus -- a cross-origin iframe included -- so a pre-dispatch read
of the active frame's URL could not vouch for it either. They are refused a
credential outright (``type`` is the checked way to key one in), before the
step dispatches and without naming the value.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.macros.substitution import substitute

SECRET = "hunter2-s3cret"  # pragma: allowlist secret (synthetic fixture)
ARGS = {"password": SECRET, "choice": "blue"}

UNCHECKED = [
    ("press_key", {"key": "{{password}}"}),
    ("select_option", {"selector": "#q", "value": "{{password}}"}),
    ("select_option", {"selector": "#q", "label": "{{password}}"}),
    ("a11y_dragdrop", {"source_selector": "#a", "grab_key": "{{password}}"}),
    ("a11y_dragdrop", {"source_selector": "#a", "nav_key_sequence": ["Tab", "{{password}}"]}),
]


@pytest.mark.parametrize(("kind", "fields"), UNCHECKED)
def test_substitute_refuses_a_credential_in_an_unchecked_input(kind: str, fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"\{\{password\}\}") as caught:
        substitute([{"action": kind, **fields}], dict(ARGS))
    message = str(caught.value)
    assert SECRET not in message
    assert "type" in message  # names the checked alternative


@pytest.mark.parametrize(("kind", "fields"), UNCHECKED)
def test_a_non_credential_arg_there_is_unaffected(kind: str, fields: dict[str, Any]) -> None:
    plain = {
        key: ([v.replace("{{password}}", "{{choice}}") for v in value] if isinstance(value, list) else value)
        for key, value in fields.items()
    }
    plain = {
        key: value.replace("{{password}}", "{{choice}}") if isinstance(value, str) else value
        for key, value in plain.items()
    }
    [action] = substitute([{"action": kind, **plain}], dict(ARGS))
    assert "blue" in repr(action)


def test_sinks_allow_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    [action] = substitute([{"action": "press_key", "key": "{{password}}"}], dict(ARGS))
    assert action["key"] == SECRET


def test_a_nested_step_is_refused_too() -> None:
    with pytest.raises(ValueError, match=r"\{\{password\}\}"):
        substitute([{"action": "try", "actions": [{"action": "press_key", "key": "{{password}}"}]}], dict(ARGS))


@pytest.mark.anyio
async def test_a_run_refuses_before_any_step_dispatches(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from octowright.macros import execution
    from octowright.session.core import BrowserSession

    page = AsyncMock()
    page.url = "https://evil.example/"
    session = BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url=page.url,
        launch_url="https://app.example.test/",
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )
    session.press_key = AsyncMock()  # type: ignore[method-assign]
    actions = [{"action": "press_key", "key": "Tab"}, {"action": "press_key", "key": "{{password}}"}]
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    with pytest.raises(ValueError, match=r"\{\{password\}\}") as caught:
        await execution.run_macro(session, "m", dict(ARGS))
    assert SECRET not in str(caught.value)
    session.press_key.assert_not_awaited()
