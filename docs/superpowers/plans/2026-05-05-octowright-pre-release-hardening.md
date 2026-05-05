# Octowright Pre-Release Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Octowright's pre-release split browser-pool architecture with one canonical pool, explicit live-state APIs, fail-safe session lifecycle, remote-dashboard protections, bounded memory, and passing tests.

**Architecture:** `src/octowright/pool.py` is the only `BrowserPool` implementation. Focused helper modules remain under `src/octowright/browser_pool/`, but `runtime.py` is removed and no test imports it. HTTP routes use public pool/scenario APIs and a central dashboard exposure guard.

**Tech Stack:** Python 3.12+, Playwright async API, Starlette, FastMCP, pytest/pytest-asyncio, uv, TypeScript/Vitest dashboard.

---

## File Structure

- Modify `src/octowright/pool.py`: canonical pool implementation, public state API, launch cleanup, engine-hint wrapping, `user_data_dir` persistence, handoff parity.
- Modify `src/octowright/browser_pool/__init__.py`: export only helper modules still intended as public package helpers; remove runtime export.
- Delete `src/octowright/browser_pool/runtime.py`: remove duplicate pool implementation.
- Keep `src/octowright/browser_pool/visuals.py`: single source for badge/title/tile helpers.
- Keep `src/octowright/browser_pool/errors.py`: source for Playwright sanity wrapping, imported by `pool.py`.
- Modify `src/octowright/session/core.py`: bounded network event fields, dropped counter, background-task close helper declarations.
- Modify `src/octowright/session/core_ops_mixin.py`: bounded network event capture, close drains/cancels background tasks before recorder close.
- Modify `src/octowright/defaults.py`: add `DASHBOARD_REMOTE_ALLOWED` and `NETWORK_EVENT_LIMIT`.
- Create `src/octowright/http/exposure.py`: loopback detection and request guard helpers.
- Modify `src/octowright/http/app.py`: set default exposure state on the Starlette app.
- Modify `src/octowright/http/routes/*.py`: use public pool/scenario APIs and guard sensitive routes.
- Modify `src/octowright/http/discovery.py`: use public pool accessors and avoid private `_sessions`.
- Modify `src/octowright/scenarios_pool.py`: public live-scenario accessors.
- Modify `src/octowright/server/meta.py`, `src/octowright/server/macros.py`, `src/octowright/server/personas.py`, and `src/octowright/server/scenarios.py`: use public pool/scenario APIs where they currently read private state.
- Modify `pyproject.toml`: add runtime `httpx`.
- Modify tests under `tests/`: update imports, add lifecycle/security/bounds tests, and remove duplicate runtime assumptions.

---

### Task 1: Restore Collection With Single Visual Helper Source

**Files:**
- Modify: `tests/test_badge.py`
- Modify: `tests/test_tile.py`
- Modify: `src/octowright/browser_pool/__init__.py`
- Delete later in Task 2: `src/octowright/browser_pool/runtime.py`

- [ ] **Step 1: Write the import-end-state test**

Add this to `tests/test_engines.py` or a new `tests/test_pool_architecture.py`:

```python
from __future__ import annotations

from pathlib import Path


def test_duplicate_browser_pool_runtime_is_removed() -> None:
    assert not Path("src/octowright/browser_pool/runtime.py").exists()
```

- [ ] **Step 2: Run tests to verify current failure**

Run:

```bash
uv run pytest -q tests/test_badge.py tests/test_tile.py tests/test_pool_architecture.py
```

Expected: collection fails for badge/tile imports from `octowright.pool`, and the architecture test fails while `runtime.py` exists.

- [ ] **Step 3: Update visual helper imports**

In `tests/test_badge.py`, replace:

```python
from octowright.pool import (
    _ENGINE_EMOJI,
    _PERSONA_EMOJI_POOL,
    _badge_color_for,
    _badge_text_for,
    _emoji_pair_for,
    _persona_emoji_for,
)
```

with:

```python
from octowright.browser_pool.visuals import (
    _ENGINE_EMOJI,
    _PERSONA_EMOJI_POOL,
    _badge_color_for,
    _badge_text_for,
    _emoji_pair_for,
    _persona_emoji_for,
)
```

Also replace any local imports in `tests/test_badge.py`:

```python
from octowright.pool import _BADGE_POSITION_DEFAULT, _BADGE_POSITIONS
```

with:

```python
from octowright.browser_pool.visuals import _BADGE_POSITION_DEFAULT, _BADGE_POSITIONS
```

In `tests/test_tile.py`, replace:

```python
from octowright.pool import _tile_args_for_chromium, _tile_position
```

