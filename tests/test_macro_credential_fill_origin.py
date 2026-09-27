# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential is typed only into a page on the session's own origin.

``value``/``text`` of a fill is the intended destination of a credential, so the
sink guard lets ``{{password}}`` through -- but it never asked WHICH page it was
typed into. A shared macro that navigates to https://evil.example/login and
then fills ``{{password}}`` handed the secret to attacker JavaScript with no
sink ever named (c-0001). Before a fill/type whose value came from a
credential-tier arg, the page's current origin is now compared with the
session's own origins (launch URL, persona base_url), plus any the step lists
literally in ``allowed_origins`` for a sign-in hop.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from octowright.credential_sinks import CREDENTIAL_FILL_MARKER
from octowright.macros.lint import lint_macro
from octowright.macros.substitution import substitute

SECRET = "hunter2-s3cret"  # pragma: allowlist secret (synthetic fixture)
ARGS = {"password": SECRET, "email": "me@example.test"}


def _session(tmp_path: Any, *, launch: str, current: str) -> Any:
    from octowright.session.core import BrowserSession

    page = AsyncMock()
    page.url = current
    session = BrowserSession(
        instance_id="test",
        kind="chromium",
        label="t",
        url=current,
        launch_url=launch,
        page=page,
        context=MagicMock(),
        browser=MagicMock(),
        log_path=tmp_path / "t.jsonl",
        recorder=MagicMock(),
    )
    session.fill = AsyncMock()  # type: ignore[method-assign]

    async def _fill_by(value: str, **finders: Any) -> dict[str, Any]:
        # Like the real one: no finder, no element -- replay then falls back to fill.
        if not {"role", "label", "text", "test_id"} & set(finders):
            raise ValueError("no finder")
        return {"ok": True}

    session.fill_by = AsyncMock(side_effect=_fill_by)  # type: ignore[method-assign]
    session.type_text = AsyncMock()  # type: ignore[method-assign]
    return session


def _step(kind: str, placeholder: str = "{{password}}", **extra: Any) -> dict[str, Any]:
    if kind == "fill":
        return {"action": "fill", "selector": "#pw", "value": placeholder, **extra}
    if kind == "fill_by":
        return {"action": "fill_by", "label": "Password", "value": placeholder, **extra}
    return {"action": "type", "selector": "#pw", "text": placeholder, **extra}


def _typed(session: Any, kind: str) -> AsyncMock:
    return {"fill": session.fill, "fill_by": session.fill_by, "type": session.type_text}[kind]


async def _run(monkeypatch: pytest.MonkeyPatch, session: Any, actions: list[dict[str, Any]]) -> Any:
    from octowright.macros import execution

    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    return await execution.run_macro(session, "m", dict(ARGS))


KINDS = ["fill", "fill_by", "type"]


