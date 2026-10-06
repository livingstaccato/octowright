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

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from octowright.browser_pool.pool import BrowserPool
from octowright.macros import execution
from octowright.session import core_locator_mixin, core_page_mixin

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
        elif self.path.startswith(("/embed?", "/moves?")):
            # The page does it, not the macro: a run that types a credential
            # runs no page code of its own.
            target = json.dumps(parse_qs(urlsplit(self.path).query)["to"][0])
            if self.path.startswith("/embed?"):
                body = f"<!doctype html><html><body><script>document.write('<iframe src=' + {target} + '></iframe>')</script></body></html>".encode()
            else:
                body = BLANK.replace(
                    b"</body>",
                    f"<script>setTimeout(() => {{ location.href = {target}; }}, 700)</script></body>".encode(),
                )
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


#: How long the foreign page may take to show its field, in a frame or after
#: the page moved itself. Generous on purpose: it is the tests' precondition,
#: not what they measure. A windows-2025 Firefox took ~14.5s to show the
#: frame's field (below), and on 2026-10-05 a Windows Firefox took ~11.8s for
#: one ordinary navigation in this module.
FOREIGN_PAGE_READY_TIMEOUT_MS = 60_000


async def _inject_foreign_frame(session: Any, monkeypatch: pytest.MonkeyPatch, evil: str) -> None:
    """Put *evil*'s form in an iframe and wait for its field, outside the typing step.

    In one macro, the frame's load came out of the typing step's budget: a step
    waits for its element under the action timeout and types in what is left.
    A windows-2025 Firefox took ~14.5s of the 15s default to show the frame's
    ``#pw``, so 20 per-key round trips through the frame got 500ms and the
    allowed case failed with "did not finish within 500ms". The same cases take
    ~0.3-0.5s to type on Linux once the field is there.
    """
    inject = {"action": "evaluate", "expression": f"document.body.innerHTML = '<iframe src=\"{evil}/form\"></iframe>'"}
    await _run(session, monkeypatch, [inject])
    field = session.page.frame_locator("iframe").locator("#pw")
    await field.wait_for(state="attached", timeout=FOREIGN_PAGE_READY_TIMEOUT_MS)


def _move_later(origin: str, url: str) -> dict[str, Any]:
    # A page on *origin* that moves itself to *url*. The delay outlasts the
    # pre-dispatch read, and the fill's wait outlasts the delay.
    return {"action": "navigate", "url": f"{origin}/moves?to={quote(url, safe='')}"}


def _step_waits_out_the_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the typing step a budget the page's own move to the foreign form fits in.

    The refusal is decided on the document that receives the value, so it can
    come only once the foreign form exists; until then there is nothing to
    refuse. The step's attached-wait (the action timeout, 15s) therefore had to
    cover the page's 700ms timer AND the foreign page's whole load. A Windows
    Firefox that took ~11.8s per navigation spent the 15s with the foreign
    navigation still in flight, and the step failed with Playwright's
    "waiting for <foreign> navigation to finish" timeout: nothing typed, but
    nothing the test could tell from a check that never ran. Reproduced on all
    three engines by delaying the foreign form's response 16s. That load is the
    precondition, so it gets the precondition's bound; what is asserted is
    still the refusal, which comes as soon as the form is there.
    """
    for module in (core_page_mixin, core_locator_mixin):  # type/fill, and fill_by
        monkeypatch.setattr(module, "DEFAULT_ACTION_TIMEOUT_MS", FOREIGN_PAGE_READY_TIMEOUT_MS)


@pytest.mark.parametrize("kind", ["fill", "fill_by", "type", "type_keys"])
async def test_a_navigation_during_the_wait_does_not_carry_the_credential(
    session: Any, trusted: str, evil: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _step_waits_out_the_move(monkeypatch)
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run(session, monkeypatch, [_move_later(trusted, evil + "/form"), _typing_step(kind)])
    await session.page.wait_for_url(evil + "/form")
    assert SECRET not in await _typed_values(session, evil)


async def test_a_selector_that_enters_a_foreign_frame_is_refused(
    session: Any, evil: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _inject_foreign_frame(session, monkeypatch, evil)
    step = _typing_step("fill", "iframe >> internal:control=enter-frame >> #pw")
    with pytest.raises(RuntimeError, match=r"credential arg \{\{password\}\}"):
        await _run(session, monkeypatch, [step])
    assert SECRET not in await _typed_values(session, evil)


@pytest.mark.parametrize("kind", ["fill", "type", "type_keys"])
async def test_a_foreign_frame_the_step_allows_is_typed_into(
    session: Any, evil: str, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    await _inject_foreign_frame(session, monkeypatch, evil)
    step = _typing_step(kind, "iframe >> internal:control=enter-frame >> #pw", allowed_origins=[evil])
    await _run(session, monkeypatch, [step])
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
        {"action": "navigate", "url": f"{trusted}/embed?to={quote(evil + '/form', safe='')}"},
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

    actions = [_move_later(trusted, evil + "/form"), _typing_step("fill")]
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