with:

```python
from octowright.browser_pool.visuals import _tile_args_for_chromium, _tile_position
```

- [ ] **Step 4: Update package exports**

Replace `src/octowright/browser_pool/__init__.py` with:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from .errors import maybe_wrap_playwright_error
from .listeners import _wire_close_evictor, _wire_listeners, _wire_user_navigation_logger
from .visuals import (
    _BADGE_POSITION_DEFAULT,
    _BADGE_POSITIONS,
    _ENGINE_EMOJI,
    _PERSONA_EMOJI_POOL,
    _badge_color_for,
    _badge_text_for,
    _emoji_pair_for,
    _persona_emoji_for,
    _tile_args_for_chromium,
    _tile_position,
    _title_tag_for,
)

__all__ = [
    "_BADGE_POSITIONS",
    "_BADGE_POSITION_DEFAULT",
    "_ENGINE_EMOJI",
    "_PERSONA_EMOJI_POOL",
    "maybe_wrap_playwright_error",
    "_badge_color_for",
    "_badge_text_for",
    "_emoji_pair_for",
    "_persona_emoji_for",
    "_tile_args_for_chromium",
    "_tile_position",
    "_title_tag_for",
    "_wire_close_evictor",
    "_wire_listeners",
    "_wire_user_navigation_logger",
]
```

- [ ] **Step 5: Run targeted import tests**

Run:

```bash
uv run pytest -q tests/test_badge.py tests/test_tile.py
```

Expected: badge and tile tests collect and pass, except tests that still depend on `runtime.py` are outside this command.

- [ ] **Step 6: Commit**

```bash
git add tests/test_badge.py tests/test_tile.py tests/test_pool_architecture.py src/octowright/browser_pool/__init__.py
git commit -m "test: point visual helper tests at canonical helper module"
```

---

### Task 2: Remove Duplicate BrowserPool Runtime

**Files:**
- Modify: `tests/test_engines.py`
- Delete: `src/octowright/browser_pool/runtime.py`
- Modify: `src/octowright/pool.py`

- [ ] **Step 1: Update tests importing duplicate runtime**

In `tests/test_engines.py`, replace:

```python
from octowright.browser_pool.runtime import BrowserPool
```

with:

```python
from octowright.pool import BrowserPool
```

- [ ] **Step 2: Move missing canonical behavior into `pool.py`**

In `src/octowright/pool.py`, add:

```python
from .browser_pool.errors import maybe_wrap_playwright_error
```

In the session construction block, include `user_data_dir`:

```python
new_session = BrowserSession(
    instance_id=instance_id,
    kind=kind,
    label=label,
    url=target_url,
    browser=browser,
    context=context,
    page=page,
    recorder=recorder,
    log_path=log_path,
    user_data_dir=Path(user_data_dir) if user_data_dir is not None else None,
    profile=profile,
    stabilize=stabilize,
    trace=trace,
    har_path=har_path,
)
```

Wrap Playwright launch/context creation and initial navigation errors with:

```python
except Exception as exc:
    raise maybe_wrap_playwright_error(exc, kind=kind) from exc
```

Full cleanup behavior is completed in Task 4.

- [ ] **Step 3: Delete duplicate implementation**

Delete:

```bash
rm src/octowright/browser_pool/runtime.py
```

- [ ] **Step 4: Search for stale runtime imports**

Run:

```bash
rg "browser_pool\\.runtime|from \\.runtime import|BrowserPool" src/octowright/browser_pool tests/test_engines.py
```

Expected: no `browser_pool.runtime` import remains. `BrowserPool` should not be exported from `src/octowright/browser_pool/__init__.py`.

- [ ] **Step 5: Run targeted tests**

Run:

```bash
uv run pytest -q tests/test_engines.py tests/test_pool_architecture.py
```

Expected: tests pass or skip live-browser cases according to existing skip behavior; no import error from deleted runtime.

- [ ] **Step 6: Commit**

```bash
git add tests/test_engines.py tests/test_pool_architecture.py src/octowright/pool.py src/octowright/browser_pool/__init__.py
git add -u src/octowright/browser_pool/runtime.py
git commit -m "refactor: remove duplicate browser pool runtime"
```

---

### Task 3: Add Public Pool And Scenario State APIs

**Files:**
- Modify: `src/octowright/pool.py`
- Modify: `src/octowright/scenarios_pool.py`
- Test: `tests/test_pool_state_api.py`
- Test: `tests/test_scenarios_pool.py`

- [ ] **Step 1: Write pool API tests**

Create `tests/test_pool_state_api.py`:

```python
from __future__ import annotations

