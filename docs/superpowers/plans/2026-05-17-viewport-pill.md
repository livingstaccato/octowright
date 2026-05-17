# Viewport Pill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a click-through viewport-status pill that distinguishes fixed and fluid browser sessions, supports Alt-click actions for sync once and relaunch fluid, and preserves fixed viewport behavior where explicitly requested.

**Architecture:** Extend launch-time viewport metadata, store it on `BrowserSession`, and inject a new init script alongside the existing badge and macro pill. Add browser-domain MCP tools for viewport status, sync, and relaunch; the pill will call a page binding scoped to the owning session so it does not require a broad new HTTP control surface.

**Tech Stack:** Python 3.11, Playwright async API, FastMCP tool decorators, Starlette route patterns where needed, JavaScript context init scripts, pytest/anyio, Ruff/mypy/ty.

---

## File Map

- Create `src/octowright/browser_pool/viewport.py`: viewport metadata dataclass and helper functions.
- Create `src/octowright/browser_pool/_assets/viewport_pill.js`: top-level page overlay script.
- Modify `src/octowright/browser_pool/launch_helpers.py`: return structured viewport metadata from `_build_viewport_kwargs` and record it in launch JSONL.
- Modify `src/octowright/browser_pool/pool.py`: pass viewport metadata into `BrowserSession`, install viewport page binding, and inject viewport pill.
- Modify `src/octowright/browser_pool/visuals.py`: load and wire the new viewport pill init script.
- Modify `src/octowright/session/core.py`: add viewport metadata fields and helper methods.
- Modify `src/octowright/session/core_ops_mixin.py`: add `viewport_status`, `viewport_sync`, and `relaunch_fluid` support methods where session-local behavior belongs.
- Modify `src/octowright/server/browser/lifecycle.py`: add MCP tools `browser_viewport_status`, `browser_viewport_sync`, and `browser_relaunch_fluid`.
- Modify `src/octowright/server/browser/__init__.py`: re-export the new MCP tool functions.
- Modify `src/octowright/server/profiles.py`: include new viewport tools in the `advanced` profile.
- Modify `tests/test_browser_pool_branches.py`, `tests/test_session_ops_mixin_actions.py`, and new focused tests as needed.

## Task 1: Viewport Metadata Model

**Files:**
- Create: `src/octowright/browser_pool/viewport.py`
- Modify: `src/octowright/browser_pool/launch_helpers.py`
- Test: `tests/test_viewport_metadata.py`

- [ ] **Step 1: Write failing metadata tests**

Create `tests/test_viewport_metadata.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from octowright.browser_pool.launch_helpers import _build_viewport_kwargs
from octowright.browser_pool.viewport import ViewportMode


def test_headed_without_explicit_viewport_is_fluid() -> None:
    kwargs, log_viewport, explicit_size, viewport = _build_viewport_kwargs(
        headless=False,
        viewport_w=None,
        viewport_h=None,
    )

    assert kwargs == {"no_viewport": True}
    assert log_viewport is None
    assert explicit_size is False
    assert viewport.mode == ViewportMode.FLUID
    assert viewport.width is None
    assert viewport.height is None


def test_headless_without_explicit_viewport_is_fixed_default() -> None:
    kwargs, log_viewport, explicit_size, viewport = _build_viewport_kwargs(
        headless=True,
        viewport_w=None,
        viewport_h=None,
    )

    assert kwargs == {"viewport": {"width": 1280, "height": 800}}
    assert log_viewport == {"mode": "fixed", "w": 1280, "h": 800}
    assert explicit_size is False
    assert viewport.mode == ViewportMode.FIXED
    assert viewport.width == 1280
    assert viewport.height == 800


def test_explicit_viewport_is_fixed_even_when_headed() -> None:
    kwargs, log_viewport, explicit_size, viewport = _build_viewport_kwargs(
        headless=False,
        viewport_w=1440,
        viewport_h=900,
    )

    assert kwargs == {"viewport": {"width": 1440, "height": 900}}
    assert log_viewport == {"mode": "fixed", "w": 1440, "h": 900}
    assert explicit_size is True
    assert viewport.mode == ViewportMode.FIXED
    assert viewport.width == 1440
    assert viewport.height == 900
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run --active pytest tests/test_viewport_metadata.py -q
```

