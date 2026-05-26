# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exercise tests for octowright.export.

The exporter turns a JSONL recording into a runnable Playwright script. The tests
build synthetic recordings (no live browser needed), call ``export_script``,
then compile the Python output to confirm it's syntactically valid and assert
the relevant Playwright calls show up. TS output is shape-checked against the
Playwright TS API surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.export import export_script


def _write_recording(path: Path, entries: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return path


def _python_compiles(source: str) -> bool:
    """Sanity check: compile to bytecode without executing — catches syntax errors."""
    compile(source, "<exported>", "exec")
    return True


# ---------------------------------------------------------------------------
# argument validation
# ---------------------------------------------------------------------------


def test_rejects_unknown_format(tmp_path: Path) -> None:
    log = _write_recording(tmp_path / "r.jsonl", [{"action": "navigate", "url": "https://x"}])
    with pytest.raises(ValueError, match="must be 'python' or 'ts'"):
        export_script(log, tmp_path / "out.py", fmt="ruby")


# ---------------------------------------------------------------------------
# Python output
# ---------------------------------------------------------------------------


def test_python_export_compiles_with_full_action_set(tmp_path: Path) -> None:
    """One recording exercising every supported action — output must compile."""
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {
                "action": "launch",
                "kind": "webkit",
                "url": "https://octowright.com",
                "headed": True,
                "viewport": {"w": 1024, "h": 768},
            },
            {"action": "navigate", "url": "https://octowright.com/home"},
            {"action": "click", "selector": "#login"},
            {"action": "fill", "selector": "input[name=email]", "value": "a@b.com"},
            {"action": "type", "selector": "input[name=q]", "text": "hi", "delay_ms": 30},
            {"action": "press_key", "key": "Enter"},
            {"action": "screenshot", "path": "/tmp/x.png"},
            {"action": "evaluate", "expression": "document.title"},
            {"action": "wait_for", "selector": ".result"},
            {"action": "wait_for", "text": "Done"},
            {"action": "wait_for"},  # network-idle branch
        ],
    )
    out = export_script(log, tmp_path / "out.py", fmt="python")

    src = out.read_text()
    assert _python_compiles(src)

    # Spot-check that each action mapped to the expected Playwright API call.
    assert "async with async_playwright() as p:" in src
    assert "await p.webkit.launch(headless=False)" in src
    assert "viewport={'width': 1024, 'height': 768}" in src
    assert "await page.goto(_resolve_bundle_url('https://octowright.com'))" in src  # initial nav from launch
    assert "await page.goto(_resolve_bundle_url('https://octowright.com/home'))" in src  # explicit navigate
    assert "await page.click('#login')" in src
    assert "await page.fill('input[name=email]', 'a@b.com')" in src
    assert "await page.type('input[name=q]', 'hi', delay=30)" in src
    assert "await page.keyboard.press('Enter')" in src
    assert "await page.screenshot(path='/tmp/x.png')" in src
    assert "await page.evaluate('document.title')" in src
    assert "await page.wait_for_selector('.result')" in src
    assert "await page.wait_for_function" in src  # text-wait branch
    assert "await page.wait_for_load_state('networkidle')" in src
    # Footer must include a close path that handles both browser and persistent ctx.
    assert 'if __name__ == "__main__":' in src
    assert "asyncio.run(main())" in src


def test_python_export_persistent_context_branch(tmp_path: Path) -> None:
    """When the launch entry has user_data_dir, the export must use launch_persistent_context."""
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {
                "action": "launch",
                "kind": "chromium",
                "url": "https://app.example",
                "headed": False,
                "viewport": {"w": 800, "h": 600},
                "user_data_dir": "/tmp/profiles/chromium/dante",
            },
            {"action": "click", "selector": "#x"},
        ],
    )
    out = export_script(log, tmp_path / "out.py", fmt="python")
    src = out.read_text()
    assert _python_compiles(src)
    # launch_persistent_context not launch().
    assert "p.chromium.launch_persistent_context(" in src
    assert "'/tmp/profiles/chromium/dante'" in src
    assert "headless=True" in src  # headed=False inverts to headless=True
    # Persistent path must also pre-populate the page from ctx.pages[0].
    assert "ctx.pages[0] if ctx.pages else await ctx.new_page()" in src
    # Persistent path must NOT use new_context.
    assert "browser = None" in src
    # Ephemeral-only call should be absent.
    assert "browser.new_context(" not in src


