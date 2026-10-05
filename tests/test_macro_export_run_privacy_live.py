# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""An exported macro CLI's own writes carry no cleartext, rendered AND run (#248, Part 0).

The script ``macro_export_cli`` writes is its own program: it prints a log,
and with ``--evidence-dir`` writes ``action-log.jsonl``, ``result.json`` and
``evidence.json``. Earlier tests exec'd the rendered source against a fake
Playwright; this one writes the script to disk and runs it in a child
interpreter against real headless Chromium and a local page that echoes what
is typed into it, on the passing path and on a failing one whose error quotes
the page text. Every file it writes, and its stdout and stderr, are searched
for the classified values in every serialized spelling.

The values mix quotes, a backslash, non-ASCII and HTML- and URL-special
characters, so the raw, JSON-escaped, Python-repr, HTML-escaped and
percent-encoded spellings all differ.
"""

from __future__ import annotations

import html
import json
import os
import subprocess  # nosec B404 -- runs this interpreter on a script this test wrote
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus

import pytest

from octowright.artifacts.script_export import render_macro_cli

PASSWORD = "Pw\"q'é<9>&x/ y\\z-7Kd"  # pragma: allowlist secret
DISPLAY = "Natl Id 'ü' 4471-X"
_UNAVAILABLE = ("executable doesn't exist", "missing x server", "cannot open display", "host system is missing")

_PAGE = b"""<!doctype html><title>form</title>
<input type=password id=pw><input id=display><p id=echo></p>
<script>
for (const id of ["pw", "display"]) {
  document.getElementById(id).addEventListener("input", (e) => {
    document.getElementById("echo").textContent = "typed " + e.target.value;
    console.log("typed " + e.target.value);
  });
}
</script>"""


@pytest.fixture
def origin() -> Iterator[str]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_PAGE)))
            self.end_headers()
            self.wfile.write(_PAGE)

        def log_message(self, *_: Any) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _one_level(value: str) -> set[str]:
    return {
        value,
        value.lower(),
        value.upper(),
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False)[1:-1],
        # Python's repr inside a single-quoted literal (both quotes present)
        # and inside a double-quoted one: an error message's ``{actual!r}``.
        repr(value + "'\"")[1:-4],
        repr(value + "'\"")[1:-4].replace("\\'", "'"),
        html.escape(value),
        html.escape(value, quote=False),
        quote(value, safe=""),
        quote_plus(value, safe=""),
    }


def _spellings(value: str) -> set[str]:
    # Two levels: a repr'd error message is then JSON-escaped into result.json.
    out = {twice for once in _one_level(value) for twice in _one_level(once)}
    return {spelling for spelling in out if spelling}


def _leaks(text: str) -> list[str]:
    return sorted(
        f"{name} as {spelling!r}"
        for name, value in (("password", PASSWORD), ("display", DISPLAY))
        for spelling in _spellings(value)
        if spelling in text
    )


def _macro(origin: str, *, fail: bool) -> dict[str, Any]:
    actions: list[dict[str, Any]] = [
        {"action": "navigate", "url": f"{origin}/form"},
        {"action": "fill", "selector": "#display", "value": "{{display}}"},
        {"action": "fill", "selector": "#pw", "value": "{{password}}"},
    ]
    if fail:
        # Fails quoting the echoed page text -- the password, typed last -- with
        # Python's repr, whose escaping of a value holding both quotes and a
        # backslash is no JSON spelling.
        actions.append({"action": "expect_text", "selector": "#echo", "text": "never", "mode": "equals"})
    return {
        "name": "export-run",
        "parameters": ["password", "display"],
        # display is not a name the classifier knows; the macro declares it.
        "parameter_specs": {"display": {"sensitive": True}},
        "actions": actions,
    }


@pytest.mark.live_browser
@pytest.mark.parametrize("fail", [False, True], ids=["passes", "fails"])
def test_an_exported_cli_run_writes_no_cleartext(tmp_path: Path, origin: str, fail: bool) -> None:
    pytest.importorskip("playwright")
    script = tmp_path / "export_run.py"
    script.write_text(render_macro_cli(name="export-run", macro=_macro(origin, fail=fail)), encoding="utf-8")
    evidence = tmp_path / "evidence"

    proc = subprocess.run(  # nosec B603 -- fixed argv, no shell
        [sys.executable, str(script), "--password", PASSWORD, "--display", DISPLAY,
         "--evidence-dir", str(evidence), "--trusted-origin", origin],
        env=dict(os.environ), cwd=tmp_path, capture_output=True, encoding="utf-8", errors="replace",
        timeout=180, check=False,
    )  # fmt: skip
    output = proc.stdout + proc.stderr
    if proc.returncode != 0 and any(marker in output.lower() for marker in _UNAVAILABLE):
        pytest.skip(f"chromium unavailable: {output[-300:]}")

    assert proc.returncode == (1 if fail else 0), output[-2000:]
    written = {path.name: path.read_text(encoding="utf-8") for path in evidence.iterdir()}
    assert set(written) == {"action-log.jsonl", "result.json", "evidence.json"}
    if fail:
        assert "text mismatch" in json.loads(written["result.json"])["error"]
    else:
        assert json.loads(written["result.json"]) == {"executed": 3, "skipped": 0}
    for name, text in {**written, "stdout": proc.stdout, "stderr": proc.stderr}.items():
        assert _leaks(text) == [], name