Expected: import/signature failures because `ViewportMode` does not exist and `_build_viewport_kwargs` still returns three values.

- [ ] **Step 3: Add metadata implementation**

Create `src/octowright/browser_pool/viewport.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ViewportMode(StrEnum):
    FLUID = "fluid"
    FIXED = "fixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ViewportInfo:
    mode: ViewportMode
    width: int | None = None
    height: int | None = None

    def to_recording(self) -> dict[str, Any] | None:
        if self.mode == ViewportMode.FLUID:
            return {"mode": self.mode.value}
        if self.mode == ViewportMode.FIXED and self.width is not None and self.height is not None:
            return {"mode": self.mode.value, "w": self.width, "h": self.height}
        return None

    def to_wire(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "width": self.width,
            "height": self.height,
            "fixed": self.mode == ViewportMode.FIXED,
            "fluid": self.mode == ViewportMode.FLUID,
        }
```

Modify `src/octowright/browser_pool/launch_helpers.py`:

```python
from octowright.browser_pool.viewport import ViewportInfo, ViewportMode
```

Replace `_build_viewport_kwargs` with:

```python
def _build_viewport_kwargs(
    headless: bool, viewport_w: int | None, viewport_h: int | None
) -> tuple[dict[str, Any], dict[str, Any] | None, bool, ViewportInfo]:
    """Build Playwright viewport kwargs plus recording/session metadata."""
    explicit_size = viewport_w is not None or viewport_h is not None
    if headless or explicit_size:
        vw = viewport_w or DEFAULT_VIEWPORT_W
        vh = viewport_h or DEFAULT_VIEWPORT_H
        info = ViewportInfo(mode=ViewportMode.FIXED, width=vw, height=vh)
        return {"viewport": {"width": vw, "height": vh}}, info.to_recording(), explicit_size, info
    info = ViewportInfo(mode=ViewportMode.FLUID)
    return {"no_viewport": True}, info.to_recording(), explicit_size, info
```

- [ ] **Step 4: Update call sites for the new return value**

In `src/octowright/browser_pool/pool.py`, change:

```python
viewport_kwargs, log_viewport, explicit_size = _build_viewport_kwargs(headless, viewport_w, viewport_h)
```

to:

```python
viewport_kwargs, log_viewport, explicit_size, viewport_info = _build_viewport_kwargs(
    headless,
    viewport_w,
    viewport_h,
)
```

Keep `viewport_info` unused until Task 2 if necessary.

- [ ] **Step 5: Run metadata tests**

Run:

```bash
uv run --active pytest tests/test_viewport_metadata.py -q
```

Expected: 3 passed.

## Task 2: Store Viewport Metadata On Sessions

**Files:**
- Modify: `src/octowright/session/core.py`
- Modify: `src/octowright/browser_pool/pool.py`
- Test: `tests/test_browser_pool_branches.py`

- [ ] **Step 1: Write failing session metadata assertion**

Add to an existing launch test in `tests/test_browser_pool_branches.py`, or create a small new unit using mocks, asserting:

```python
assert new_session.viewport_mode == "fluid"
assert new_session.viewport_width is None
assert new_session.viewport_height is None
```

For an explicit viewport launch, assert:

```python
assert new_session.viewport_mode == "fixed"
assert new_session.viewport_width == 800
assert new_session.viewport_height == 600
```

- [ ] **Step 2: Add fields to `BrowserSession`**

In `src/octowright/session/core.py`, import the enum:

```python
from octowright.browser_pool.viewport import ViewportMode
```

Add fields to `BrowserSession`:

```python
    viewport_mode: str = ViewportMode.UNKNOWN.value
    viewport_width: int | None = None
    viewport_height: int | None = None
```

- [ ] **Step 3: Pass metadata from pool launch**

In `src/octowright/browser_pool/pool.py`, add these keyword arguments to `BrowserSession(...)`:

```python
                viewport_mode=viewport_info.mode.value,
                viewport_width=viewport_info.width,
                viewport_height=viewport_info.height,
```

- [ ] **Step 4: Run focused launch/session tests**

Run:

```bash
uv run --active pytest tests/test_viewport_metadata.py tests/test_browser_pool_branches.py -q
```

Expected: passed or only unrelated existing global coverage failure if a single focused command trips coverage; full suite later is authoritative.