def test_python_export_resolves_bundle_scheme_urls(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [{"action": "launch", "kind": "webkit", "url": "bundle://seed/welcome.html?slot=0", "headed": True}],
    )
    out = export_script(log, tmp_path / "artifacts" / "replay.py", fmt="python")
    src = out.read_text()

    assert _python_compiles(src)
    assert "from pathlib import Path" in src
    assert "def _resolve_bundle_url(raw: str) -> str:" in src
    assert "Path(__file__).resolve().parents[1]" in src
    assert "await page.goto(_resolve_bundle_url('bundle://seed/welcome.html?slot=0'))" in src


def test_python_export_uses_default_viewport_when_omitted(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [{"action": "launch", "kind": "firefox", "url": "https://x.io", "headed": True}],
    )
    out = export_script(log, tmp_path / "out.py", fmt="python")
    assert "viewport={'width': 1280, 'height': 800}" in out.read_text()


def test_python_export_uses_default_viewport_when_fluid_recorded(tmp_path: Path) -> None:
    log = tmp_path / "fluid.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00Z",
                "action": "launch",
                "kind": "chromium",
                "url": "https://octowright.com",
                "viewport": {"mode": "fluid"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.py"
    export_script(log, out, fmt="python")
    assert "viewport={'width': 1280, 'height': 800}" in out.read_text()


def test_python_export_skips_blank_and_unknown_lines(tmp_path: Path) -> None:
    """Blank lines, unknown actions, and lifecycle entries we don't translate
    must not break export and must not appear in output."""
    log = tmp_path / "r.jsonl"
    log.write_text(
        "\n"
        + json.dumps({"action": "launch", "kind": "webkit", "url": "https://x", "headed": True})
        + "\n"
        + "\n"  # blank
        + json.dumps({"action": "snapshot", "selector": "html"})  # not exported
        + "\n"
        + json.dumps({"action": "click", "selector": "#a"})
        + "\n"
        + json.dumps({"action": "totally_unknown_action", "value": 42})
        + "\n",
        encoding="utf-8",
    )
    out = export_script(log, tmp_path / "out.py", fmt="python")
    src = out.read_text()
    assert _python_compiles(src)
    assert "totally_unknown_action" not in src
    assert "snapshot" not in src
    assert "page.click('#a')" in src


def test_python_export_prefers_semantic_locator_with_selector_fallback(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {"action": "click", "selector": "#login", "role": "button", "role_name": "Log in"},
            {"action": "fill", "selector": "#email", "value": "me@octowright.test", "label": "Email"},
        ],
    )
    out = export_script(log, tmp_path / "out.py", fmt="python")
    src = out.read_text()

    assert _python_compiles(src)
    assert "await page.get_by_role('button', name='Log in').click()" in src
    assert "await page.click('#login')" in src
    assert "await page.get_by_label('Email').fill('me@octowright.test')" in src
    assert "await page.fill('#email', 'me@octowright.test')" in src


def test_python_export_keeps_native_semantic_actions(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {"action": "click_by", "role": "button", "role_name": "Log in"},
            {"action": "fill_by", "value": "me@octowright.test", "label": "Email"},
        ],
    )
    out = export_script(log, tmp_path / "out.py", fmt="python")
    src = out.read_text()

    assert _python_compiles(src)
    assert "await page.get_by_role('button', name='Log in').click()" in src
    assert "await page.get_by_label('Email').fill('me@octowright.test')" in src
    assert "page.click(" not in src
    assert "page.fill(" not in src


