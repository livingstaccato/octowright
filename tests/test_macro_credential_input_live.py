# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A credential fill or type behaves like an ordinary one on a trusted page.

The origin check (``octowright.credential_input``) must not cost what the
selector-based fill and type always did: the first of several matches, a field
re-rendered while the fill waits, and keys that follow focus. And a type that
the page moves partway through must stop, not finish on the new origin. Each
is measured on all three engines; the attack cases the check exists for are in
``test_macro_credential_fill_frame_live``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.macros import execution

pytestmark = pytest.mark.live_browser

SECRET = "hunter2-Input-Check!"  # pragma: allowlist secret -- a fixture, never a real credential
OTP = "402913"

_OTP_BOXES = "".join(f'<input class="d" maxlength="1" aria-label="digit {i}">' for i in range(6))
PAGES = {
    "/confirm": '<label for="a">Password</label><input type="password" id="a">'
    '<label for="b">Confirm password</label><input type="password" id="b">',
    # A skeleton the fill waits on, replaced by the real field after a delay.
    "/hydrate": '<div id="c"><label for="pw">Password</label><input type="password" id="pw" disabled></div>'
    "<script>setTimeout(() => { document.getElementById('c').innerHTML = "
    '\'<label for="pw">Password</label><input type="password" id="pw">\'; }, 600)</script>',
    "/otp": _OTP_BOXES + "<script>document.querySelectorAll('.d').forEach((box, i, all) => "
    "box.addEventListener('input', () => { if (all[i + 1]) all[i + 1].focus(); }));</script>",
    "/form": '<input type="password" id="pw" autofocus>',
    "/disabled": '<label for="pw">Password</label><input type="password" id="pw" disabled>',
    "/login": '<label for="pw">Password</label><input type="password" id="pw">',
}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/moves":
            # Leaves for the address in the query once three characters are in.
            target = self.path.split("to=", 1)[1]
            inner = (
                '<input type="password" id="pw"><script>document.getElementById("pw").addEventListener('
                f"'input', (e) => {{ if (e.target.value.length === 3) location.href = {target!r}; }});</script>"
            )
        else:
            inner = PAGES.get(path, "<h1>Signed out</h1>")
        body = f"<!doctype html><html><body>{inner}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


def _serve() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


@pytest.fixture
def trusted() -> Iterator[str]:
    yield from _serve()


@pytest.fixture
def evil() -> Iterator[str]:
    yield from _serve()


@pytest.fixture(params=["chromium", "firefox", "webkit"])
async def session(request: pytest.FixtureRequest, tmp_path: Path, trusted: str) -> Any:
    pool = BrowserPool(recordings_dir=tmp_path)
    try:
        inst = await pool.launch(kind=request.param, headed=False, url=trusted + "/")
    except Exception as exc:  # engine not installed on this host
        await pool.shutdown()
        pytest.skip(f"{request.param} unavailable: {exc}")
    try:
        yield pool.get(inst["instance_id"])
    finally:
        await pool.close(inst["instance_id"], force=True)
        await pool.shutdown()


def _step(kind: str, selector: str, placeholder: str = "{{password}}", **extra: Any) -> dict[str, Any]:
    if kind == "fill":
        return {"action": "fill", "selector": selector, "value": placeholder, **extra}
    if kind == "fill_by":
        return {"action": "fill_by", "label": "Password", "value": placeholder, **extra}
    if kind == "type_keys":
        return {"action": "type", "selector": selector, "text": placeholder, "key_mode": "keys", **extra}
    return {"action": "type", "selector": selector, "text": placeholder, **extra}


async def _run(session: Any, monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]], **args: str) -> Any:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    return await execution.run_macro(session, "m", args or {"password": SECRET})


async def _values(session: Any, selector: str) -> list[str]:
    return list(await session.page.eval_on_selector_all(selector, "els => els.map((el) => el.value)"))