## Task 3: Viewport Status And Sync MCP Tools

**Files:**
- Modify: `src/octowright/session/core_ops_mixin.py`
- Modify: `src/octowright/server/browser/lifecycle.py`
- Modify: `src/octowright/server/browser/__init__.py`
- Modify: `src/octowright/server/profiles.py`
- Test: `tests/test_session_ops_mixin_actions.py`
- Test: `tests/test_consolidated_tools.py` or new `tests/test_browser_viewport_tools.py`

- [ ] **Step 1: Write failing status/sync tests**

Add tests that build a mock session with:

```python
page.evaluate = AsyncMock(return_value={"innerWidth": 1280, "innerHeight": 800, "outerWidth": 1512, "outerHeight": 930})
page.set_viewport_size = AsyncMock()
session.viewport_mode = "fixed"
session.viewport_width = 1280
session.viewport_height = 800
```

Assert:

```python
status = await session.viewport_status()
assert status["mode"] == "fixed"
assert status["page"] == {"width": 1280, "height": 800}
assert status["outer"] == {"width": 1512, "height": 930}
assert status["mismatch"] is True
```

Assert sync:

```python
result = await session.viewport_sync()
page.set_viewport_size.assert_awaited_once_with({"width": 1512, "height": 930})
assert result["ok"] is True
assert session.viewport_width == 1512
assert session.viewport_height == 930
```

- [ ] **Step 2: Implement session methods**

In `src/octowright/session/core_ops_mixin.py`, add:

```python
    async def viewport_status(self) -> dict[str, Any]:
        measured = await self.page.evaluate(
            """() => ({
                innerWidth: window.innerWidth,
                innerHeight: window.innerHeight,
                outerWidth: window.outerWidth,
                outerHeight: window.outerHeight,
                devicePixelRatio: window.devicePixelRatio
            })"""
        )
        page = {"width": int(measured.get("innerWidth") or 0), "height": int(measured.get("innerHeight") or 0)}
        outer = {"width": int(measured.get("outerWidth") or 0), "height": int(measured.get("outerHeight") or 0)}
        mismatch = (
            self.viewport_mode == "fixed"
            and outer["width"] > 0
            and outer["height"] > 0
            and (abs(outer["width"] - page["width"]) > 24 or abs(outer["height"] - page["height"]) > 80)
        )
        return {
            "mode": self.viewport_mode,
            "fixed": self.viewport_mode == "fixed",
            "fluid": self.viewport_mode == "fluid",
            "configured": {"width": self.viewport_width, "height": self.viewport_height},
            "page": page,
            "outer": outer,
            "device_pixel_ratio": measured.get("devicePixelRatio"),
            "mismatch": mismatch,
        }

    async def viewport_sync(self) -> dict[str, Any]:
        status = await self.viewport_status()
        outer = status["outer"]
        width = int(outer["width"] or status["page"]["width"])
        height = int(outer["height"] or status["page"]["height"])
        if width <= 0 or height <= 0:
            raise ValueError("unable to measure a usable viewport size")
        await self.page.set_viewport_size({"width": width, "height": height})
        self.viewport_mode = "fixed"
        self.viewport_width = width
        self.viewport_height = height
        self.recorder.record("resize", width=width, height=height)
        return {"ok": True, "mode": "fixed", "width": width, "height": height}
```

Use the existing `Any` import in that file.

- [ ] **Step 3: Add MCP tools**

In `src/octowright/server/browser/lifecycle.py`, add:

```python
@mcp.tool(structured_output=False, description="Return fixed/fluid viewport status and measured page/window dimensions.")
async def browser_viewport_status(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).viewport_status()


@mcp.tool(structured_output=False, description="Resize a fixed Playwright viewport once to the current measured browser window size.")
async def browser_viewport_sync(instance_id: str) -> dict[str, Any]:
    return await pool.get(instance_id).viewport_sync()
```

Update imports/re-exports in `src/octowright/server/browser/__init__.py` and add both tool names to the `advanced` profile in `src/octowright/server/profiles.py`.

- [ ] **Step 4: Run focused status/sync tests**

Run:

```bash
uv run --active pytest tests/test_session_ops_mixin_actions.py tests/test_consolidated_tools.py -q
```