from types import SimpleNamespace

from octowright.pool import BrowserPool


def test_pool_public_state_api_reads_sessions_without_private_callers() -> None:
    pool = BrowserPool()
    session = SimpleNamespace(
        instance_id="abc123",
        kind="webkit",
        label="demo",
        profile="demo",
        url="https://example.com",
        log_path="/tmp/demo.jsonl",
        har_path=None,
    )
    pool._sessions["abc123"] = session  # type: ignore[assignment]

    assert pool.has_session("abc123") is True
    assert pool.maybe_get("abc123") is session
    assert list(pool.iter_sessions()) == [session]
    assert pool.active_count() == 1
    assert pool.list_sessions() == [
        {
            "instance_id": "abc123",
            "kind": "webkit",
            "label": "demo",
            "profile": "demo",
            "url": "https://example.com",
            "log_path": "/tmp/demo.jsonl",
            "har_path": None,
        }
    ]
```

- [ ] **Step 2: Write scenario API tests**

Add to `tests/test_scenarios_pool.py`:

```python
def test_public_live_lookup_helpers() -> None:
    sp = ScenarioPool()
    live = _live()
    sp._live[live.scenario_id] = live

    assert sp.has_live(live.scenario_id) is True
    assert sp.maybe_get(live.scenario_id) is live
    assert sp.has_live("missing") is False
    assert sp.maybe_get("missing") is None
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
uv run pytest -q tests/test_pool_state_api.py tests/test_scenarios_pool.py::test_public_live_lookup_helpers
```

Expected: fails because the new public APIs do not exist.

- [ ] **Step 4: Implement pool APIs**

Add to `BrowserPool` in `src/octowright/pool.py`:

```python
from collections.abc import Iterable
```

Then add methods:

```python
def maybe_get(self, instance_id: str) -> BrowserSession | None:
    return self._sessions.get(instance_id)

def has_session(self, instance_id: str) -> bool:
    return instance_id in self._sessions

def iter_sessions(self) -> Iterable[BrowserSession]:
    return tuple(self._sessions.values())

def active_count(self) -> int:
    return len(self._sessions)
```

Keep `get()` and `list_sessions()` as the strict and serialized APIs.

- [ ] **Step 5: Implement scenario APIs**

Add to `ScenarioPool` in `src/octowright/scenarios_pool.py`:

```python
def maybe_get(self, scenario_id: str) -> LiveScenario | None:
    return self._live.get(scenario_id)

def has_live(self, scenario_id: str) -> bool:
    return scenario_id in self._live
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest -q tests/test_pool_state_api.py tests/test_scenarios_pool.py::test_public_live_lookup_helpers
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/pool.py src/octowright/scenarios_pool.py tests/test_pool_state_api.py tests/test_scenarios_pool.py
git commit -m "refactor: add public live state accessors"
```

---

### Task 4: Make Launch Cleanup Fail-Safe

**Files:**
- Modify: `src/octowright/pool.py`
- Test: `tests/test_pool_launch_cleanup.py`

- [ ] **Step 1: Write failing cleanup tests**

Create `tests/test_pool_launch_cleanup.py`:

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octowright.pool import BrowserPool


class FakeRecorder:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.closed = False

    def record(self, _action: str, **_fields: object) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class FakePage:
    video = None

    async def goto(self, _url: str) -> None:
        raise RuntimeError("Executable doesn't exist at /missing/chromium")


class FakeContext:
    def __init__(self) -> None:
        self.pages = []
        self.closed = False
        self.tracing = SimpleNamespace(start=self._tracing_start)

    async def new_page(self) -> FakePage:
        return FakePage()

    async def close(self) -> None:
        self.closed = True

    async def add_init_script(self, *, script: str) -> None:
        return None

    async def _tracing_start(self, **_kwargs: object) -> None:
        return None

    def on(self, *_args: object) -> None:
        return None


class FakeBrowser:
    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False

    async def new_context(self, **_kwargs: object) -> FakeContext:
        return self.context

    async def close(self) -> None:
        self.closed = True

    def on(self, *_args: object) -> None:
        return None


class FakeBrowserType:
    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser

    async def launch(self, **_kwargs: object) -> FakeBrowser:
        return self.browser


@pytest.mark.asyncio
async def test_launch_failure_closes_context_browser_and_recorder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    context = FakeContext()
    browser = FakeBrowser(context)
    pool = BrowserPool()
    pool._pw = SimpleNamespace(chromium=FakeBrowserType(browser))
    recorder_holder: dict[str, FakeRecorder] = {}

    def fake_recorder(path: Path) -> FakeRecorder:
        recorder = FakeRecorder(path)
        recorder_holder["recorder"] = recorder
        return recorder

    monkeypatch.setattr("octowright.pool.RECORDINGS_DIR", tmp_path)
    monkeypatch.setattr("octowright.pool.Recorder", fake_recorder)

    with pytest.raises(RuntimeError):
        await pool.launch(kind="chromium", url="https://example.com", headed=False)

    assert context.closed is True
    assert browser.closed is True
    assert recorder_holder["recorder"].closed is True
    assert pool.list_sessions() == []
```

