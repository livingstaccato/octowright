# Schema Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement context optimization techniques to drastically reduce LLM schema overhead and turn-by-turn chatter by introducing a `browser_brief` primitive, response modes for mutative actions, and tool capability profiles.

**Architecture:** 
1. **browser_brief**: A new fast, lightweight tool returning only URL, title, and key actionable elements.
2. **Response Modes**: Add a `response_mode` parameter to mutative tools like `browser_click` and `browser_navigate` that can optionally return the `browser_brief` to save a round trip.
3. **Capability Profiles**: We will categorize the massive 60+ tool surface into profiles (e.g., `core`, `debug`, `macros`) to prevent schema bloat on initial connection.

**Tech Stack:** Python, FastMCP, Pytest, Playwright

---

### Task 1: Implement `browser_brief` Primitive

**Files:**
- Modify: `src/octowright/server/browser/inspect.py`
- Modify: `src/octowright/server/browser/__init__.py`
- Modify: `tests/test_server_browser_inspect_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/test_server_browser_inspect_tools.py
@pytest.mark.anyio
async def test_browser_brief(_patch_pool: MagicMock) -> None:
    s = _session()
    _patch_pool.get.return_value = s
    out = await _inspect.browser_brief("i")
    
    assert out["url"] == "https://octowright.com"
    assert out["title"] == "Example"
    assert "elements" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server_browser_inspect_tools.py::test_browser_brief -v`
Expected: FAIL with "AttributeError: module 'octowright.server.browser.inspect' has no attribute 'browser_brief'"

- [ ] **Step 3: Write minimal implementation**

Add to `src/octowright/server/browser/inspect.py`:

```python
@mcp.tool(
    structured_output=False,
    description=(
        "Return a brief summary of the current page state, including URL, title, "
        "and highly truncated snapshot of actionable elements."
    ),
)
async def browser_brief(instance_id: str) -> dict[str, Any]:
    session = pool.get(instance_id)
    title = await session.page.title()
    # Pull a tiny slice of the body snapshot to provide basic orientation
    aria = await session.page.locator("body").aria_snapshot()
    elements = aria[:500] + ("..." if len(aria) > 500 else "")
    
    return {
        "url": session.page.url,
        "title": title,
        "elements": elements,
    }
```

Add to exports in `src/octowright/server/browser/__init__.py`:
```python
from octowright.server.browser.inspect import (
    # ... existing imports
    browser_brief,
)

__all__ = [
    # ... existing
    "browser_brief",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server_browser_inspect_tools.py::test_browser_brief -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/octowright/server/browser/inspect.py src/octowright/server/browser/__init__.py tests/test_server_browser_inspect_tools.py
git commit -m "feat: add browser_brief primitive"
```

### Task 2: Add Response Modes to Mutative Actions

**Files:**
- Modify: `src/octowright/server/browser/input.py`
- Modify: `tests/test_server_browser_input_tools.py` (Create if missing or use existing for input)

- [ ] **Step 1: Write the failing test**

```python
# Assume test file is tests/test_server_browser_input_tools.py
# If tests for input.py exist in another file, adapt. 
# Let's check `tests/test_consolidated_tools.py` or similar. We will just test browser_click.
import pytest
from unittest.mock import MagicMock, AsyncMock
from octowright.server.browser import input as _input
from octowright.server.browser import inspect as _inspect

@pytest.fixture(autouse=True)
def _patch_pool_input(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake_pool = MagicMock()
    monkeypatch.setattr(_input, "pool", fake_pool)
    return fake_pool

@pytest.mark.anyio
async def test_browser_click_brief_mode(_patch_pool_input: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    s = MagicMock()
    _patch_pool_input.get.return_value = s
    s.click = AsyncMock(return_value={"ok": True})
    
    # Mock browser_brief
    monkeypatch.setattr(_input, "browser_brief", AsyncMock(return_value={"url": "test", "elements": "none"}))
    
    out = await _input.browser_click("i", "button", response_mode="brief")
    assert out["brief"]["url"] == "test"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server_browser_input_tools.py -v`
Expected: FAIL because `response_mode` is not an argument to `browser_click`.

- [ ] **Step 3: Write minimal implementation**

Modify `src/octowright/server/browser/input.py`:
Change `browser_click`:

```python
from octowright.server.browser.inspect import browser_brief

@mcp.tool(
    structured_output=False,
    description="Click an element by CSS selector.",
)
async def browser_click(
    instance_id: str, 
    selector: str, 
    response_mode: str | None = None
) -> dict[str, Any]:
    session = pool.get(instance_id)
    res = await session.click(selector)
    if response_mode == "brief":
        res["brief"] = await browser_brief(instance_id)
    return res
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_server_browser_input_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/octowright/server/browser/input.py tests/test_server_browser_input_tools.py
git commit -m "feat: add brief response mode to browser_click"
```

### Task 3: Capability Profiles

**Files:**
- Modify: `src/octowright/server/_state.py`
- Modify: `src/octowright/server/browser/__init__.py`

(Note: Implementing true FastMCP capability profiles might require upstream changes or advanced FastMCP context features. We can simulate it by having a startup configuration in `_state.py` or modifying the tool registration logic. For this plan, we will introduce a categorization mapping in `browser/__init__.py` that can be utilized to register only specific tool groups when the server starts.)

- [ ] **Step 1: Write the minimal implementation mapping**

Modify `src/octowright/server/browser/__init__.py` to categorize `__all__` into groups:

```python
PROFILES = {
    "core": [
        "browser_click", "browser_type", "browser_fill", "browser_launch", 
        "browser_close", "browser_navigate", "browser_brief", "browser_wait_for",
        "browser_read_markdown"
    ],
    "advanced": [
        "browser_snapshot", "browser_evaluate", "browser_console_messages",
        "browser_expect_text", "browser_expect_url"
    ]
}
# Keeping __all__ as the flat list for backward compatibility
```

- [ ] **Step 2: Commit**

```bash
git add src/octowright/server/browser/__init__.py
git commit -m "feat: define capability profiles for browser tools"
```