def test_python_export_creates_parent_dir(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [{"action": "launch", "kind": "webkit", "url": "https://x", "headed": True}],
    )
    nested_out = tmp_path / "nested" / "deep" / "out.py"
    assert not nested_out.parent.exists()
    export_script(log, nested_out, fmt="python")
    assert nested_out.exists()


# ---------------------------------------------------------------------------
# TypeScript output
# ---------------------------------------------------------------------------


def test_ts_export_full_action_set(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {
                "action": "launch",
                "kind": "firefox",
                "url": "https://octowright.com",
                "headed": True,
            },
            {"action": "navigate", "url": "https://octowright.com/home"},
            {"action": "click", "selector": "#login"},
            {"action": "fill", "selector": "input[name=email]", "value": "a@b.com"},
            {"action": "type", "selector": "input[name=q]", "text": "hi", "delay_ms": 25},
            {"action": "press_key", "key": "Enter"},
            {"action": "screenshot", "path": "/tmp/x.png"},
            {"action": "evaluate", "expression": "document.title"},
            {"action": "wait_for", "selector": ".result"},
            {"action": "wait_for", "text": "Done"},
            {"action": "wait_for"},
        ],
    )
    out = export_script(log, tmp_path / "out.ts", fmt="ts")
    src = out.read_text()

    # Header imports each engine plus types.
    assert 'import { chromium, firefox, webkit, Browser, BrowserContext, Page } from "playwright";' in src
    # Engine launch path (lowercase headless boolean — ts is case-sensitive).
    assert "browser = await firefox.launch({ headless: false })" in src
    assert 'await page.goto(resolveBundleUrl("https://octowright.com"));' in src  # initial nav
    assert 'await page.goto(resolveBundleUrl("https://octowright.com/home"));' in src
    assert 'await page.click("#login");' in src
    assert 'await page.fill("input[name=email]", "a@b.com");' in src
    assert 'await page.type("input[name=q]", "hi", { delay: 25 });' in src
    assert 'await page.keyboard.press("Enter");' in src
    assert 'await page.screenshot({ path: "/tmp/x.png" });' in src
    assert 'await page.evaluate("document.title");' in src
    assert 'await page.waitForSelector(".result");' in src
    assert "await page.waitForFunction(" in src
    assert "await page.waitForLoadState('networkidle');" in src


def test_ts_export_persistent_context_branch(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {
                "action": "launch",
                "kind": "webkit",
                "url": "https://app",
                "headed": False,
                "viewport": {"w": 1024, "h": 768},
                "user_data_dir": "/tmp/profiles/webkit/dante",
            },
            {"action": "click", "selector": "#x"},
        ],
    )
    out = export_script(log, tmp_path / "out.ts", fmt="ts")
    src = out.read_text()
    assert "webkit.launchPersistentContext(" in src
    assert '"/tmp/profiles/webkit/dante"' in src
    assert "headless: true" in src  # headed=False → headless=true
    assert "viewport: { width: 1024, height: 768 }" in src
    assert "ctx.pages()[0] ?? await ctx.newPage()" in src


def test_ts_export_resolves_bundle_scheme_urls(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [{"action": "launch", "kind": "webkit", "url": "bundle://seed/welcome.html?slot=0", "headed": True}],
    )
    out = export_script(log, tmp_path / "artifacts" / "replay.ts", fmt="ts")
    src = out.read_text()

    assert 'import { fileURLToPath, pathToFileURL } from "node:url";' in src
    assert 'import path from "node:path";' in src
    assert "const resolveBundleUrl = (raw: string): string => {" in src
    assert 'await page.goto(resolveBundleUrl("bundle://seed/welcome.html?slot=0"));' in src


def test_ts_export_default_viewport(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [{"action": "launch", "kind": "chromium", "url": "https://x", "headed": True}],
    )
    out = export_script(log, tmp_path / "out.ts", fmt="ts")
    assert "viewport: { width: 1280, height: 800 }" in out.read_text()