- [ ] **Step 2: Run test to verify failure**

Run:

```bash
uv run pytest -q tests/test_pool_launch_cleanup.py
```

Expected: fails because launch does not close all created resources on initial navigation failure.

- [ ] **Step 3: Implement cleanup in `pool.py`**

Inside `BrowserPool.launch`, initialize these locals before Playwright creation:

```python
browser: Any | None = None
context: Any | None = None
page: Any | None = None
recorder: Recorder | None = None
registered = False
```

Wrap the body after validation in:

```python
try:
    ...
    recorder = Recorder(log_path)
    ...
    await page.goto(target_url)
    ...
    self._sessions[instance_id] = new_session
    registered = True
    ...
    return result
except Exception as exc:
    if not registered:
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if recorder is not None:
            try:
                recorder.close()
            except Exception:
                pass
    raise maybe_wrap_playwright_error(exc, kind=kind) from exc
```

Keep persistent contexts safe by allowing `browser` to remain `None`; context close is enough there.

- [ ] **Step 4: Run targeted tests**

Run:

```bash
uv run pytest -q tests/test_pool_launch_cleanup.py tests/test_engines.py
```

Expected: cleanup test passes; engine tests collect and preserve existing pass/skip behavior.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/pool.py tests/test_pool_launch_cleanup.py
git commit -m "fix: clean up failed browser launches"
```

---

### Task 5: Fix Session-Scoped Handoff

**Files:**
- Modify: `src/octowright/pool.py`
- Test: `tests/test_handoff.py`
- Test: `tests/test_session_mode.py`

- [ ] **Step 1: Add session mode storage test**

Add to `tests/test_session_mode.py`:

```python
@pytest.mark.asyncio
async def test_session_mode_records_user_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from octowright.pool import BrowserPool

    pool = BrowserPool()
    monkeypatch.setattr("tempfile.mkdtemp", lambda prefix: str(tmp_path / "session-dir"))

    result = await pool.launch(
        kind="chromium",
        url="data:text/html,<title>session</title>",
        headed=False,
        label="work",
        session=True,
    )
    session = pool.get(result["instance_id"])
    try:
        assert session.user_data_dir == tmp_path / "session-dir"
    finally:
        await pool.close_all()
