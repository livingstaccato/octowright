# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Live smoke test: frame-aware snapshot + macro_repair_apply, end-to-end.

Drives a real browser through the in-process source (the same MCP tool functions
the daemon serves) and asserts two regressions stay fixed:

  1. After ``browser_switch_frame``, ``browser_snapshot`` descends INTO the
     iframe — returning the frame's aria + url — instead of the top-level page.
     (Same for ``browser_brief`` / ``capture_create`` / ``golden_save`` url.)
  2. ``macro_repair_apply`` rewrites a brittle selector-based ``click`` into its
     semantic ``click_by``, drops the stale CSS selector, persists it, and the
     repaired macro actually clicks the live page.

All on-disk state is isolated under a temp dir, so this never touches your real
macros/recordings/goldens.

Run:   uv run --active python scripts/frame_and_repair_smoke.py
Watch: OCTOWRIGHT_HEADLESS=0 uv run --active python scripts/frame_and_repair_smoke.py
Exit:  0 if every check passes, 1 otherwise (CI-friendly).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path

# Isolate all on-disk state and default to headless BEFORE importing octowright
# (defaults.py reads these env vars at import time).
_TMP = tempfile.mkdtemp(prefix="octo-frame-repair-smoke-")
os.environ.setdefault("OCTOWRIGHT_HEADLESS", "1")
os.environ["OCTOWRIGHT_MACROS_DIR"] = str(Path(_TMP) / "macros")
os.environ["OCTOWRIGHT_RECORDINGS_DIR"] = str(Path(_TMP) / "rec")
os.environ["OCTOWRIGHT_GOLDENS_DIR"] = str(Path(_TMP) / "goldens")


def _data_url(html: str) -> str:
    return "data:text/html;base64," + base64.b64encode(html.encode()).decode()


# Parent page (PARENT_MARKER + a "Run Action" button) embedding an iframe whose
# inner document carries FRAME_MARKER + a login form.
_INNER = _data_url(
    "<!doctype html><meta charset=utf-8>"
    "<h2>INNER FRAME login form (FRAME_MARKER)</h2>"
    "<label>Email <input type=email></label><button>Sign In</button>"
)
_TOP = _data_url(
    "<!doctype html><meta charset=utf-8><title>frame+repair smoke</title>"
    "<h1>PARENT PAGE top frame (PARENT_MARKER)</h1>"
    "<button id='run-btn' onclick=\"document.getElementById('status').textContent='ACTION RAN'\">Run Action</button>"
    "<div id='status'>idle</div>"
    f"<iframe name='loginframe' src='{_INNER}'></iframe>"
)

_results: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, bool(ok), detail))


async def _run() -> None:
    from octowright.macros import load_macro
    from octowright.server._state import pool
    from octowright.server.browser.inspect import browser_snapshot
    from octowright.server.browser.lifecycle import browser_launch
    from octowright.server.browser.views import browser_list_frames, browser_reset_frame, browser_switch_frame
    from octowright.server.goldens import golden_save
    from octowright.server.macros import macro_compile, macro_repair_apply, macro_run

    launched = await browser_launch(kind="chromium", url=_TOP, ephemeral=True)
    iid = launched["instance_id"]
    try:
        # ── 1. frame-aware snapshot ───────────────────────────────────────
        top = await browser_snapshot(iid)
        _check(
            "top snapshot shows the parent page",
            "PARENT_MARKER" in top["aria"] and "FRAME_MARKER" not in top["aria"],
            top["aria"][:80],
        )

        frames = browser_list_frames(iid)
        _check("iframe is listed", any(f.get("name") == "loginframe" for f in frames), str(frames))

        await browser_switch_frame(iid, name="loginframe")
        frame = await browser_snapshot(iid)
        _check(
            "snapshot descends into the switched frame (not the parent)",
            "FRAME_MARKER" in frame["aria"] and "PARENT_MARKER" not in frame["aria"],
            frame["aria"][:80],
        )
        _check("snapshot url is the frame's url", frame["url"] == _INNER, frame["url"][:48])

        gold = await golden_save(iid, name="smoke-frame-golden")
        gdata = json.loads(Path(gold["path"]).read_text(encoding="utf-8"))
        _check("golden_save records the frame url", gdata["url"] == _INNER, str(gdata.get("url"))[:48])

        await browser_reset_frame(iid)

        # ── 2. macro_repair_apply, end-to-end ─────────────────────────────
        # role-based locator (not text) so the click targets the button, not
        # the injected macro status pill (whose text echoes the action).
        macro_compile(
            yaml_text=(
                "name: smoke-repair\n"
                "actions:\n"
                "  - action: click\n"
                '    selector: "#stale-id-that-no-longer-exists"\n'
                "    role: button\n"
                "    role_name: Run Action\n"
            ),
            name="smoke-repair",
            write=True,
        )
        applied = macro_repair_apply("smoke-repair", 0)
        repl = applied["replacement_action"]
        _check(
            "repair rewrites click -> click_by and drops the stale selector",
            repl["action"] == "click_by" and "selector" not in repl,
            str(repl),
        )

        on_disk = load_macro("smoke-repair")["actions"][0]
        _check(
            "repair persisted to disk",
            on_disk == {"action": "click_by", "role": "button", "role_name": "Run Action"},
            str(on_disk),
        )

        await macro_run(iid, "smoke-repair")
        status = await pool.get(iid).evaluate("document.getElementById('status').textContent")
        _check(
            "repaired macro clicks the live page (status='ACTION RAN')", status == "ACTION RAN", f"status={status!r}"
        )
    finally:
        await pool.close(iid, force=True)


def main() -> int:
    asyncio.run(_run())
    print("\n=== frame + repair smoke ===")
    ok_all = True
    for name, ok, detail in _results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if not ok else ""))
        ok_all = ok_all and ok
    print("=== ALL PASS ===" if ok_all else "=== FAILURES ABOVE ===")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