Expected: passed or global coverage gating failure if not running the full suite.

## Task 4: Relaunch Fluid Tool

**Files:**
- Modify: `src/octowright/browser_pool/lifecycle.py`
- Modify: `src/octowright/browser_pool/pool.py`
- Modify: `src/octowright/server/browser/lifecycle.py`
- Test: `tests/test_consolidated_tools.py`

- [ ] **Step 1: Write failing relaunch-fluid test**

Mock `pool.get`, `pool.close`, and `pool.launch`. Assert the tool launches with no viewport dimensions:

```python
result = await _lifecycle.browser_relaunch_fluid("old-id")

pool.close.assert_awaited_once_with("old-id")
pool.launch.assert_awaited_once()
_, kwargs = pool.launch.call_args
assert kwargs["url"] == "https://example.com"
assert kwargs["kind"] == "chromium"
assert kwargs["label"] == "player"
assert kwargs["profile"] == "profile-a"
assert "viewport_w" not in kwargs or kwargs["viewport_w"] is None
assert "viewport_h" not in kwargs or kwargs["viewport_h"] is None
assert result["old_instance_id"] == "old-id"
assert result["new_instance_id"] == "new-id"
```

- [ ] **Step 2: Add pool helper**

In `src/octowright/browser_pool/pool.py`, add:

```python
    async def relaunch_fluid(self, instance_id: str) -> dict[str, Any]:
        source = self.get(instance_id)
        target_url = getattr(source.page, "url", None) or source.url
        launch_kwargs = {
            "kind": source.kind,
            "url": target_url,
            "headed": True,
            "label": source.label,
            "profile": source.profile,
            "stabilize": source.stabilize,
            "trace": source.trace,
            "har": bool(source.har_path),
            "har_path": str(source.har_path) if source.har_path else None,
            "badge": True,
        }
        close_result = await self.close(instance_id)
        result = await self.launch(**launch_kwargs)
        return {
            "ok": True,
            "old_instance_id": instance_id,
            "new_instance_id": result["instance_id"],
            "old_closed": bool(close_result.get("closed")),
            "mode": "fluid",
            "launch": result,
        }
```

If preserving session-scoped tmpdir mode is needed, include:

```python
"session": source.profile is None and source.user_data_dir is not None,
```

and test that branch.

- [ ] **Step 3: Add MCP tool**

In `src/octowright/server/browser/lifecycle.py`, add:

```python
@mcp.tool(structured_output=False, description="Close and relaunch a session as a headed fluid viewport using no_viewport=True.")
async def browser_relaunch_fluid(instance_id: str) -> dict[str, Any]:
    result = await pool.relaunch_fluid(instance_id)
    publish_dashboard_invalidation_nowait("sessions")
    return result
```

Re-export and add to advanced profile.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run --active pytest tests/test_consolidated_tools.py -q
```

Expected: focused tests pass if coverage is disabled for this command; otherwise full suite later confirms.

## Task 5: Viewport Pill Overlay Script

**Files:**
- Create: `src/octowright/browser_pool/_assets/viewport_pill.js`
- Modify: `src/octowright/browser_pool/visuals.py`
- Test: `tests/test_viewport_pill.py`

- [ ] **Step 1: Write failing injection test**

Create `tests/test_viewport_pill.py` with a fake context:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from unittest.mock import AsyncMock

import pytest

from octowright.browser_pool.visuals import wire_init_scripts


@pytest.mark.anyio
async def test_viewport_pill_script_is_injected() -> None:
    context = type("Context", (), {"add_init_script": AsyncMock()})()

    await wire_init_scripts(
        context,
        profile=None,
        label="player",
        instance_id="abc123",
        kind="chromium",
        badge=False,
        badge_position="bottom-right",
        stabilize=False,
        viewport_mode="fixed",
        viewport_width=1280,
        viewport_height=800,
    )

    scripts = [call.kwargs["script"] for call in context.add_init_script.await_args_list]
    assert any("__octowright_viewport_status__" in script for script in scripts)
    assert any('"fixed"' in script and "1280" in script and "800" in script for script in scripts)
```

- [ ] **Step 2: Add script file**

Create `src/octowright/browser_pool/_assets/viewport_pill.js` with:

```javascript
(() => {
    if (window.top !== window.self) return;
    const ROOT_ID = "__octowright_viewport_status__";
    const INITIAL = __VIEWPORT_INFO__;
    const CLICK_MODIFIER = "altKey";
    let modifierActive = false;

    const measure = () => ({
        innerWidth: window.innerWidth,
        innerHeight: window.innerHeight,
        outerWidth: window.outerWidth,
        outerHeight: window.outerHeight,
    });

    const stateLabel = () => {
        if (INITIAL.mode === "fluid") return "viewport · fluid";
        if (INITIAL.width && INITIAL.height) return `viewport · fixed ${INITIAL.width}x${INITIAL.height}`;
        return "viewport · fixed";
    };

    const colorFor = () => {
        if (INITIAL.mode === "fluid") return "rgba(22, 163, 74, 0.78)";
        return "rgba(75, 85, 99, 0.78)";
    };

    const applyInteractive = () => {
        const root = document.getElementById(ROOT_ID);
        if (!root) return;
        root.style.pointerEvents = modifierActive ? "auto" : "none";
        root.style.cursor = modifierActive ? "pointer" : "";
        root.style.outline = modifierActive ? "1px solid rgba(255,255,255,.45)" : "";
    };

    const closeModal = () => {
        const existing = document.getElementById(ROOT_ID + "_modal");
        if (existing) existing.remove();
    };

    const action = async (name) => {
        if (!window.__octowright_viewport_action) return;
        try {
            await window.__octowright_viewport_action({ action: name, measured: measure() });
        } catch (_) {}
    };

    const openModal = () => {
        closeModal();
        const measured = measure();
        const modal = document.createElement("div");
        modal.id = ROOT_ID + "_modal";
        Object.assign(modal.style, {
            position: "fixed",
            top: "44px",
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: "2147483647",
            background: "rgba(20,20,24,.96)",
            color: "white",
            border: "1px solid rgba(255,255,255,.12)",
            borderRadius: "8px",
            padding: "10px",
            font: "12px system-ui, sans-serif",
            boxShadow: "0 12px 32px rgba(0,0,0,.42)",
            minWidth: "300px",
        });
        modal.innerHTML = `
            <strong>Viewport ${INITIAL.mode}</strong><br>
            Page: ${measured.innerWidth}x${measured.innerHeight}<br>
            Window: ${measured.outerWidth}x${measured.outerHeight}
        `;
        const row = document.createElement("div");
        Object.assign(row.style, { display: "flex", gap: "8px", marginTop: "10px", flexWrap: "wrap" });
        for (const [label, name] of [["Sync once", "sync"], ["Relaunch fluid", "relaunch-fluid"], ["Close", "close"]]) {
            const btn = document.createElement("button");
            btn.textContent = label;
            Object.assign(btn.style, { border: "0", borderRadius: "5px", padding: "5px 9px", cursor: "pointer" });
            btn.addEventListener("click", (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (name === "close") closeModal();
                else action(name);
            });
            row.append(btn);
        }
        modal.append(row);
        document.body.append(modal);
    };

    const build = () => {
        if (!document.body) return null;
        let root = document.getElementById(ROOT_ID);
        if (root) return root;
        root = document.createElement("div");
        root.id = ROOT_ID;
        root.textContent = stateLabel();
        Object.assign(root.style, {
            position: "fixed",
            left: "50%",
            top: "12px",
            transform: "translateX(-50%)",
            zIndex: "2147483646",
            background: colorFor(),
            color: "white",
            borderRadius: "12px",
            padding: "4px 8px",
            font: "12px ui-monospace, Menlo, Consolas, monospace",
            boxShadow: "0 1px 8px rgba(0,0,0,.35)",
            pointerEvents: "none",
            userSelect: "none",
        });
        root.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            openModal();
        });
        document.body.append(root);
        applyInteractive();
        return root;
    };

    build();
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", build, { once: true });
    }
    new MutationObserver(build).observe(document.documentElement || document, { childList: true, subtree: true });
    window.addEventListener("keydown", (event) => { modifierActive = !!event[CLICK_MODIFIER]; applyInteractive(); }, true);
    window.addEventListener("keyup", (event) => { modifierActive = !!event[CLICK_MODIFIER]; applyInteractive(); }, true);
    window.addEventListener("blur", () => { modifierActive = false; applyInteractive(); });
})();
```