```

If Playwright browser binaries are missing, use the existing live-browser skip style from `tests/test_session_mode.py`; do not make the test fail solely because local browser binaries are absent.

- [ ] **Step 2: Add handoff preservation test**

Add to `tests/test_handoff.py`:

```python
@pytest.mark.asyncio
async def test_handoff_preserves_session_scoped_tmpdir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pool = BrowserPool()
    source = SimpleNamespace(
        instance_id="old-session",
        kind="chromium",
        label="scratch",
        profile=None,
        user_data_dir=tmp_path / "session-dir",
        page=SimpleNamespace(url="https://example.com"),
        url="https://example.com",
        stabilize=False,
        trace=False,
        har_path=None,
    )
    pool._sessions["old-session"] = source  # type: ignore[assignment]
    launched: dict[str, object] = {}

    async def fake_close(instance_id: str) -> dict[str, object]:
        pool._sessions.pop(instance_id, None)
        return {"closed": True}

    async def fake_launch(**kwargs: object) -> dict[str, object]:
        launched.update(kwargs)
        return {
            "instance_id": "new-session",
            "kind": "chromium",
            "label": kwargs.get("label"),
            "profile": kwargs.get("profile"),
            "url": kwargs.get("url"),
            "log_path": "/tmp/new.jsonl",
            "record_video": False,
            "trace": False,
        }

    monkeypatch.setattr(pool, "close", fake_close)
    monkeypatch.setattr(pool, "launch", fake_launch)

    result = await BrowserPool.handoff(pool, "old-session", headed=False)

    assert result["new_instance_id"] == "new-session"
    assert launched["session"] is True
    assert launched["profile"] is None
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
uv run pytest -q tests/test_handoff.py::test_handoff_preserves_session_scoped_tmpdir tests/test_session_mode.py::test_session_mode_records_user_data_dir
```

Expected: fails until `user_data_dir` is stored and handoff propagates session mode.

- [ ] **Step 4: Implement handoff parity**

In `pool.py`, ensure `BrowserSession` gets:

```python
user_data_dir=Path(user_data_dir) if user_data_dir is not None else None,
```

Replace `handoff` implementation with the richer end-state signature:

```python
async def handoff(
    self,
    old_instance_id: str,
    *,
    headed: bool | None = None,
    close_original: bool = True,
    accept_stateless: bool = False,
) -> dict[str, Any]:
    source = self.get(old_instance_id)
    source_profile = source.profile
    source_user_data_dir = getattr(source, "user_data_dir", None)
    if source_profile is None and source_user_data_dir is None and not accept_stateless:
        raise ValueError(
            "handoff would be stateless: source has no profile/user_data_dir; pass accept_stateless=True to proceed"
        )
    if not close_original and (source_profile is not None or source_user_data_dir is not None):
        raise ValueError("persistent handoff requires close_original=True so the state directory can be safely reused")

    target_url = getattr(source.page, "url", None) or source.url
    session_scoped = source_profile is None and source_user_data_dir is not None
    close_result: dict[str, Any] | None = None
    if close_original:
        close_result = await self.close(old_instance_id)

    launch = await self.launch(
        kind=source.kind,
        url=target_url,
        headed=headed,
        label=source.label,
        profile=source_profile,
        stabilize=getattr(source, "stabilize", False),
        trace=getattr(source, "trace", False),
        har=bool(getattr(source, "har_path", None)),
        har_path=str(source.har_path) if getattr(source, "har_path", None) else None,
        session=session_scoped,
    )

    return {
        "ok": True,
        "old_instance_id": old_instance_id,
        "new_instance_id": launch["instance_id"],
        "old_closed": bool(close_result and close_result.get("closed")),
        "profile": source_profile,
        "kind": source.kind,
        "url": target_url,
        "har_path": launch.get("har_path"),
    }
```

- [ ] **Step 5: Run targeted tests**

Run:

```bash
uv run pytest -q tests/test_handoff.py tests/test_session_mode.py
```

Expected: all handoff/session-mode tests pass or skip live-browser cases according to existing patterns.

- [ ] **Step 6: Commit**

```bash
git add src/octowright/pool.py tests/test_handoff.py tests/test_session_mode.py
git commit -m "fix: preserve session-scoped state during handoff"
```

---

### Task 6: Refactor HTTP And Server Code Off Private Live State

**Files:**
- Modify: `src/octowright/http/discovery.py`
- Modify: `src/octowright/http/routes/sessions.py`
- Modify: `src/octowright/http/routes/scenarios.py`
- Modify: `src/octowright/server/meta.py`
- Modify: `src/octowright/server/macros.py`
- Modify: `src/octowright/server/personas.py`
- Modify: `src/octowright/server/scenarios.py`
- Test: existing `tests/test_http_server.py`, `tests/test_http_server_writes.py`, `tests/test_server_macros_tools.py`, `tests/test_status_tool.py`

- [ ] **Step 1: Find private state reads**

Run:

```bash
rg "_sessions|_live" src/octowright
```

Expected current hits in HTTP/server modules. Pool internals and tests can still use private state for setup.

- [ ] **Step 2: Refactor discovery helpers**

In `src/octowright/http/discovery.py`, replace:

```python
sessions = pool._sessions
return sessions.get(session_id)
```

with:

```python
return _state.pool.maybe_get(session_id)
```

Replace any direct live iteration with:

```python
for session in _state.pool.iter_sessions():
    ...
