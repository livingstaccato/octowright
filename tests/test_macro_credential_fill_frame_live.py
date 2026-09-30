# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The credential-fill origin check reads the document the value is typed into.

It read the active frame's URL once, before dispatch, and two things then
decided where the value actually went, both measured here on every engine:

* **A navigation during the fill's own wait.** Playwright's actionability wait
  survives a navigation, so a page that moves itself after the check had the
  field filled on the new origin.
* **A selector that enters a frame.** ``iframe >> internal:control=enter-frame
  >> #pw`` is resolved inside the child frame, whose origin was never read.

Two loopback servers on different ports are two origins; the session is
launched on the first, so the second stands in for an attacker's site.
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

SECRET = "hunter2-Frame-Check!"  # pragma: allowlist secret -- a fixture, never a real credential
FORM = b"""<!doctype html><html><body>
<label for="pw">Password</label><input type="password" id="pw"></body></html>"""
BLANK = b"<!doctype html><html><body><h1>Signed out</h1></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/form"):
            body = FORM
        elif self.path.startswith("/framed"):
            body = b'<!doctype html><html><body><iframe src="/form"></iframe></body></html>'
        else:
            body = BLANK
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


def _typing_step(kind: str, selector: str = "#pw", **extra: Any) -> dict[str, Any]:
    if kind == "fill":
        return {"action": "fill", "selector": selector, "value": "{{password}}", **extra}
    if kind == "fill_by":
        return {"action": "fill_by", "label": "Password", "value": "{{password}}", **extra}
    if kind == "type_keys":
        return {"action": "type", "selector": selector, "text": "{{password}}", "key_mode": "keys", **extra}
    return {"action": "type", "selector": selector, "text": "{{password}}", **extra}


async def _run(session: Any, monkeypatch: pytest.MonkeyPatch, actions: list[dict[str, Any]]) -> Any:
    monkeypatch.setattr(execution, "load_macro", lambda name: {"name": name, "actions": actions})
    return await execution.run_macro(session, "m", {"password": SECRET})


async def _typed_values(session: Any, origin: str) -> list[str]:
    """What every ``#pw`` in a frame on *origin* holds."""
    values = []
    for frame in session.page.frames:
        if frame.url.startswith(origin):
            values.append(
                await frame.evaluate("() => { const el = document.querySelector('#pw'); return el ? el.value : ''; }")
            )
    return values


def _move_later(url: str) -> dict[str, Any]:
    # No placeholder, so the sink guard has nothing to say about it. The delay
    # outlasts the pre-dispatch read, and the fill's wait outlasts the delay.
    return {"action": "evaluate", "expression": f"setTimeout(() => {{ location.href = {url!r}; }}, 700)"}


@pytest.mark.parametrize("kind", ["fill", "fill_by", "type", "type_keys"])
async def test_a_navigation_during_the_wait_does_not_carry_the_credential(
    session: Any, evil: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run(session, monkeypatch, [_move_later(evil + "/form"), _typing_step(kind)])
    await session.page.wait_for_url(evil + "/form")
    assert SECRET not in await _typed_values(session, evil)


async def test_a_selector_that_enters_a_foreign_frame_is_refused(
    session: Any, evil: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    inject = {"action": "evaluate", "expression": f"document.body.innerHTML = '<iframe src=\"{evil}/form\"></iframe>'"}
    step = _typing_step("fill", "iframe >> internal:control=enter-frame >> #pw")
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run(session, monkeypatch, [inject, step])
    assert SECRET not in await _typed_values(session, evil)


@pytest.mark.parametrize("kind", ["fill", "type", "type_keys"])
async def test_a_foreign_frame_the_step_allows_is_typed_into(
    session: Any, evil: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    inject = {"action": "evaluate", "expression": f"document.body.innerHTML = '<iframe src=\"{evil}/form\"></iframe>'"}
    step = _typing_step(kind, "iframe >> internal:control=enter-frame >> #pw", allowed_origins=[evil])
    await _run(session, monkeypatch, [inject, step])
    assert await _typed_values(session, evil) == [SECRET]


@pytest.mark.parametrize("kind", ["fill", "fill_by", "type", "type_keys"])
async def test_the_own_origin_is_typed_into_directly_and_through_a_frame(
    session: Any, trusted: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    await _run(session, monkeypatch, [{"action": "navigate", "url": trusted + "/form"}, _typing_step(kind)])
    assert await _typed_values(session, trusted) == [SECRET]
    if kind == "fill_by":
        return  # a label locator does not cross a frame boundary
    step = _typing_step(kind, "iframe >> internal:control=enter-frame >> #pw")
    await _run(session, monkeypatch, [{"action": "navigate", "url": trusted + "/framed"}, step])
    assert SECRET in await _typed_values(session, trusted)


async def test_the_exported_cli_checks_the_frame_it_types_into(trusted: str, evil: str) -> None:
    """The generated script drives Chromium only, so this is its one engine."""
    from octowright.artifacts.script_export import render_macro_cli

    actions = [
        {"action": "navigate", "url": trusted + "/"},
        {"action": "evaluate", "expression": f"document.body.innerHTML = '<iframe src=\"{evil}/form\"></iframe>'"},
        _typing_step("fill", "iframe >> internal:control=enter-frame >> #pw"),
    ]
    namespace: dict[str, Any] = {}
    exec(
        render_macro_cli(name="m", macro={"parameters": ["password"], "actions": actions}, include_evidence=False),
        namespace,
    )
    try:
        with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
            await namespace["run_m"](password=SECRET, trusted_origins=(trusted,))
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(f"chromium unavailable: {exc}")
        raise


async def test_the_exported_cli_checks_after_a_navigation_during_the_wait(trusted: str, evil: str) -> None:
    from octowright.artifacts.script_export import render_macro_cli

    actions = [{"action": "navigate", "url": trusted + "/"}, _move_later(evil + "/form"), _typing_step("fill")]
    namespace: dict[str, Any] = {}
    exec(
        render_macro_cli(name="m", macro={"parameters": ["password"], "actions": actions}, include_evidence=False),
        namespace,
    )
    try:
        with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
            await namespace["run_m"](password=SECRET, trusted_origins=(trusted,))
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            pytest.skip(f"chromium unavailable: {exc}")
        raise