@pytest.mark.anyio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_credential_typed_on_the_launch_origin_runs(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/login")
    await _run(monkeypatch, session, [_step(kind)])
    typed = _typed(session, kind)
    typed.assert_awaited_once()
    # Neither guard input reaches the Playwright call.
    assert CREDENTIAL_FILL_MARKER not in typed.await_args.kwargs
    assert "allowed_origins" not in typed.await_args.kwargs


@pytest.mark.anyio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_credential_typed_on_a_foreign_origin_is_refused(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/login?x=1")
    with pytest.raises(RuntimeError, match=r"https://evil\.example") as caught:
        await _run(monkeypatch, session, [_step(kind)])
    message = str(caught.value)
    assert "allowed_origins" in message
    assert "OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS" in message
    assert SECRET not in message
    assert "/login" not in message  # the origin, never the path
    _typed(session, kind).assert_not_awaited()


@pytest.mark.anyio
async def test_another_port_on_the_launch_host_is_foreign(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch="http://localhost:3000/", current="http://localhost:45678/")
    with pytest.raises(RuntimeError, match=r"localhost:45678"):
        await _run(monkeypatch, session, [_step("fill")])
    session.fill.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("kind", KINDS)
async def test_a_step_listing_the_origin_may_type_there(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://login.idp.example/authorize")
    await _run(monkeypatch, session, [_step(kind, allowed_origins=["https://login.idp.example"])])
    typed = _typed(session, kind)
    typed.assert_awaited_once()
    assert "allowed_origins" not in typed.await_args.kwargs


@pytest.mark.anyio
async def test_allowed_origins_is_per_step(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://login.idp.example/")
    actions = [_step("fill", allowed_origins=["https://login.idp.example"]), _step("fill")]
    with pytest.raises(RuntimeError, match=r"login\.idp\.example"):
        await _run(monkeypatch, session, actions)
    assert session.fill.await_count == 1


@pytest.mark.anyio
async def test_warn_mode_runs_the_step_and_marks_the_result(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/login")
    result = await _run(monkeypatch, session, [{"action": "navigate_back"}, _step("fill")])
    session.fill.assert_awaited_once()
    assert result["credential_fill_offsite"] == [{"step": 1, "action": "fill", "origin": "https://evil.example"}]
    assert SECRET not in repr(result)


@pytest.mark.anyio
async def test_a_clean_run_carries_no_marker(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    result = await _run(monkeypatch, session, [_step("fill")])
    assert "credential_fill_offsite" not in result


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["bogus", "", "WARNING", "off"])
async def test_an_unknown_mode_fails_closed(tmp_path: Any, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", mode)
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/")
    with pytest.raises(RuntimeError, match=r"evil\.example"):
        await _run(monkeypatch, session, [_step("fill")])
    session.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_sinks_allow_turns_the_origin_check_off(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_SINKS", "allow")
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/")
    await _run(monkeypatch, session, [_step("fill")])
    session.fill.assert_awaited_once()


@pytest.mark.anyio
async def test_a_non_credential_arg_on_a_foreign_origin_is_unaffected(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/")
    await _run(monkeypatch, session, [_step("fill", "{{email}}")])
    session.fill.assert_awaited_once()


@pytest.mark.anyio
async def test_a_credential_in_a_try_body_is_checked(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """``try`` suppresses its body's errors, the refusal included; the fill still never runs."""
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/")
    await _run(monkeypatch, session, [{"action": "try", "actions": [_step("fill")]}])
    session.fill.assert_not_awaited()


@pytest.mark.anyio
async def test_the_check_reads_the_active_frame(tmp_path: Any) -> None:
    """A fill lands in the active iframe's document, whose origin is the one that reads it."""
    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    session.active_frame = MagicMock(url="https://evil.example/embed")
    assert await session.target_url() == "https://evil.example/embed"


# --- substitution marks the step; nothing in the macro can -----------------------------


def test_substitute_marks_only_credential_typed_values() -> None:
    marked, identity, spoofed = substitute(
        [_step("fill"), _step("fill", "{{email}}"), _step("fill", "{{email}}", **{CREDENTIAL_FILL_MARKER: []})],
        ARGS,
    )
    assert marked[CREDENTIAL_FILL_MARKER] == ["password"]
    assert CREDENTIAL_FILL_MARKER not in identity
    assert CREDENTIAL_FILL_MARKER not in spoofed


def test_a_macro_cannot_unmark_a_credential_step() -> None:
    [action] = substitute([_step("fill", **{CREDENTIAL_FILL_MARKER: None})], ARGS)
    assert action[CREDENTIAL_FILL_MARKER] == ["password"]


@pytest.mark.parametrize(
    "allowed",
    [
        ["https://{{idp}}"],  # chosen at run time
        ["https://*.idp.example"],  # a wildcard is not an origin
        ["https://login.idp.example/authorize"],  # a path is not an origin
        ["login.idp.example"],  # no scheme
        ["https://user@login.idp.example"],
        "https://login.idp.example",  # a string, not a list
    ],
)
def test_allowed_origins_must_be_literal_exact_origins(allowed: Any) -> None:
    with pytest.raises(ValueError, match="allowed_origins"):
        substitute([_step("fill", allowed_origins=allowed)], {**ARGS, "idp": "login.idp.example"})


def test_lint_accepts_allowed_origins_on_a_typing_step() -> None:
    for kind in KINDS:
        issues = lint_macro(
            {"name": "t", "parameters": ["password"], "actions": [_step(kind, allowed_origins=["https://a.test"])]}
        )
        assert [i for i in issues if i.code == "unknown_field"] == [], kind


def test_lint_reports_a_malformed_allowed_origins() -> None:
    issues = lint_macro(
        {"name": "t", "parameters": ["password"], "actions": [_step("fill", allowed_origins=["https://*.a.test"])]}
    )
    assert any("allowed_origins" in i.message for i in issues)


# --- the check stays bound to the step's dispatch ---------------------------------------


@pytest.mark.anyio
async def test_the_check_is_bound_for_the_dispatch_of_a_marked_step_only(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session re-checks on the element's own frame; see ``session.fill_origin``."""
    from octowright.session.fill_origin import pending_fill_origin_check

    session = _session(tmp_path, launch="https://app.example.test/", current="https://app.example.test/")
    seen: list[Any] = []
    session.fill.side_effect = lambda *a, **kw: seen.append(pending_fill_origin_check())
    await _run(monkeypatch, session, [_step("fill"), _step("fill", "{{email}}")])
    marked, unmarked = seen
    assert unmarked is None
    assert marked is not None
    marked("https://app.example.test/inner")  # the own origin passes
    with pytest.raises(ValueError, match=r"https://evil\.example"):
        marked("https://evil.example/frame")
    assert pending_fill_origin_check() is None


@pytest.mark.anyio
async def test_warn_mode_records_an_origin_once_however_often_it_is_read(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octowright.session.fill_origin import pending_fill_origin_check

    monkeypatch.setenv("OCTOWRIGHT_MACRO_CREDENTIAL_FILL_ORIGINS", "warn")
    session = _session(tmp_path, launch="https://app.example.test/", current="https://evil.example/login")

    def _element_check(*_a: Any, **_kw: Any) -> None:
        check = pending_fill_origin_check()
        check("https://evil.example/login")  # the same origin the pre-check saw
        check("https://other.example/frame")  # a frame the pre-check never read

    session.fill.side_effect = _element_check
    result = await _run(monkeypatch, session, [_step("fill")])
    assert result["credential_fill_offsite"] == [
        {"step": 0, "action": "fill", "origin": "https://evil.example"},
        {"step": 0, "action": "fill", "origin": "https://other.example"},
    ]