- [ ] **Step 3: Wire script injection**

In `src/octowright/browser_pool/visuals.py`, load the asset:

```python
_VIEWPORT_PILL_SCRIPT = (_ASSETS / "viewport_pill.js").read_text(encoding="utf-8")
```

Extend `wire_init_scripts(...)` signature:

```python
    viewport_mode: str = "unknown",
    viewport_width: int | None = None,
    viewport_height: int | None = None,
```

Before stabilize injection, add:

```python
    viewport_payload = {
        "mode": viewport_mode,
        "width": viewport_width,
        "height": viewport_height,
    }
    viewport_script = _VIEWPORT_PILL_SCRIPT.replace("__VIEWPORT_INFO__", _json.dumps(viewport_payload))
    await context.add_init_script(script=viewport_script)
```

Update every `wire_init_scripts` call to pass the session viewport metadata.

- [ ] **Step 4: Run pill tests**

Run:

```bash
uv run --active pytest tests/test_viewport_pill.py -q
```

Expected: passes or full-suite coverage gate later confirms.

## Task 6: Page Binding For Pill Actions

**Files:**
- Modify: `src/octowright/browser_pool/pool.py`
- Modify: `src/octowright/session/core.py`
- Test: `tests/test_viewport_pill.py`

- [ ] **Step 1: Write failing binding test**

Mock a context with `expose_binding = AsyncMock()`. Assert pool launch wiring calls:

```python
context.expose_binding.assert_awaited()
```

and the binding name is:

```python
"__octowright_viewport_action"
```

- [ ] **Step 2: Implement binding helper**

In `src/octowright/browser_pool/pool.py`, after session creation and before navigation, add:

```python
            async def _viewport_action(_source: Any, payload: dict[str, Any]) -> dict[str, Any]:
                action = payload.get("action")
                if action == "sync":
                    return await new_session.viewport_sync()
                if action == "relaunch-fluid":
                    return await self.relaunch_fluid(new_session.instance_id)
                raise ValueError(f"unknown viewport action: {action!r}")

            await context.expose_binding("__octowright_viewport_action", _viewport_action)
```

If Playwright requires binding before page creation for init scripts to see it, move the binding into `_open_browser_context` or expose it immediately after context creation and before navigation.

- [ ] **Step 3: Run binding tests**

Run:

```bash
uv run --active pytest tests/test_viewport_pill.py -q
```

Expected: binding test passes.

## Task 7: Full Verification

**Files:**
- All touched files.

- [ ] **Step 1: Run full Python tests**

Run:

```bash
uv run --active pytest -q tests/
```

Expected: all tests pass and coverage remains above the configured threshold.

- [ ] **Step 2: Run lint gate**

Run:

```bash
make lint
```

Expected: all lint, type, security, docs, complexity, and secrets checks pass.

- [ ] **Step 3: Run live MCP smoke**

Restart or reconnect Octowright if necessary. Then use MCP tools:

1. `octowright_status`
2. `browser_quick_launch` with a headed Chromium data URL and no explicit viewport
3. `browser_viewport_status`
4. `browser_relaunch_fluid` if the session is fixed, or a fixed explicit launch followed by `browser_viewport_sync`
5. `browser_close`

Expected:

- no stale manifest sessions
- no live browsers after cleanup
- viewport status reports `fluid` for headed no-explicit-viewport launch
- explicit viewport launch reports `fixed`
- sync action changes fixed dimensions
- relaunch fluid returns a new instance id and closes the old one

- [ ] **Step 4: Commit**

Run:

```bash
git add src/octowright tests docs/superpowers/plans/2026-05-17-viewport-pill.md
git commit -m "feat: add viewport status pill"
```

Expected: commitlint and pre-commit hooks pass.

## Self-Review

- Spec coverage: the plan covers the dedicated pill, Alt-click modal, sync once, relaunch fluid, fixed/fluid launch metadata, MCP actions, and tests.
- Placeholder scan: no `TBD`, `TODO`, or unbounded "add tests" instructions remain.
- Scope: auto-sync is intentionally excluded, matching the design spec.
- Type consistency: `ViewportInfo`, `ViewportMode`, `viewport_mode`, `viewport_width`, and `viewport_height` are used consistently across tasks.