def test_ts_export_uses_default_viewport_when_fluid_recorded(tmp_path: Path) -> None:
    log = tmp_path / "fluid-ts.jsonl"
    log.write_text(
        json.dumps(
            {
                "ts": "2026-01-01T00:00:00Z",
                "action": "launch",
                "kind": "chromium",
                "url": "https://octowright.com",
                "viewport": {"mode": "fluid"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.ts"
    export_script(log, out, fmt="ts")
    assert "viewport: { width: 1280, height: 800 }" in out.read_text()


def test_ts_export_skips_unknown_actions(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {"action": "launch", "kind": "webkit", "url": "https://x", "headed": True},
            {"action": "totally_unknown", "x": 1},
            {"action": "click", "selector": "#a"},
        ],
    )
    out = export_script(log, tmp_path / "out.ts", fmt="ts")
    src = out.read_text()
    assert "totally_unknown" not in src
    assert 'await page.click("#a");' in src


def test_ts_export_prefers_semantic_locator_with_selector_fallback(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {"action": "click", "selector": "#login", "role": "button", "role_name": "Log in"},
            {"action": "fill", "selector": "#email", "value": "me@octowright.test", "label": "Email"},
        ],
    )
    out = export_script(log, tmp_path / "out.ts", fmt="ts")
    src = out.read_text()

    assert 'await page.getByRole("button", { name: "Log in" }).click();' in src
    assert 'await page.click("#login");' in src
    assert 'await page.getByLabel("Email").fill("me@octowright.test");' in src
    assert 'await page.fill("#email", "me@octowright.test");' in src


def test_ts_export_keeps_native_semantic_actions(tmp_path: Path) -> None:
    log = _write_recording(
        tmp_path / "r.jsonl",
        [
            {"action": "click_by", "role": "button", "role_name": "Log in"},
            {"action": "fill_by", "value": "me@octowright.test", "label": "Email"},
        ],
    )
    out = export_script(log, tmp_path / "out.ts", fmt="ts")
    src = out.read_text()

    assert 'await page.getByRole("button", { name: "Log in" }).click();' in src
    assert 'await page.getByLabel("Email").fill("me@octowright.test");' in src
    assert "page.click(" not in src
    assert "page.fill(" not in src


# ---------------------------------------------------------------------------
# atomic-write cleanup
# ---------------------------------------------------------------------------


def test_cleans_up_temp_file_on_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If ``os.replace`` fails, the staging ``.tmp`` file must not be leaked.

    Regression for the temp-file-leak path flagged on PR #50: the
    ``NamedTemporaryFile(delete=False)`` makes cleanup the caller's job,
    and a failure between ``tmp.write`` and ``os.replace`` would otherwise
    leave an orphan in the recordings dir.
    """
    log = _write_recording(tmp_path / "r.jsonl", [{"action": "navigate", "url": "https://x"}])
    out_path = tmp_path / "out.py"

    import os as _os

    real_replace = _os.replace

    def _boom(src: str, dst: str) -> None:
        # Verify the stager actually created the temp file before failing,
        # otherwise this test couldn't observe whether the cleanup ran.
        assert Path(src).exists(), "exporter should stage temp file before os.replace"
        raise OSError("simulated replace failure")

    monkeypatch.setattr("octowright._paths.os.replace", _boom)
    with pytest.raises(OSError, match="simulated replace failure"):
        export_script(log, out_path, fmt="python")

    # The exporter must clean up its staging file even though os.replace failed.
    leaked = list(tmp_path.glob(".out.py.*.tmp"))
    assert leaked == [], f"export leaked temp file(s): {leaked}"
    # And the final out_path must NOT exist (the failure aborted before
    # the rename could land).
    assert not out_path.exists()
    # Sanity: real os.replace is unaffected by the monkeypatch outside this call.
    _ = real_replace