```

- [ ] **Step 3: Refactor session routes**

In `src/octowright/http/routes/sessions.py`, replace:

```python
live = [_live_summary(s) for s in pool._sessions.values()]
```

with:

```python
live = [_live_summary(s) for s in pool.iter_sessions()]
```

Replace:

```python
if sid not in pool._sessions:
```

with:

```python
if not pool.has_session(sid):
```

Replace:

```python
session = pool._sessions[sid]
```

with:

```python
session = pool.get(sid)
```

- [ ] **Step 4: Refactor scenario routes**

In `src/octowright/http/routes/scenarios.py`, replace:

```python
if sid not in spool._live:
```

with:

```python
if not spool.has_live(sid):
```

- [ ] **Step 5: Refactor server modules**

In `src/octowright/server/meta.py`, replace:

```python
live_count = len(pool._sessions)
```

with:

```python
live_count = pool.active_count()
```

In `src/octowright/server/macros.py`, replace:

```python
for session in pool._sessions.values():
```

with:

```python
for session in pool.iter_sessions():
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest -q tests/test_http_server.py tests/test_http_server_writes.py tests/test_server_macros_tools.py tests/test_status_tool.py
```

Expected: pass.

- [ ] **Step 7: Confirm source private reads are restricted**

Run:

```bash
rg "_sessions|_live" src/octowright
```

Expected: remaining `_sessions` references are in `pool.py`, listener internals that evict from the pool, and narrowly justified modules. Remaining `_live` references are in `scenarios_pool.py` only.

- [ ] **Step 8: Commit**

```bash
git add src/octowright/http/discovery.py src/octowright/http/routes/sessions.py src/octowright/http/routes/scenarios.py src/octowright/server/meta.py src/octowright/server/macros.py
git commit -m "refactor: route live state through public accessors"
```

---

### Task 7: Add Dashboard Exposure Guard

**Files:**
- Modify: `src/octowright/defaults.py`
- Create: `src/octowright/http/exposure.py`
- Modify: `src/octowright/http/app.py`
- Modify: `src/octowright/http/routes/sessions.py`
- Modify: `src/octowright/http/routes/scenarios.py`
- Modify: `src/octowright/http/routes/meta.py`
- Modify: `src/octowright/http/routes/media.py`
- Modify: `src/octowright/http/routes/events.py`
- Test: `tests/test_http_exposure.py`

- [ ] **Step 1: Write exposure tests**

Create `tests/test_http_exposure.py`:

```python
from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from octowright.http.exposure import guard_sensitive_http, is_loopback_host


async def sensitive(_request):
    return JSONResponse({"ok": True})


async def public(_request):
    return JSONResponse({"ok": True})


def test_loopback_host_detection() -> None:
    assert is_loopback_host("127.0.0.1") is True
    assert is_loopback_host("localhost") is True
    assert is_loopback_host("::1") is True
    assert is_loopback_host("0.0.0.0") is False
    assert is_loopback_host("192.168.1.10") is False


def test_sensitive_route_denied_on_non_loopback_without_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", raising=False)
    app = Starlette(routes=[Route("/sensitive", guard_sensitive_http(sensitive)), Route("/public", public)])
    app.state.octowright_http_host = "0.0.0.0"
    client = TestClient(app)

    response = client.get("/sensitive")

    assert response.status_code == 403
    assert response.json()["error"] == "remote dashboard access is disabled"
    assert client.get("/public").status_code == 200


def test_sensitive_route_allowed_on_non_loopback_with_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD", "1")
    app = Starlette(routes=[Route("/sensitive", guard_sensitive_http(sensitive))])
    app.state.octowright_http_host = "0.0.0.0"
    client = TestClient(app)

    assert client.get("/sensitive").status_code == 200
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest -q tests/test_http_exposure.py
```

Expected: fails because `octowright.http.exposure` does not exist.

- [ ] **Step 3: Add defaults**

In `src/octowright/defaults.py`, add:

```python
DASHBOARD_REMOTE_ALLOWED = os.environ.get("OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD") == "1"
NETWORK_EVENT_LIMIT = int(os.environ.get("OCTOWRIGHT_NETWORK_EVENT_LIMIT", "5000"))
```

`NETWORK_EVENT_LIMIT` is used in Task 8.

- [ ] **Step 4: Implement exposure helper**

Create `src/octowright/http/exposure.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import functools
import ipaddress
from collections.abc import Awaitable, Callable
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..defaults import DASHBOARD_REMOTE_ALLOWED, HTTP_HOST


def is_loopback_host(host: str | None) -> bool:
    if host is None:
        return is_loopback_host(HTTP_HOST)
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def remote_dashboard_allowed() -> bool:
    return DASHBOARD_REMOTE_ALLOWED


def sensitive_allowed_for_request(request: Request) -> bool:
    host = getattr(request.app.state, "octowright_http_host", HTTP_HOST)
    return is_loopback_host(host) or remote_dashboard_allowed()


def guard_sensitive_http(handler: Callable[[Request], Awaitable[Response]]) -> Callable[[Request], Awaitable[Response]]:
    @functools.wraps(handler)
    async def _wrapped(request: Request) -> Response:
        if not sensitive_allowed_for_request(request):
            return JSONResponse(
                {
                    "error": "remote dashboard access is disabled",
                    "hint": "bind to 127.0.0.1 or set OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1",
                },
                status_code=403,
            )
        return await handler(request)

    return _wrapped