@pytest.mark.parametrize("kind", ["fill", "fill_by", "type", "type_keys"])
async def test_a_selector_with_two_matches_fills_the_first(
    session: Any, trusted: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Password + confirm: the selector (or the label, which "Confirm password" also has) matches both, and the first is filled, as a selector fill does."""
    await _run(session, monkeypatch, [{"action": "navigate", "url": trusted + "/confirm"}, _step(kind, "input")])
    assert await _values(session, "input") == [SECRET, ""]


@pytest.mark.parametrize("kind", ["fill", "fill_by"])
async def test_a_field_re_rendered_while_the_fill_waits_is_still_filled(
    session: Any, trusted: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The disabled skeleton is replaced; the fill follows the selector to the real field."""
    await _run(session, monkeypatch, [{"action": "navigate", "url": trusted + "/hydrate"}, _step(kind, "#pw")])
    assert await _values(session, "#pw") == [SECRET]


@pytest.mark.parametrize("kind", ["type", "type_keys"])
async def test_an_auto_advancing_code_gets_one_digit_per_box(
    session: Any, trusted: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Keys follow focus, as a keyboard's do: the page moves focus after each digit."""
    steps = [{"action": "navigate", "url": trusted + "/otp"}, _step(kind, ".d", "{{otp_code}}")]
    await _run(session, monkeypatch, steps, otp_code=OTP)
    assert await _values(session, ".d") == list(OTP)


@pytest.mark.parametrize("kind", ["type", "type_keys"])
async def test_a_navigation_partway_through_a_type_stops_it(
    session: Any, trusted: str, evil: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """Three characters in, the page leaves for a foreign origin whose field has focus; nothing more is typed."""
    steps = [{"action": "navigate", "url": f"{trusted}/moves?to={evil}/form"}, _step(kind, "#pw", delay_ms=50)]
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run(session, monkeypatch, steps)
    await session.page.wait_for_url(evil + "/form")
    assert await _values(session, "#pw") == [""]


@pytest.mark.parametrize("kind", ["fill", "fill_by"])
async def test_a_navigation_while_the_fill_waits_on_a_disabled_field_is_refused(
    session: Any, trusted: str, evil: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """The field exists on the trusted page, so the first check passes; the page then moves.

    A plain selector fill waits for the field to be enabled and then fills
    the foreign page's field: measured on all three engines. The checked fill
    is tied to the element it checked, so the navigation detaches it, and the
    second check refuses the new document.
    """
    move = {"action": "evaluate", "expression": f"setTimeout(() => {{ location.href = '{evil}/login'; }}, 700)"}
    steps = [{"action": "navigate", "url": trusted + "/disabled"}, move, _step(kind, "#pw")]
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run(session, monkeypatch, steps)
    await session.page.wait_for_url(evil + "/login")
    assert await _values(session, "#pw") == [""]


async def _run_cli(actions: list[dict[str, Any]], trusted: str) -> None:
    """Run the exported script, which drives Chromium only, so this is its one engine."""
    from octowright.artifacts.script_export import render_macro_cli

    namespace: dict[str, Any] = {}
    exec(
        render_macro_cli(name="m", macro={"parameters": ["password"], "actions": actions}, include_evidence=False),
        namespace,
    )
    try:
        await namespace["run_m"](password=SECRET, trusted_origins=(trusted,))
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(f"chromium unavailable: {exc}")
        raise


@pytest.mark.parametrize("kind", ["fill", "type"])
async def test_the_exported_cli_fills_the_first_of_two_matches(trusted: str, kind: str) -> None:
    lengths = "[...document.querySelectorAll('input')].map((el) => el.value.length).join()"
    probe = {"action": "expect_js", "expression": lengths, "equals": f"{len(SECRET)},0"}
    await _run_cli([{"action": "navigate", "url": trusted + "/confirm"}, _step(kind, "input"), probe], trusted)


async def test_the_exported_cli_stops_a_type_the_page_moves_partway(trusted: str, evil: str) -> None:
    actions = [{"action": "navigate", "url": f"{trusted}/moves?to={evil}/form"}, _step("type", "#pw", delay_ms=50)]
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run_cli(actions, trusted)