```

- [ ] **Step 5: Store bound host on app**

In `src/octowright/http/app.py`, after creating the app, set a default:

```python
app.state.octowright_http_host = "127.0.0.1"
```

In `src/octowright/http/__init__.py`, inside `serve_app` after `build_app(...)` returns and before handing the app to uvicorn, set:

```python
app.state.octowright_http_host = host
```

- [ ] **Step 6: Guard sensitive routes**

Import in route modules:

```python
from ..exposure import guard_sensitive_http
```

Wrap sensitive `Route` handlers. Example in `sessions.routes()`:

```python
Route("/api/sessions", guard_sensitive_http(session_launch), methods=["POST"]),
Route("/api/sessions/{id}", guard_sensitive_http(session_close), methods=["DELETE"]),
Route("/api/sessions/{id}/navigate", guard_sensitive_http(session_navigate), methods=["POST"]),
Route("/api/sessions/{id}/recording", guard_sensitive_http(recording_delete), methods=["DELETE"]),
```

Apply the same wrapper to scenario writes, persona update, media file serving, live screenshots, trace open, markdown, events/raw captured endpoints, console, and downloads. Leave health unwrapped.

- [ ] **Step 7: Run exposure and HTTP tests**

Run:

```bash
uv run pytest -q tests/test_http_exposure.py tests/test_http_server.py tests/test_http_server_writes.py tests/test_http_meta_routes.py
```

Expected: pass. Existing tests that build apps without setting host use the default loopback state and continue to pass.

- [ ] **Step 8: Commit**

```bash
git add src/octowright/defaults.py src/octowright/http/exposure.py src/octowright/http/app.py src/octowright/http/__init__.py src/octowright/http/routes tests/test_http_exposure.py
git commit -m "fix: block sensitive dashboard routes on remote binds"
```

---

### Task 8: Bound Network Capture And Drain Background Tasks

**Files:**
- Modify: `src/octowright/session/core.py`
- Modify: `src/octowright/session/core_ops_mixin.py`
- Modify: `src/octowright/session/_protocols.py`
- Test: `tests/test_session_lifecycle.py`
- Test: `tests/test_server_browser_inspect_tools.py` or existing network tests

- [ ] **Step 1: Write bounded network test**

Create `tests/test_session_lifecycle.py`:

```python
from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace

import pytest

from octowright.session.core import BrowserSession


def make_session() -> BrowserSession:
    recorder = SimpleNamespace(record=lambda *args, **kwargs: None, close=lambda: None)
    return BrowserSession(
        instance_id="sid",
        kind="chromium",
        label=None,
        url="about:blank",
        browser=None,
        context=SimpleNamespace(close=lambda: None),
        page=SimpleNamespace(),
        recorder=recorder,
        log_path=__import__("pathlib").Path("/tmp/sid.jsonl"),
    )


def test_network_capture_is_bounded() -> None:
    session = make_session()
    session._network_requests = deque(maxlen=2)

    for i in range(3):
        request = SimpleNamespace(url=f"https://example.com/{i}", method="GET", resource_type="fetch")
        response = SimpleNamespace(request=request, status=200, status_text="OK")
        session._handle_response(response)

    result = session.get_network_requests()

    assert result["total_retained"] == 2
    assert result["dropped"] == 1
    assert [r["url"] for r in result["requests"]] == ["https://example.com/1", "https://example.com/2"]
```

- [ ] **Step 2: Write background drain test**

Add to `tests/test_session_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_close_waits_for_background_tasks_before_recorder_close() -> None:
    events: list[str] = []

    async def bg() -> None:
        await asyncio.sleep(0)
        events.append("task")

    class Context:
        tracing = SimpleNamespace(stop=lambda **kwargs: None)

        async def close(self) -> None:
            events.append("context")

    class Recorder:
        def record(self, *_args, **_kwargs) -> None:
            return None

        def close(self) -> None:
            events.append("recorder")

    session = BrowserSession(
        instance_id="sid",
        kind="chromium",
        label=None,
        url="about:blank",
        browser=None,
        context=Context(),
        page=SimpleNamespace(),
        recorder=Recorder(),
        log_path=__import__("pathlib").Path("/tmp/sid.jsonl"),
    )
    task = asyncio.create_task(bg())
    session._bg_tasks.add(task)

    await session.close()

    assert events.index("task") < events.index("recorder")
```

- [ ] **Step 3: Run tests to verify failure**

Run:

```bash
uv run pytest -q tests/test_session_lifecycle.py
```

Expected: fails until network capture is bounded and close drains tasks.

- [ ] **Step 4: Implement bounded fields**

In `src/octowright/session/core.py`, import:

```python
from collections import deque
```

Add default limit import:

```python
from ..defaults import NETWORK_EVENT_LIMIT
```

Change `_network_requests` field:

```python
_network_requests: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=NETWORK_EVENT_LIMIT))
_network_requests_dropped: int = 0
```

Update `src/octowright/session/_protocols.py` to match:

```python
_network_requests: deque[dict[str, Any]]
_network_requests_dropped: int
```

- [ ] **Step 5: Implement bounded append helper**

In `SessionOpsMixin`, add:

```python
def _append_network_request(self, entry: dict[str, Any]) -> None:
    before = len(self._network_requests)
    self._network_requests.append(entry)
    if len(self._network_requests) == before and getattr(self._network_requests, "maxlen", None) is not None:
        self._network_requests_dropped += 1
```

Replace direct `.append(...)` in `_handle_response` and `_handle_request_failed` with `_append_network_request({...})`.

Update `get_network_requests` return:

```python
return {
    "requests": sliced,
    "next_cursor": len(self._network_requests),
    "total_retained": len(self._network_requests),
    "dropped": self._network_requests_dropped,
    "total": len(self._network_requests),
}
```

Keep `"total"` for backward compatibility with existing frontend/server callers; use `"total_retained"` for clarity.

- [ ] **Step 6: Drain background tasks before recorder close**

In `SessionOpsMixin`, add:

```python
async def _drain_background_tasks(self, timeout: float = 2.0) -> None:
    import asyncio
    import contextlib

    tasks = [task for task in list(self._bg_tasks) if not task.done()]
    if not tasks:
        return
    done, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    for task in done:
        with contextlib.suppress(Exception):
            task.result()
```

In `close()`, call before `self.recorder.record("close", ...)`:

```python
await self._drain_background_tasks()
```

- [ ] **Step 7: Run lifecycle tests**

Run:

```bash
uv run pytest -q tests/test_session_lifecycle.py tests/test_server_browser_inspect_tools.py
```

Expected: pass.

- [ ] **Step 8: Commit**

```bash
git add src/octowright/session/core.py src/octowright/session/core_ops_mixin.py src/octowright/session/_protocols.py tests/test_session_lifecycle.py
git commit -m "fix: bound session memory and drain background tasks"
```

---

### Task 9: Declare Runtime Dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Test: `tests/test_defaults.py` or dependency smoke command

- [ ] **Step 1: Add `httpx` dependency**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    "httpx>=0.27",
```

- [ ] **Step 2: Sync lockfile**

Run:

```bash
uv lock
```

Expected: `uv.lock` updates if needed.

- [ ] **Step 3: Run dependency smoke**

Run:

```bash
uv run python -c "import httpx; import octowright.singleton; import octowright.proxy_bridge"
```

Expected: command exits 0.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "fix: declare httpx runtime dependency"
```

---

### Task 10: Final Verification And Cleanup

**Files:**
- All changed files

- [ ] **Step 1: Confirm no duplicate runtime or stale imports**

Run:

```bash
test ! -f src/octowright/browser_pool/runtime.py
rg "browser_pool\\.runtime|from \\.runtime import" src tests
```

Expected: first command exits 0; second command has no matches.

- [ ] **Step 2: Run full Python tests**

Run:

```bash
uv run pytest -q tests/
```

Expected: collection succeeds and all non-skipped tests pass.

- [ ] **Step 3: Run lint/quality**

Run:

```bash
make lint
```

Expected: ruff check, ruff format check, mypy, ty, bandit, codespell, and SPDX checks pass.

- [ ] **Step 4: Run frontend tests**

Run:

```bash
npm run test
```

Working directory:

```bash
packages/octowright-frontend
```

Expected: Vitest suite passes.

- [ ] **Step 5: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only intentional hardening changes remain uncommitted. Existing untracked `.gemini/` may remain ignored by this work.

- [ ] **Step 6: Final commit if any verification-only fixes were needed**

If verification required additional fixes, commit them:

```bash
git add <changed-files>
git commit -m "fix: complete pre-release hardening verification"
```

Expected: branch contains the spec commit plus focused implementation commits, and the final verification commands pass.

---

## Plan Self-Review

- Spec coverage: tasks cover canonical pool, duplicate runtime deletion, public state APIs, launch cleanup, session handoff, dashboard exposure, bounded memory, background task draining, dependency declaration, and full verification.
- Placeholder scan: no incomplete-marker placeholders remain. Each task includes exact files, code snippets, commands, and expected outcomes.
- Type consistency: `BrowserPool` APIs, `ScenarioPool` APIs, `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD`, and `OCTOWRIGHT_NETWORK_EVENT_LIMIT` match the approved design spec.
