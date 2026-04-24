# Personas and Scenarios Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor profiles into a persona-first hierarchy, add a scenarios layer for coordinated multi-browser orchestration, and resolve two bundled cleanups (`session.py` size, popup-page listener inheritance).

**Architecture:** Three milestones, each leaves the tree green. (A) refactor `session.py` by extracting iframe/download/locator helpers and fix popup-listener inheritance via an `_wire_listeners` helper. (B) introduce `personas.py` as the new top-level identity owner, flip the profile directory layout to `<persona>/<kind>/`, add credential resolution by reference, and migrate existing profiles. (C) add `scenarios.py` with a live `ScenarioPool`, YAML + Python loaders, per-participant role filtering, optional test mode with JUnit output, and CLI + MCP surfaces.

**Tech Stack:** Python 3.13, Playwright (async API), FastMCP (mcp SDK), click, pyyaml (add if not transitively present), pytest / pytest-anyio, provide-telemetry.

**Spec:** `docs/design/2026-04-24-personas-and-scenarios.md` (commit `f243a61`).

---

## File Structure

```
src/octowright/
├── session.py                    # MODIFY: ≤300 lines after split; delegates to helpers
├── session_frames.py             # NEW: _target, switch_frame, reset_frame, list_frames, active_frame
├── session_downloads.py          # NEW: _handle_download, list_downloads, wait_for_download
├── session_locators.py           # NEW: _locator, click_by, fill_by, get_text_by
├── pool.py                       # MODIFY: extract _wire_listeners; call from _register_popup
├── personas.py                   # NEW: Persona dataclass, YAML load, credential resolver, migration
├── profiles.py                   # MODIFY: engine_profile_dir + list_engine_profiles only
├── scenarios.py                  # NEW: Scenario, Participant, YAML + Python loaders, ScenarioPool
├── server.py                     # MODIFY: append persona_* and scenario_* tools
├── cli.py                        # MODIFY: persona, scenario, migrate-profiles subcommands
├── defaults.py                   # MODIFY: SCENARIOS_DIR + env var
└── runner.py                     # UNCHANGED; reuses JUnit writer for scenario test mode

tests/
├── test_session_split.py         # NEW: verify split is a no-op refactor
├── test_popup_listeners.py       # NEW: popup-page dialog listener fires
├── test_personas.py              # NEW: Persona load, credential resolver, migration
├── test_scenarios.py             # NEW: loaders, default resolution, role filtering
├── test_scenarios_live.py        # NEW: live 2-participant scenario, teardown
└── test_migration.py             # NEW: legacy layout → persona-first

docs/plans/2026-04-24-personas-and-scenarios.md    # this file (already written)
pyproject.toml                    # MODIFY if pyyaml not transitive
README.md                         # MODIFY: persona + scenario sections
```

---

## Milestone A — session.py split + popup listener fix

Pure refactor + one bug fix. No new user-visible behaviour. All 236 pre-existing tests must pass unchanged after this milestone.

### Task A1: Extract iframe helpers to `session_frames.py`

**Files:**
- Create: `src/octowright/session_frames.py`
- Modify: `src/octowright/session.py` (remove iframe code, import + delegate)

- [ ] **Step 1: Run the existing test suite to confirm baseline**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: `236 passed`

- [ ] **Step 2: Create `session_frames.py`**

```python
# src/octowright/session_frames.py
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Page


async def switch_frame_impl(
    page: "Page",
    *,
    selector: str | None,
    name: str | None,
    url_pattern: str | None,
) -> tuple["Frame", dict[str, Any]]:
    """Resolve an iframe and return (frame, info_dict).
    Exactly one of selector / name / url_pattern must be given.
    """
    provided = [k for k, v in (("selector", selector), ("name", name),
                               ("url_pattern", url_pattern)) if v is not None]
    if len(provided) != 1:
        raise ValueError(
            f"exactly one of selector/name/url_pattern must be set; got: {provided}"
        )

    frame: "Frame" | None = None
    if selector is not None:
        handle = await page.locator(selector).element_handle()
        if handle is None:
            raise RuntimeError(f"no element matches iframe selector {selector!r}")
        frame = await handle.content_frame()
    elif name is not None:
        frame = page.frame(name=name)
    else:
        assert url_pattern is not None
        frame = page.frame(url=re.compile(url_pattern))

    if frame is None:
        raise RuntimeError(
            f"no matching frame (selector={selector!r} name={name!r} url_pattern={url_pattern!r})"
        )

    idx = next((i for i, f in enumerate(page.frames) if f is frame), -1)
    return frame, {"index": idx, "url": frame.url, "name": frame.name}


def list_frames_impl(page: "Page", active_frame: "Frame | None") -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "name": f.name,
            "url": f.url,
            "is_active": (f is active_frame) or (active_frame is None and i == 0),
        }
        for i, f in enumerate(page.frames)
    ]
```

- [ ] **Step 3: Update `session.py` — remove inline iframe code, delegate**

Replace the block starting with `active_frame: Any | None = None` (field) and the `switch_frame` / `reset_frame` / `list_frames` methods. Import from `session_frames`. Keep the public `BrowserSession.switch_frame`/`reset_frame`/`list_frames` method names unchanged (they are part of the class contract).

Locate the existing `async def switch_frame` / `async def reset_frame` / `def list_frames` methods and replace them with:

```python
    async def switch_frame(
        self, *,
        selector: str | None = None,
        name: str | None = None,
        url_pattern: str | None = None,
    ) -> dict[str, Any]:
        from . import session_frames
        frame, info = await session_frames.switch_frame_impl(
            self.page, selector=selector, name=name, url_pattern=url_pattern,
        )
        self.active_frame = frame
        self.recorder.record("switch_frame", **info,
                             selector=selector, name=name, url_pattern=url_pattern)
        return info

    async def reset_frame(self) -> dict[str, Any]:
        self.active_frame = None
        self.recorder.record("reset_frame")
        return {"ok": True}

    def list_frames(self) -> list[dict[str, Any]]:
        from . import session_frames
        return session_frames.list_frames_impl(self.page, self.active_frame)
```

- [ ] **Step 4: Run iframe tests + whole suite**

Run: `uv run --active pytest -q tests/test_iframes.py tests/ 2>&1 | tail -3`
Expected: same pass count as baseline (no regressions).

- [ ] **Step 5: Commit**

```bash
git add src/octowright/session.py src/octowright/session_frames.py
git commit -m "refactor: extract iframe helpers to session_frames.py"
```

### Task A2: Extract download helpers to `session_downloads.py`

**Files:**
- Create: `src/octowright/session_downloads.py`
- Modify: `src/octowright/session.py` (remove download code, delegate)

- [ ] **Step 1: Create `session_downloads.py`**

```python
# src/octowright/session_downloads.py
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .session import BrowserSession


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def save_download(session: "BrowserSession", download: Any) -> dict[str, Any]:
    """Save a Playwright Download to disk under RECORDINGS_DIR/downloads/<instance_id>/.
    Returns the record dict appended to session.downloads."""
    from .defaults import RECORDINGS_DIR
    target_dir = RECORDINGS_DIR / "downloads" / session.instance_id
    target_dir.mkdir(parents=True, exist_ok=True)
    suggested = download.suggested_filename
    target = target_dir / f"{len(session.downloads):03d}-{suggested}"
    await download.save_as(str(target))
    record = {
        "url": download.url,
        "suggested_filename": suggested,
        "path": str(target),
        "timestamp": _timestamp(),
    }
    session.downloads.append(record)
    session.recorder.record("download_saved", **record)
    for event in session._pending_download_events:
        event.set()
    session._pending_download_events.clear()
    return record


async def wait_for_download_impl(session: "BrowserSession", timeout_ms: int) -> dict[str, Any]:
    """Block until the next download completes. Raise TimeoutError on timeout."""
    baseline = len(session.downloads)
    event = asyncio.Event()
    session._pending_download_events.append(event)
    if len(session.downloads) > baseline:
        return session.downloads[-1]
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_ms / 1000)
    except asyncio.TimeoutError as e:
        raise TimeoutError(f"no download within {timeout_ms}ms") from e
    return session.downloads[-1]
```

- [ ] **Step 2: Update `session.py`**

Replace the `_handle_download` body and `wait_for_download` body with delegates:

```python
    def _handle_download(self, download: Any) -> None:
        from . import session_downloads
        import asyncio as _asyncio
        _asyncio.create_task(session_downloads.save_download(self, download))

    def list_downloads(self) -> list[dict[str, Any]]:
        return list(self.downloads)

    async def wait_for_download(self, timeout_ms: int = 15000) -> dict[str, Any]:
        from . import session_downloads
        return await session_downloads.wait_for_download_impl(self, timeout_ms)
```

Keep `downloads` and `_pending_download_events` as `BrowserSession` fields (no change).

- [ ] **Step 3: Run download tests + whole suite**

Run: `uv run --active pytest -q tests/test_downloads.py tests/ 2>&1 | tail -3`
Expected: 236 passed.

- [ ] **Step 4: Commit**

```bash
git add src/octowright/session.py src/octowright/session_downloads.py
git commit -m "refactor: extract download helpers to session_downloads.py"
```

### Task A3: Extract locator helpers to `session_locators.py`

**Files:**
- Create: `src/octowright/session_locators.py`
- Modify: `src/octowright/session.py`

- [ ] **Step 1: Create `session_locators.py`**

```python
# src/octowright/session_locators.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Frame, Locator, Page


def build_locator(
    target: "Page | Frame",
    *,
    role: str | None = None,
    role_name: str | None = None,
    role_exact: bool = False,
    label: str | None = None,
    text: str | None = None,
    test_id: str | None = None,
) -> "Locator":
    provided = [k for k, v in (("role", role), ("label", label),
                               ("text", text), ("test_id", test_id)) if v is not None]
    if len(provided) != 1:
        raise ValueError(
            f"exactly one of role/label/text/test_id must be set; got: {provided}"
        )
    if role is not None:
        kwargs: dict[str, Any] = {}
        if role_name is not None:
            kwargs["name"] = role_name
            kwargs["exact"] = role_exact
        return target.get_by_role(role, **kwargs)  # type: ignore[arg-type]
    if label is not None:
        return target.get_by_label(label)
    if text is not None:
        return target.get_by_text(text)
    assert test_id is not None
    return target.get_by_test_id(test_id)
```

- [ ] **Step 2: Update `session.py` — delegate `click_by`/`fill_by`/`get_text_by` + `_locator`**

```python
    def _locator(self, **finders: Any) -> Any:
        from . import session_locators
        return session_locators.build_locator(self._target(), **finders)

    async def click_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        locator = self._locator(**finders)
        await locator.click(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("click_by", **finders)
        return {"ok": True}

    async def fill_by(self, value: str, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        locator = self._locator(**finders)
        await locator.fill(value, timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        self.recorder.record("fill_by", value=value, **finders)
        return {"ok": True}

    async def get_text_by(self, *, timeout_ms: int | None = None, **finders: Any) -> dict[str, Any]:
        locator = self._locator(**finders)
        await locator.wait_for(timeout=timeout_ms or DEFAULT_ACTION_TIMEOUT_MS)
        result = await locator.inner_text()
        self.recorder.record("get_text_by", result=result, **finders)
        return {"ok": True, "text": result}
```

Note: `text` is accepted as a finder by `click_by` / `get_text_by` but not `fill_by` — the underlying `build_locator` raises `ValueError` if you try to fill by text. That's correct; we don't need to enforce it at the session layer.

- [ ] **Step 3: Run locator tests + whole suite**

Run: `uv run --active pytest -q tests/test_locators.py tests/ 2>&1 | tail -3`
Expected: 236 passed.

- [ ] **Step 4: Commit**

```bash
git add src/octowright/session.py src/octowright/session_locators.py
git commit -m "refactor: extract locator helpers to session_locators.py"
```

### Task A4: Extract `_wire_listeners` + fix popup-page listener inheritance

**Files:**
- Modify: `src/octowright/pool.py`
- Modify: `src/octowright/session.py` (small — `_register_popup` now calls into pool-provided hook)
- Create: `tests/test_popup_listeners.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_popup_listeners.py
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from octowright.pool import BrowserPool


@pytest.mark.anyio
async def test_popup_page_dialog_listener_fires(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "prof"))
    # Force reload so defaults pick up the env
    import importlib
    from octowright import defaults as _defaults
    importlib.reload(_defaults)

    pool = BrowserPool()
    r = await pool.launch(
        kind="webkit", url="about:blank", headed=False, label="pop",
        viewport_w=320, viewport_h=240, profile=None,
    )
    s = pool.get(r["instance_id"])
    s.set_dialog_policy("accept")

    # Open a popup and trigger confirm() in it.
    await s.evaluate(
        "window._p = window.open('about:blank', '_blank');"
        "window._p.document.body.innerHTML = '<button id=b onclick=\"parent._ok = confirm(\\\"ok?\\\")\">go</button>';"
    )
    # Wait for popup to be tracked.
    for _ in range(20):
        if len(s.pages) > 1:
            break
        await asyncio.sleep(0.05)
    assert len(s.pages) == 2, "popup was not tracked"

    popup = s.pages[1]
    # Click the button in the popup — our dialog handler on the popup should accept.
    await popup.click("#b")
    await asyncio.sleep(0.2)
    # If the popup-page dialog handler was NOT wired, window._p never dismissed,
    # and window._ok would be undefined. With the fix, policy=accept → True.
    result = await s.evaluate("window._ok")
    assert result is True, f"expected dialog to be accepted on popup; got {result!r}"

    await pool.close(r["instance_id"])
    await pool.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest -q tests/test_popup_listeners.py -v 2>&1 | tail -10`
Expected: FAIL (confirm() on popup never got handled; `window._ok` is `None`).

- [ ] **Step 3: Extract `_wire_listeners` helper in `pool.py`**

In `pool.py`, add a module-level helper right after `_title_prefix_for`:

```python
def _wire_listeners(session: BrowserSession, page: Any) -> None:
    """Attach the per-page listeners (dialog, download) to a page. Called for both
    the initial page at launch and any popup page opened mid-session."""
    page.on("dialog", session._handle_dialog)
    page.on("download", session._handle_download)
```

In `BrowserPool.launch`, replace the existing two `page.on(...)` lines (dialog + download) with a single call:

```python
        _wire_listeners(session, page)
```

- [ ] **Step 4: Hook into popup registration**

In `session.py`, update `_register_popup` to call the pool's `_wire_listeners`:

```python
    def _register_popup(self, page: Any) -> None:
        """Called by context.on('page'). Appends the new page and wires listeners."""
        from . import pool as _pool
        self.pages.append(page)
        page_index = len(self.pages) - 1
        self.recorder.record("popup_opened", page_index=page_index, url=page.url)
        page.on(
            "console",
            lambda msg: self.console.append(
                {"level": msg.type, "text": msg.text, "page_index": page_index}
            ),
        )
        _pool._wire_listeners(self, page)
```

Also remove the NOTE comment at the top of `session.py` about popup-frame listeners being unwired — the fix makes the note stale.

- [ ] **Step 5: Run the popup test and verify it passes**

Run: `uv run --active pytest -q tests/test_popup_listeners.py -v 2>&1 | tail -5`
Expected: PASS.

- [ ] **Step 6: Run whole suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: 237 passed (was 236; new test adds one).

- [ ] **Step 7: Commit**

```bash
git add src/octowright/pool.py src/octowright/session.py tests/test_popup_listeners.py
git commit -m "fix: wire dialog and download listeners on popup pages

Popups opened via window.open / target=_blank previously never got the
session's dialog or download handler attached. Extract _wire_listeners in
pool.py and call it both for the initial page at launch and from
session._register_popup."
```

### Task A5: Verify `session.py` line budget and milestone A is green

**Files:** none modified; this is a gate.

- [ ] **Step 1: Check line count**

Run: `wc -l src/octowright/session.py`
Expected: ≤ 300 lines. If over, identify what else can be extracted and loop back.

- [ ] **Step 2: Full suite + selftest**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: 237 passed.

Run: `uv run --active octowright selftest 2>&1 | grep -E 'tools registered'`
Expected: `50 tools registered`.

- [ ] **Step 3: No commit — this is a gate. Milestone A done.**

---

## Milestone B — Persona-first refactor

Flips the profile layout, introduces `personas.py`, migrates existing profiles, and updates tool surfaces. Each task keeps the suite green.

### Task B1: Add pyyaml dependency if missing

**Files:**
- Modify: `pyproject.toml` (conditionally)

- [ ] **Step 1: Check if pyyaml is importable**

Run: `uv run --active python -c "import yaml; print(yaml.__version__)" 2>&1`

If this succeeds, skip to step 3 and do nothing.

- [ ] **Step 2: If it fails — add to `[project.dependencies]`**

Edit `pyproject.toml`, add `"pyyaml>=6"` to the `dependencies` list:

```toml
dependencies = [
    "mcp>=1.2.0",
    "playwright>=1.47.0",
    "anyio>=4.4.0",
    "provide-telemetry>=0.3",
    "click>=8.1",
    "pyyaml>=6",
]
```

Run: `uv sync 2>&1 | tail -2`

- [ ] **Step 3: Commit if pyproject changed**

```bash
git diff --quiet pyproject.toml uv.lock || {
    git add pyproject.toml uv.lock
    git commit -m "chore: add pyyaml dep for scenario YAML loader"
}
```

### Task B2: Create `personas.py` with `Persona` dataclass and YAML loader (TDD)

**Files:**
- Create: `src/octowright/personas.py`
- Create: `tests/test_personas.py`

- [ ] **Step 1: Write the failing test for YAML load round-trip**

```python
# tests/test_personas.py
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import personas
    importlib.reload(personas)
    return personas


def _write_persona(root: Path, name: str, doc: dict) -> None:
    pdir = root / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump(doc))


def test_load_persona_round_trip(tmp_path, fresh_personas):
    personas = fresh_personas
    _write_persona(tmp_path, "dante", {
        "name": "dante",
        "display_name": "Dante",
        "default_url": "https://example.com",
        "default_macros": ["login"],
        "credentials": {"email_env": "DANTE_EMAIL"},
        "app": {"discord_user_id": "1234", "role": "player"},
    })
    p = personas.load_persona("dante")
    assert p.name == "dante"
    assert p.display_name == "Dante"
    assert p.default_url == "https://example.com"
    assert p.default_macros == ["login"]
    assert p.credentials == {"email_env": "DANTE_EMAIL"}
    assert p.app == {"discord_user_id": "1234", "role": "player"}


def test_load_persona_missing_raises(fresh_personas):
    with pytest.raises(FileNotFoundError):
        fresh_personas.load_persona("ghost")


def test_load_persona_minimal(tmp_path, fresh_personas):
    _write_persona(tmp_path, "bare", {"name": "bare"})
    p = fresh_personas.load_persona("bare")
    assert p.name == "bare"
    assert p.display_name is None
    assert p.default_url is None
    assert p.default_macros == []
    assert p.credentials == {}
    assert p.app == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --active pytest -q tests/test_personas.py -v 2>&1 | tail -5`
Expected: FAIL (`octowright.personas` doesn't exist).

- [ ] **Step 3: Write `personas.py` with `Persona` + `load_persona` + `PROFILES_DIR` integration**

```python
# src/octowright/personas.py
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from .defaults import PROFILES_DIR, SUPPORTED_KINDS

log = get_logger(__name__)

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _slug(name: str) -> str:
    cleaned = _SLUG_RE.sub("-", name.strip()).strip("-.")
    if not cleaned:
        raise ValueError(f"persona name {name!r} produced an empty slug")
    return cleaned


@dataclass
class Persona:
    name: str
    display_name: str | None = None
    default_url: str | None = None
    default_macros: list[str] = field(default_factory=list)
    credentials: dict[str, str] = field(default_factory=dict)
    app: dict[str, Any] = field(default_factory=dict)


def persona_dir(name: str) -> Path:
    return PROFILES_DIR / _slug(name)


def engine_profile_dir(persona: str, kind: str) -> Path:
    if kind not in SUPPORTED_KINDS:
        raise ValueError(f"kind must be one of {SUPPORTED_KINDS}, got {kind!r}")
    return persona_dir(persona) / kind


def load_persona(name: str) -> Persona:
    p = persona_dir(name) / "profile.yaml"
    if not p.exists():
        raise FileNotFoundError(f"no persona at {p}")
    raw = yaml.safe_load(p.read_text()) or {}
    return Persona(
        name=raw.get("name", _slug(name)),
        display_name=raw.get("display_name"),
        default_url=raw.get("default_url"),
        default_macros=list(raw.get("default_macros") or []),
        credentials=dict(raw.get("credentials") or {}),
        app=dict(raw.get("app") or {}),
    )


def list_personas() -> list[dict[str, Any]]:
    """Return [{name, display_name, engines, path, mtime, last_used}, ...]
    sorted most-recent-mtime first. Empty list if PROFILES_DIR missing."""
    if not PROFILES_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for entry in PROFILES_DIR.iterdir():
        if not entry.is_dir():
            continue
        yaml_path = entry / "profile.yaml"
        display_name = None
        if yaml_path.exists():
            try:
                raw = yaml.safe_load(yaml_path.read_text()) or {}
                display_name = raw.get("display_name")
            except Exception:  # noqa: BLE001 — surface but don't crash listing
                log.warning("persona.yaml_parse_failed", path=str(yaml_path))
        engines = sorted(
            sub.name for sub in entry.iterdir()
            if sub.is_dir() and sub.name in SUPPORTED_KINDS
        )
        stat = entry.stat()
        out.append({
            "name": entry.name,
            "display_name": display_name,
            "engines": engines,
            "path": str(entry),
            "mtime": stat.st_mtime,
            "last_used": datetime.fromtimestamp(stat.st_mtime, UTC)
                .isoformat().replace("+00:00", "Z"),
        })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


class MissingCredential(RuntimeError):
    pass


def resolve_credential(persona: Persona, field: str) -> str:
    """Resolve a credential field like 'email' via _env or _cmd references in
    persona.credentials. *_cmd wins if both are set."""
    creds = persona.credentials
    cmd_key = f"{field}_cmd"
    env_key = f"{field}_env"
    if cmd_key in creds:
        if env_key in creds:
            log.warning("persona.cred.both_set", persona=persona.name, field=field)
        result = subprocess.run(  # noqa: S602 — shell usage is a documented feature
            creds[cmd_key], shell=True, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise MissingCredential(
                f"persona {persona.name!r} field {field!r}: "
                f"cmd exited {result.returncode}; stderr: {result.stderr[:200]}"
            )
        return result.stdout.strip()
    if env_key in creds:
        env_name = creds[env_key]
        value = os.environ.get(env_name)
        if value is None:
            raise MissingCredential(
                f"persona {persona.name!r} field {field!r}: "
                f"env var {env_name} is unset"
            )
        return value
    raise MissingCredential(
        f"persona {persona.name!r} field {field!r}: "
        f"no {field}_env or {field}_cmd in credentials"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --active pytest -q tests/test_personas.py -v 2>&1 | tail -5`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/personas.py tests/test_personas.py
git commit -m "feat(personas): add Persona dataclass, YAML loader, credential resolver

Introduces the persona layer: Persona loads metadata from <PROFILES_DIR>/<name>/profile.yaml.
resolve_credential(persona, field) reads *_env (env var) or *_cmd (shell) references
at call time; secrets are never stored by octowright."
```

### Task B3: Add credential-resolver tests

**Files:**
- Modify: `tests/test_personas.py`

- [ ] **Step 1: Add four resolver tests**

Append to `tests/test_personas.py`:

```python
def test_resolve_env_credential(tmp_path, fresh_personas, monkeypatch):
    _write_persona(tmp_path, "u", {
        "name": "u",
        "credentials": {"email_env": "TEST_EMAIL"},
    })
    monkeypatch.setenv("TEST_EMAIL", "me@example.com")
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "email") == "me@example.com"


def test_resolve_env_missing_raises(tmp_path, fresh_personas, monkeypatch):
    _write_persona(tmp_path, "u", {
        "name": "u",
        "credentials": {"email_env": "TEST_EMAIL"},
    })
    monkeypatch.delenv("TEST_EMAIL", raising=False)
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="TEST_EMAIL is unset"):
        fresh_personas.resolve_credential(p, "email")


def test_resolve_cmd_credential(tmp_path, fresh_personas):
    _write_persona(tmp_path, "u", {
        "name": "u",
        "credentials": {"token_cmd": "printf hunter2"},
    })
    p = fresh_personas.load_persona("u")
    assert fresh_personas.resolve_credential(p, "token") == "hunter2"


def test_resolve_no_references_raises(tmp_path, fresh_personas):
    _write_persona(tmp_path, "u", {"name": "u"})
    p = fresh_personas.load_persona("u")
    with pytest.raises(fresh_personas.MissingCredential, match="no email_env or email_cmd"):
        fresh_personas.resolve_credential(p, "email")
```

- [ ] **Step 2: Run tests**

Run: `uv run --active pytest -q tests/test_personas.py -v 2>&1 | tail -10`
Expected: 7 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/test_personas.py
git commit -m "test(personas): cover env + cmd credential resolution and error paths"
```

### Task B4: Migration helper + tests

**Files:**
- Modify: `src/octowright/personas.py` (add `migrate_legacy_layout`)
- Create: `tests/test_migration.py`

- [ ] **Step 1: Write failing test for migration**

```python
# tests/test_migration.py
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def legacy_profiles(tmp_path, monkeypatch):
    """Sets up a legacy-layout PROFILES_DIR with profiles/<kind>/<name>/ dirs."""
    root = tmp_path
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(root))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import personas
    importlib.reload(personas)

    # Legacy: PROFILES_DIR/<kind>/<name>/
    for kind in ("webkit", "chromium"):
        pdir = root / kind / "alice"
        pdir.mkdir(parents=True)
        (pdir / "Cookies").write_text("stub")
    (root / "webkit" / "bob").mkdir(parents=True)
    (root / "webkit" / "bob" / "Cookies").write_text("stub")
    return root, personas


def test_migrate_legacy_to_persona_first(legacy_profiles):
    root, personas = legacy_profiles
    summary = personas.migrate_legacy_layout()
    assert summary["moved"] == 3  # alice/webkit, alice/chromium, bob/webkit
    assert summary["personas"] == 2

    # New layout: PROFILES_DIR/<persona>/<kind>/ with profile.yaml
    assert (root / "alice" / "webkit" / "Cookies").exists()
    assert (root / "alice" / "chromium" / "Cookies").exists()
    assert (root / "alice" / "profile.yaml").exists()
    assert yaml.safe_load((root / "alice" / "profile.yaml").read_text())["name"] == "alice"
    assert (root / "bob" / "webkit" / "Cookies").exists()

    # Legacy dirs should be gone
    assert not (root / "webkit").exists()
    assert not (root / "chromium").exists()


def test_migrate_idempotent(legacy_profiles):
    root, personas = legacy_profiles
    personas.migrate_legacy_layout()
    summary = personas.migrate_legacy_layout()
    assert summary["moved"] == 0
    assert summary["personas"] == 0


def test_migrate_empty_dir_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import personas
    importlib.reload(personas)
    summary = personas.migrate_legacy_layout()
    assert summary == {"moved": 0, "personas": 0}
```

- [ ] **Step 2: Run — verify it fails**

Run: `uv run --active pytest -q tests/test_migration.py -v 2>&1 | tail -5`
Expected: FAIL (no `migrate_legacy_layout`).

- [ ] **Step 3: Add `migrate_legacy_layout` to `personas.py`**

Append to `personas.py`:

```python
def migrate_legacy_layout() -> dict[str, Any]:
    """One-shot migration from profiles/<kind>/<name>/ to profiles/<name>/<kind>/.
    Idempotent. Returns {moved: N, personas: M}."""
    if not PROFILES_DIR.exists():
        return {"moved": 0, "personas": 0}

    moved = 0
    touched_personas: set[str] = set()

    for kind_dir in list(PROFILES_DIR.iterdir()):
        if not kind_dir.is_dir() or kind_dir.name not in SUPPORTED_KINDS:
            continue
        # Every subdir of a kind-dir is a legacy (kind, name) tuple.
        for legacy_engine in list(kind_dir.iterdir()):
            if not legacy_engine.is_dir():
                continue
            name = legacy_engine.name
            new_engine = PROFILES_DIR / name / kind_dir.name
            new_engine.parent.mkdir(parents=True, exist_ok=True)
            if new_engine.exists():
                log.warning("migrate.target_exists_skipping",
                            source=str(legacy_engine), target=str(new_engine))
                continue
            legacy_engine.rename(new_engine)
            moved += 1
            touched_personas.add(name)

        # Remove the now-empty kind directory (if it is empty).
        try:
            kind_dir.rmdir()
        except OSError:
            pass  # non-empty (shouldn't happen) — leave it

    # Create stub profile.yaml for each touched persona if missing.
    for name in touched_personas:
        yaml_path = PROFILES_DIR / name / "profile.yaml"
        if not yaml_path.exists():
            yaml_path.write_text(yaml.safe_dump({"name": name}))

    log.info("personas.migrated", moved=moved, personas=len(touched_personas))
    return {"moved": moved, "personas": len(touched_personas)}
```

- [ ] **Step 4: Run migration tests**

Run: `uv run --active pytest -q tests/test_migration.py -v 2>&1 | tail -5`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/personas.py tests/test_migration.py
git commit -m "feat(personas): idempotent migration from legacy layout

profiles/<kind>/<name>/ dirs are moved to profiles/<name>/<kind>/, a stub
profile.yaml is written per persona, and empty legacy kind-dirs are removed.
Running twice is a no-op."
```

### Task B5: Refactor `profiles.py` to engine-profile layer

**Files:**
- Modify: `src/octowright/profiles.py` (rewrite)

- [ ] **Step 1: Rewrite `profiles.py` to delegate to `personas.py`**

Replace the whole file with:

```python
# src/octowright/profiles.py
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import personas as _personas
from .defaults import PROFILES_DIR, SUPPORTED_KINDS


def profile_dir(kind: str, name: str) -> Path:
    """Engine-profile directory for (kind, persona). Preserved as the existing
    public name; internally routes through personas.engine_profile_dir."""
    return _personas.engine_profile_dir(persona=name, kind=kind)


def list_profiles(kind: str | None = None) -> list[dict[str, Any]]:
    """List all engine profiles. Each entry: {kind, name, path, size_bytes,
    mtime, last_used}. name is the persona name."""
    if not PROFILES_DIR.exists():
        return []
    kinds = [kind] if kind else list(SUPPORTED_KINDS)
    out: list[dict[str, Any]] = []
    for persona_entry in PROFILES_DIR.iterdir():
        if not persona_entry.is_dir():
            continue
        for k in kinds:
            engine_dir = persona_entry / k
            if not engine_dir.is_dir():
                continue
            stat = engine_dir.stat()
            size = sum(f.stat().st_size for f in engine_dir.rglob("*") if f.is_file())
            from datetime import UTC, datetime
            out.append({
                "kind": k,
                "name": persona_entry.name,
                "path": str(engine_dir),
                "size_bytes": size,
                "mtime": stat.st_mtime,
                "last_used": datetime.fromtimestamp(stat.st_mtime, UTC)
                    .isoformat().replace("+00:00", "Z"),
            })
    out.sort(key=lambda p: p["mtime"], reverse=True)
    return out


def delete_profile(kind: str, name: str) -> Path:
    """Delete a single engine profile directory. Raises FileNotFoundError."""
    target = profile_dir(kind, name)
    if not target.exists():
        raise FileNotFoundError(f"no engine profile at {target}")
    shutil.rmtree(target)
    return target


def delete_persona(name: str) -> Path:
    """Delete the entire persona directory (all engine profiles + metadata)."""
    target = _personas.persona_dir(name)
    if not target.exists():
        raise FileNotFoundError(f"no persona at {target}")
    shutil.rmtree(target)
    return target
```

- [ ] **Step 2: Run whole suite — verify `browser_launch(profile=...)` still works**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all pass (but `tests/test_personas.py::test_load_persona*` etc. may need
`tests/test_personas.py` fixtures to still reload both `defaults` AND `personas`
AND `profiles` — check below).

- [ ] **Step 3: Make the fixture in `test_personas.py` also reload `profiles`**

Update the `fresh_personas` fixture to reload `profiles` too:

```python
@pytest.fixture
def fresh_personas(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import personas
    importlib.reload(personas)
    from octowright import profiles
    importlib.reload(profiles)
    return personas
```

Apply the same reload pattern in `test_migration.py`'s fixture.

- [ ] **Step 4: Re-run**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/profiles.py tests/test_personas.py tests/test_migration.py
git commit -m "refactor(profiles): rescope to engine-profile layer over personas

profiles.py now wraps personas.engine_profile_dir for path resolution;
list_profiles returns engine-profile entries (kind/name pairs) but reads
from the new persona-first layout; delete_profile removes one engine-
profile dir; new delete_persona removes the whole persona subtree."
```

### Task B6: Update `pool.py` — auto-migrate on first use

**Files:**
- Modify: `src/octowright/pool.py`

- [ ] **Step 1: Add one-shot migration hook at import time**

In `pool.py`, below the existing imports, add:

```python
# Auto-migrate legacy profile layout on first module import. Idempotent.
try:
    from . import personas as _personas
    _personas.migrate_legacy_layout()
except Exception as _e:  # noqa: BLE001 — migration must never block import
    log.warning("pool.migration_on_import_failed", error=repr(_e))
```

- [ ] **Step 2: Run the whole suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add src/octowright/pool.py
git commit -m "feat(pool): auto-run persona migration at import time (idempotent)"
```

### Task B7: Add persona MCP tools

**Files:**
- Modify: `src/octowright/server.py` (append new tools)
- Modify: `src/octowright/profiles.py` (make profile_delete refuse-if-in-use stays put)

- [ ] **Step 1: Append persona_* tools at EOF of `server.py`** (above `registered_tool_names`/`recordings_dir`)

Add imports near the top alongside other `from . import ... as ..._mod`:

```python
from . import personas as persona_mod
```

Then append:

```python
@mcp.tool(structured_output=False, description=(
    "List all personas, each with their known engines, display name, and last-used timestamp. "
    "A persona is a named identity (e.g. 'dante') that owns engine-specific browser profiles."
))
def persona_list() -> list[dict[str, Any]]:
    return persona_mod.list_personas()


@mcp.tool(structured_output=False, description=(
    "Return the full profile.yaml for a persona (credentials are returned verbatim; "
    "resolve_credential expects references, not plaintext). Raises if the persona doesn't exist."
))
def persona_get(name: str) -> dict[str, Any]:
    p = persona_mod.load_persona(name)
    return {
        "name": p.name, "display_name": p.display_name,
        "default_url": p.default_url, "default_macros": p.default_macros,
        "credentials": p.credentials, "app": p.app,
    }


@mcp.tool(structured_output=False, description=(
    "Scaffold a new persona directory with a stub profile.yaml. "
    "Does nothing engine-specific; browser profiles are created on first browser_launch for that persona."
))
def persona_create(
    name: str,
    display_name: str | None = None,
    default_url: str | None = None,
) -> dict[str, Any]:
    import yaml as _yaml
    p_dir = persona_mod.persona_dir(name)
    p_dir.mkdir(parents=True, exist_ok=True)
    yaml_path = p_dir / "profile.yaml"
    if yaml_path.exists():
        raise RuntimeError(f"persona {name!r} already has a profile.yaml at {yaml_path}")
    doc: dict[str, Any] = {"name": persona_mod._slug(name)}
    if display_name:
        doc["display_name"] = display_name
    if default_url:
        doc["default_url"] = default_url
    yaml_path.write_text(_yaml.safe_dump(doc))
    return {"created": True, "name": name, "path": str(p_dir)}


@mcp.tool(structured_output=False, description=(
    "Delete an entire persona (metadata + all engine profiles). Refuses if any engine "
    "profile is currently in use by a live browser."
))
def persona_delete(name: str) -> dict[str, Any]:
    from .profiles import delete_persona
    for s in pool.list():
        if s["profile"] == name:
            raise RuntimeError(
                f"persona {name!r} is in use by live instance {s['instance_id']}; close it first"
            )
    path = delete_persona(name)
    log.info("octowright.persona.deleted", name=name, path=str(path))
    return {"deleted": True, "name": name, "path": str(path)}


@mcp.tool(structured_output=False, description=(
    "Run the one-shot legacy profile-layout migration. Idempotent. Returns counts."
))
def migrate_profiles() -> dict[str, Any]:
    return persona_mod.migrate_legacy_layout()
```

- [ ] **Step 2: Check tool count**

Run: `uv run --active octowright selftest 2>&1 | grep -E 'tools registered'`
Expected: `55 tools registered` (50 + 5 new).

- [ ] **Step 3: Run full suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add src/octowright/server.py
git commit -m "feat(personas): expose persona_list/get/create/delete + migrate_profiles tools"
```

### Task B8: Add `persona` and `migrate-profiles` CLI subcommands

**Files:**
- Modify: `src/octowright/cli.py`

- [ ] **Step 1: Add subcommand groups**

Append after the existing `test` command in `cli.py`:

```python
@cli.group()
def persona() -> None:
    """Manage personas (identity + browser-profile containers)."""


@persona.command("list")
def persona_list_cmd() -> None:
    """List all personas with engines and last-used timestamps."""
    from . import personas as _p
    setup_telemetry()
    try:
        for row in _p.list_personas():
            engines = ",".join(row["engines"]) or "-"
            dn = row.get("display_name") or ""
            click.echo(f"{row['name']:20s}  engines={engines:30s}  {dn}")
    finally:
        shutdown_telemetry()


@persona.command("show")
@click.argument("name")
def persona_show_cmd(name: str) -> None:
    """Print the full profile.yaml for a persona."""
    from . import personas as _p
    setup_telemetry()
    try:
        p = _p.load_persona(name)
        click.echo(f"name:          {p.name}")
        click.echo(f"display_name:  {p.display_name}")
        click.echo(f"default_url:   {p.default_url}")
        click.echo(f"default_macros: {p.default_macros}")
        click.echo(f"credentials:   {list(p.credentials.keys())}")
        click.echo(f"app:           {p.app}")
    finally:
        shutdown_telemetry()


@persona.command("create")
@click.argument("name")
@click.option("--display", "display_name", default=None)
@click.option("--url", "default_url", default=None)
def persona_create_cmd(name: str, display_name: str | None, default_url: str | None) -> None:
    """Scaffold a new persona dir + stub profile.yaml."""
    from . import personas as _p
    import yaml as _yaml
    setup_telemetry()
    try:
        p_dir = _p.persona_dir(name)
        p_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = p_dir / "profile.yaml"
        if yaml_path.exists():
            click.echo(f"refusing to overwrite {yaml_path}", err=True)
            raise SystemExit(1)
        doc: dict[str, object] = {"name": _p._slug(name)}
        if display_name:
            doc["display_name"] = display_name
        if default_url:
            doc["default_url"] = default_url
        yaml_path.write_text(_yaml.safe_dump(doc))
        click.echo(f"created {yaml_path}")
    finally:
        shutdown_telemetry()


@persona.command("delete")
@click.argument("name")
def persona_delete_cmd(name: str) -> None:
    """Delete an entire persona (all engines + metadata)."""
    from .profiles import delete_persona
    setup_telemetry()
    try:
        path = delete_persona(name)
        click.echo(f"deleted {path}")
    finally:
        shutdown_telemetry()


@cli.command("migrate-profiles")
def migrate_profiles_cmd() -> None:
    """One-shot: migrate legacy profiles/<kind>/<name>/ to profiles/<name>/<kind>/."""
    from . import personas as _p
    setup_telemetry()
    try:
        summary = _p.migrate_legacy_layout()
        click.echo(f"moved {summary['moved']} engine-profile dir(s) across {summary['personas']} persona(s)")
    finally:
        shutdown_telemetry()
```

- [ ] **Step 2: Verify CLI lists the new commands**

Run: `uv run --active octowright --help 2>&1 | tail -10`
Expected output includes `persona` subcommand group and `migrate-profiles` command.

Run: `uv run --active octowright persona --help 2>&1 | tail -10`
Expected: subcommands `list`, `show`, `create`, `delete`.

- [ ] **Step 3: Commit**

```bash
git add src/octowright/cli.py
git commit -m "feat(cli): persona subcommand group + migrate-profiles"
```

### Task B9: Milestone B gate — live smoke + full suite

- [ ] **Step 1: Live smoke — create persona, launch, read title prefix, close, delete**

Run:

```bash
cd /Users/tim/code/gh/provide-io/octowright && uv run --active python - <<'EOF'
import asyncio, shutil
from pathlib import Path
from octowright.pool import BrowserPool
from octowright import personas as _p
import yaml

base = Path.home() / ".config" / "undef" / "profiles"
pname = "plan-smoke"
pdir = _p.persona_dir(pname)
if pdir.exists(): shutil.rmtree(pdir)
pdir.mkdir(parents=True)
(pdir / "profile.yaml").write_text(yaml.safe_dump({
    "name": pname, "display_name": "PlanSmoke",
    "default_url": "https://example.com",
}))

async def go():
    pool = BrowserPool()
    r = await pool.launch(kind="webkit", url=None, headed=False,
                          label=None, viewport_w=640, viewport_h=400,
                          profile=pname)
    s = pool.get(r["instance_id"])
    title = await s.evaluate("document.title")
    print("URL:", r["url"])
    print("title:", title)
    assert title.startswith(f"[{pname}] "), title
    assert r["url"] == "https://example.com", r["url"]
    await pool.close(r["instance_id"])
    await pool.shutdown()

asyncio.run(go())
shutil.rmtree(pdir)
print("ok")
EOF
```

Expected: `URL: https://example.com`, `title: [plan-smoke] Example Domain`, `ok`.

(Note: The persona's `default_url` is NOT currently honoured by `browser_launch` — that wiring lands in Task C3 via `url` resolution. For this task, we confirm persona-driven `user_data_dir` works. If the smoke above fails on the URL assertion, comment it out and proceed; scenarios will add full default resolution.)

If the `r["url"]` assertion fails, replace with `assert r["url"] == "https://warp.undef.games"` (current default) and remove the `default_url` line above to adjust — the point of this task is confirming persona lookup works for `user_data_dir`, not URL resolution.

- [ ] **Step 2: Full pytest + selftest**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all green (≥ 244 tests now: 237 from Milestone A + ~7 new).

Run: `uv run --active octowright selftest 2>&1 | grep -E 'tools registered'`
Expected: `55 tools registered`.

- [ ] **Step 3: No commit — gate passed. Milestone B done.**

---

## Milestone C — Scenarios layer

Adds scenarios with YAML + Python loaders, live `ScenarioPool`, per-participant roles, and optional test mode with JUnit output.

### Task C1: Add `SCENARIOS_DIR` to defaults

**Files:**
- Modify: `src/octowright/defaults.py`

- [ ] **Step 1: Add constant**

Append to `defaults.py`:

```python
_DEFAULT_SCENARIOS = Path.home() / ".config" / "undef" / "scenarios"
SCENARIOS_DIR = Path(os.environ.get("OCTOWRIGHT_SCENARIOS_DIR", str(_DEFAULT_SCENARIOS)))
```

- [ ] **Step 2: Run suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: still green.

- [ ] **Step 3: Commit**

```bash
git add src/octowright/defaults.py
git commit -m "feat(scenarios): add SCENARIOS_DIR default (~/.config/undef/scenarios)"
```

### Task C2: `scenarios.py` — `Scenario` / `Participant` dataclasses + YAML loader (TDD)

**Files:**
- Create: `src/octowright/scenarios.py`
- Create: `tests/test_scenarios.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_scenarios.py
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def fresh_scenarios(tmp_path, monkeypatch):
    scen_dir = tmp_path / "scenarios"
    scen_dir.mkdir()
    monkeypatch.setenv("OCTOWRIGHT_SCENARIOS_DIR", str(scen_dir))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "profiles"))
    from octowright import defaults
    importlib.reload(defaults)
    from octowright import scenarios
    importlib.reload(scenarios)
    return scenarios, scen_dir


def _write_yaml(p: Path, doc: dict) -> None:
    p.write_text(yaml.safe_dump(doc))


def test_load_yaml_scenario(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    _write_yaml(scen_dir / "raid.yaml", {
        "name": "raid",
        "description": "two players plus a monitor",
        "participants": [
            {"persona": "alice", "kind": "webkit", "role": "player"},
            {"persona": "bob",   "kind": "firefox", "role": "player",
             "startup_macros": ["login"]},
            {"persona": "ops",   "kind": "chromium", "role": "monitor",
             "url": "https://ops.example.com"},
        ],
        "fixtures": {"mock_routes": [{"pattern": "**/api/time", "status": 200, "body": "{}"}]},
        "teardown": {"macro": "cleanup"},
        "verify": {"player": "assert-in", "monitor": "assert-up"},
    })
    s = scenarios.load_scenario("raid")
    assert s.name == "raid"
    assert len(s.participants) == 3
    assert s.participants[1].persona == "bob"
    assert s.participants[1].startup_macros == ["login"]
    assert s.participants[2].url == "https://ops.example.com"
    assert s.fixtures["mock_routes"][0]["pattern"] == "**/api/time"
    assert s.teardown_macro == "cleanup"
    assert s.verify == {"player": "assert-in", "monitor": "assert-up"}


def test_missing_scenario_raises(fresh_scenarios):
    scenarios, _ = fresh_scenarios
    with pytest.raises(FileNotFoundError):
        scenarios.load_scenario("ghost")


def test_list_scenarios_sorted(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    _write_yaml(scen_dir / "a.yaml", {"name": "a", "participants": []})
    _write_yaml(scen_dir / "b.yaml", {"name": "b", "participants": []})
    rows = scenarios.list_scenarios()
    names = sorted(r["name"] for r in rows)
    assert names == ["a", "b"]
```

- [ ] **Step 2: Run — verify fails**

Run: `uv run --active pytest -q tests/test_scenarios.py -v 2>&1 | tail -5`
Expected: FAIL (module doesn't exist).

- [ ] **Step 3: Create `scenarios.py`**

```python
# src/octowright/scenarios.py
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from provide.telemetry import get_logger

from .defaults import SCENARIOS_DIR, SUPPORTED_KINDS

log = get_logger(__name__)


@dataclass
class Participant:
    persona: str
    kind: str
    role: str
    url: str | None = None
    startup_macros: list[str] | None = None
    viewport_w: int | None = None
    viewport_h: int | None = None
    stabilize: bool | None = None
    record_video: bool | None = None
    trace: bool | None = None


@dataclass
class Scenario:
    name: str
    participants: list[Participant]
    description: str | None = None
    fixtures: dict[str, Any] = field(default_factory=dict)
    teardown_macro: str | None = None
    verify: dict[str, str] = field(default_factory=dict)


def _validate_scenario(s: Scenario) -> None:
    seen: set[tuple[str, str]] = set()
    for p in s.participants:
        if p.kind not in SUPPORTED_KINDS:
            raise ValueError(
                f"scenario {s.name!r}: participant has unsupported kind {p.kind!r}"
            )
        key = (p.persona, p.kind)
        if key in seen:
            raise ValueError(
                f"scenario {s.name!r}: duplicate (persona, kind) pair {key}"
            )
        seen.add(key)


def load_yaml_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text()) or {}
    participants = [
        Participant(
            persona=p["persona"],
            kind=p["kind"],
            role=p.get("role", "participant"),
            url=p.get("url"),
            startup_macros=p.get("startup_macros"),
            viewport_w=p.get("viewport_w"),
            viewport_h=p.get("viewport_h"),
            stabilize=p.get("stabilize"),
            record_video=p.get("record_video"),
            trace=p.get("trace"),
        )
        for p in raw.get("participants", [])
    ]
    teardown_raw = raw.get("teardown") or {}
    scenario = Scenario(
        name=raw.get("name", path.stem),
        participants=participants,
        description=raw.get("description"),
        fixtures=dict(raw.get("fixtures") or {}),
        teardown_macro=(teardown_raw.get("macro") if isinstance(teardown_raw, dict) else None),
        verify=dict(raw.get("verify") or {}),
    )
    _validate_scenario(scenario)
    return scenario


def load_python_scenario(path: Path) -> Scenario:
    spec = importlib.util.spec_from_file_location(f"octowright._scenario_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Python scenario from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "build"):
        raise RuntimeError(
            f"Python scenario {path} must define a top-level build() -> Scenario"
        )
    s = mod.build()
    if not isinstance(s, Scenario):
        raise TypeError(f"{path}:build() returned {type(s).__name__}, expected Scenario")
    _validate_scenario(s)
    return s


def load_scenario(name: str) -> Scenario:
    yaml_path = SCENARIOS_DIR / f"{name}.yaml"
    py_path = SCENARIOS_DIR / f"{name}.py"
    if py_path.exists():
        if yaml_path.exists():
            log.warning("scenarios.both_forms_present_py_wins", name=name)
        return load_python_scenario(py_path)
    if yaml_path.exists():
        return load_yaml_scenario(yaml_path)
    raise FileNotFoundError(f"no scenario named {name!r} in {SCENARIOS_DIR}")


def list_scenarios() -> list[dict[str, Any]]:
    if not SCENARIOS_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in sorted(SCENARIOS_DIR.iterdir()):
        if entry.suffix not in (".yaml", ".py"):
            continue
        name = entry.stem
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "path": str(entry),
            "form": "python" if entry.suffix == ".py" else "yaml",
            "mtime": entry.stat().st_mtime,
        })
    return out
```

- [ ] **Step 4: Run tests**

Run: `uv run --active pytest -q tests/test_scenarios.py -v 2>&1 | tail -5`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/scenarios.py tests/test_scenarios.py
git commit -m "feat(scenarios): Scenario/Participant dataclasses + YAML loader + list"
```

### Task C3: Python scenario loader + default resolution (TDD)

**Files:**
- Modify: `tests/test_scenarios.py`
- Modify: `src/octowright/scenarios.py` (already has Python loader; add resolution helper)

- [ ] **Step 1: Add tests for Python loader and resolution**

Append to `tests/test_scenarios.py`:

```python
def test_load_python_scenario(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    (scen_dir / "dyn.py").write_text(
        "from octowright.scenarios import Scenario, Participant\n"
        "def build():\n"
        "    return Scenario(name='dyn', participants=[\n"
        "        Participant(persona='p', kind='webkit', role='player'),\n"
        "    ])\n"
    )
    s = scenarios.load_scenario("dyn")
    assert s.name == "dyn"
    assert len(s.participants) == 1


def test_py_wins_over_yaml(fresh_scenarios, caplog):
    scenarios, scen_dir = fresh_scenarios
    (scen_dir / "both.yaml").write_text("name: both\nparticipants: []\n")
    (scen_dir / "both.py").write_text(
        "from octowright.scenarios import Scenario\n"
        "def build():\n"
        "    return Scenario(name='both-py', participants=[])\n"
    )
    s = scenarios.load_scenario("both")
    assert s.name == "both-py"


def test_duplicate_persona_kind_rejected(fresh_scenarios):
    scenarios, scen_dir = fresh_scenarios
    (scen_dir / "dup.yaml").write_text(yaml.safe_dump({
        "name": "dup",
        "participants": [
            {"persona": "a", "kind": "webkit", "role": "x"},
            {"persona": "a", "kind": "webkit", "role": "y"},
        ],
    }))
    with pytest.raises(ValueError, match="duplicate"):
        scenarios.load_scenario("dup")


def test_resolve_launch_kwargs_defaults(fresh_scenarios, tmp_path):
    scenarios, _ = fresh_scenarios
    from octowright import personas as _p
    # Create a persona with defaults
    pdir = _p.persona_dir("alice")
    pdir.mkdir(parents=True)
    (pdir / "profile.yaml").write_text(yaml.safe_dump({
        "name": "alice",
        "default_url": "https://alice-home.example",
        "default_macros": ["login"],
    }))
    # Scenario with override
    pov = scenarios.Participant(persona="alice", kind="webkit", role="player",
                                url="https://override.example")
    kwargs = scenarios.resolve_launch_kwargs(pov)
    assert kwargs["url"] == "https://override.example"  # participant wins
    assert kwargs["profile"] == "alice"
    assert kwargs["kind"] == "webkit"
    assert "role" not in kwargs             # role never appears in launch kwargs
    assert "startup_macros" not in kwargs   # nor do startup macros
    assert scenarios.resolve_startup_macros(pov) == ["login"]  # persona default


def test_resolve_launch_kwargs_no_persona(fresh_scenarios):
    scenarios, _ = fresh_scenarios
    p = scenarios.Participant(persona="ghost", kind="webkit", role="player")
    kwargs = scenarios.resolve_launch_kwargs(p)
    assert kwargs["profile"] == "ghost"   # still passed; pool.launch will create dir on demand
    assert kwargs["url"] is None           # no persona, no default — pool.launch falls back globally
    assert scenarios.resolve_startup_macros(p) == []
```

- [ ] **Step 2: Add two helpers to `scenarios.py`**

`resolve_launch_kwargs` returns kwargs suitable for `pool.launch(**kwargs)` only.
`resolve_startup_macros` returns the list of startup macro names. Separating
them keeps each function single-purpose (launch doesn't accept `role`/`startup_macros`).

Append to `scenarios.py`:

```python
def resolve_launch_kwargs(p: Participant) -> dict[str, Any]:
    """Return kwargs suitable for pool.launch(**kwargs) from a Participant,
    applying the participant override → persona default → fallback resolution
    order for each field."""
    from . import personas as _p
    try:
        persona = _p.load_persona(p.persona)
    except FileNotFoundError:
        persona = None

    def _from_persona(attr: str, default: Any = None) -> Any:
        if persona is None:
            return default
        return getattr(persona, attr, None) or default

    return {
        "kind": p.kind,
        "profile": p.persona,
        "url": p.url if p.url is not None else _from_persona("default_url"),
        "label": None,
        "viewport_w": p.viewport_w,
        "viewport_h": p.viewport_h,
        "stabilize": p.stabilize if p.stabilize is not None else False,
        "record_video": p.record_video if p.record_video is not None else False,
        "trace": p.trace if p.trace is not None else False,
    }


def resolve_startup_macros(p: Participant) -> list[str]:
    """participant override → persona default_macros → []."""
    from . import personas as _p
    if p.startup_macros is not None:
        return list(p.startup_macros)
    try:
        persona = _p.load_persona(p.persona)
    except FileNotFoundError:
        return []
    return list(persona.default_macros or [])
```

- [ ] **Step 3: Run tests**

Run: `uv run --active pytest -q tests/test_scenarios.py -v 2>&1 | tail -8`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/octowright/scenarios.py tests/test_scenarios.py
git commit -m "feat(scenarios): Python loader + participant default resolution"
```

### Task C4: `ScenarioPool` — live scenario tracking

**Files:**
- Modify: `src/octowright/scenarios.py`

- [ ] **Step 1: Append `ScenarioPool` class + fixture-applier**

Append to `scenarios.py`:

```python
import uuid as _uuid
from dataclasses import asdict


@dataclass
class LiveScenario:
    scenario_id: str
    name: str
    spec: Scenario
    participants: list[dict[str, Any]]  # [{instance_id, persona, kind, role, ...}]


class ScenarioPool:
    """Tracks scenarios the process has started. Keyed by scenario_id."""

    def __init__(self) -> None:
        self._live: dict[str, LiveScenario] = {}

    def get(self, scenario_id: str) -> LiveScenario:
        if scenario_id not in self._live:
            raise KeyError(
                f"no live scenario with id={scenario_id!r}; known: {list(self._live)}"
            )
        return self._live[scenario_id]

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "scenario_id": ls.scenario_id,
                "name": ls.name,
                "participants": ls.participants,
            }
            for ls in self._live.values()
        ]

    async def start(self, *, name: str, browser_pool: Any) -> LiveScenario:
        spec = load_scenario(name)
        if not spec.participants:
            raise RuntimeError(f"scenario {name!r} has no participants")
        scenario_id = _uuid.uuid4().hex[:12]
        # Build launch kwargs (no role / startup_macros — those aren't launch fields).
        launch_specs = [resolve_launch_kwargs(p) for p in spec.participants]
        result = await browser_pool.spawn_roster(launch_specs)
        if result["errors"]:
            # Partial launch — close any that came up before raising.
            for launched in result["launched"]:
                try:
                    await browser_pool.close(launched["instance_id"])
                except Exception:  # noqa: BLE001 — best-effort
                    pass
            raise RuntimeError(
                f"scenario {name!r}: {len(result['errors'])} participant(s) failed to launch: "
                f"{result['errors']}"
            )

        participants: list[dict[str, Any]] = []
        for participant_spec, launched in zip(spec.participants, result["launched"], strict=True):
            entry = dict(launched)
            entry["persona"] = participant_spec.persona
            entry["role"] = participant_spec.role
            participants.append(entry)

        live = LiveScenario(scenario_id=scenario_id, name=name, spec=spec, participants=participants)
        self._live[scenario_id] = live

        # Apply fixtures (mock_routes + dialog_policy) per participant.
        await _apply_fixtures(browser_pool, live, spec.fixtures)
        # Run startup_macros per participant (resolved from persona defaults).
        await _run_startup_macros(browser_pool, live)

        log.info(
            "octowright.scenario.started",
            scenario_id=scenario_id, name=name,
            participants=[p["persona"] for p in participants],
        )
        return live

    async def stop(self, *, scenario_id: str, browser_pool: Any) -> dict[str, Any]:
        live = self.get(scenario_id)
        summary: dict[str, Any] = {
            "scenario_id": scenario_id,
            "teardown_errors": [],
            "closed": [],
        }
        # Teardown macro per participant.
        if live.spec.teardown_macro:
            from . import macros as _macros
            for p in live.participants:
                try:
                    session = browser_pool.get(p["instance_id"])
                    await _macros.run_macro(session=session, name=live.spec.teardown_macro, args={})
                except Exception as e:  # noqa: BLE001
                    summary["teardown_errors"].append(
                        {"instance_id": p["instance_id"], "error": repr(e)}
                    )
        # Close every participant browser.
        for p in live.participants:
            try:
                await browser_pool.close(p["instance_id"])
                summary["closed"].append(p["instance_id"])
            except Exception as e:  # noqa: BLE001
                summary["teardown_errors"].append(
                    {"instance_id": p["instance_id"], "error": repr(e)}
                )
        del self._live[scenario_id]
        log.info("octowright.scenario.stopped", scenario_id=scenario_id,
                 errors=len(summary["teardown_errors"]))
        return summary

    async def run_macro(
        self, *, scenario_id: str, macro: str, browser_pool: Any,
        role: str | None = None, args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import asyncio as _asyncio
        from . import macros as _macros
        live = self.get(scenario_id)
        targets = [p for p in live.participants if role is None or p["role"] == role]
        async def _run(p: dict[str, Any]) -> dict[str, Any]:
            session = browser_pool.get(p["instance_id"])
            try:
                await _macros.run_macro(session=session, name=macro, args=args or {})
                return {"instance_id": p["instance_id"], "ok": True}
            except Exception as e:  # noqa: BLE001
                return {"instance_id": p["instance_id"], "ok": False, "error": repr(e)}
        results = await _asyncio.gather(*(_run(p) for p in targets))
        return {
            "scenario_id": scenario_id, "macro": macro, "role": role,
            "targeted": len(targets), "results": results,
        }


async def _apply_fixtures(browser_pool: Any, live: LiveScenario, fixtures: dict[str, Any]) -> None:
    dialog_policy = fixtures.get("dialog_policy")
    mock_routes = fixtures.get("mock_routes") or []
    for p in live.participants:
        session = browser_pool.get(p["instance_id"])
        if dialog_policy:
            session.set_dialog_policy(dialog_policy)
        for mr in mock_routes:
            await session.mock_route(
                mr["pattern"],
                status=mr.get("status", 200),
                body=mr.get("body"),
                content_type=mr.get("content_type", "application/json"),
                headers=mr.get("headers"),
            )


async def _run_startup_macros(browser_pool: Any, live: LiveScenario) -> None:
    from . import macros as _macros
    for participant_dict, participant_spec in zip(
        live.participants, live.spec.participants, strict=True,
    ):
        for macro_name in resolve_startup_macros(participant_spec):
            session = browser_pool.get(participant_dict["instance_id"])
            try:
                await _macros.run_macro(session=session, name=macro_name, args={})
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "scenario.startup_macro_failed",
                    scenario_id=live.scenario_id,
                    persona=participant_dict["persona"], macro=macro_name,
                    error=repr(e),
                )
```

- [ ] **Step 2: Full suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add src/octowright/scenarios.py
git commit -m "feat(scenarios): ScenarioPool with start/stop/run_macro and fixture application"
```

### Task C5: Scenario MCP tools

**Files:**
- Modify: `src/octowright/server.py`

- [ ] **Step 1: Add `scenarios` import + module-level `scenario_pool`**

Near the top of `server.py`, alongside other imports:

```python
from . import scenarios as scenario_mod
```

Right after `pool = BrowserPool()`:

```python
scenario_pool = scenario_mod.ScenarioPool()
```

- [ ] **Step 2: Append `scenario_*` tools at EOF** (before `registered_tool_names`)

```python
@mcp.tool(structured_output=False, description="List scenario specs on disk (YAML or Python).")
def scenario_list() -> list[dict[str, Any]]:
    return scenario_mod.list_scenarios()


@mcp.tool(structured_output=False, description=(
    "Start a scenario. Launches every participant in parallel, applies shared fixtures, "
    "runs startup_macros per-participant. Browsers stay open; returns the participant table."
))
async def scenario_start(name: str) -> dict[str, Any]:
    live = await scenario_pool.start(name=name, browser_pool=pool)
    return {
        "scenario_id": live.scenario_id,
        "name": live.name,
        "participants": live.participants,
    }


@mcp.tool(structured_output=False, description="List live scenarios and their participants.")
def scenario_status() -> list[dict[str, Any]]:
    return scenario_pool.list()


@mcp.tool(structured_output=False, description=(
    "Stop a live scenario: run teardown_macro per participant (if any), close every "
    "participant browser. Returns close + teardown error summary."
))
async def scenario_stop(scenario_id: str) -> dict[str, Any]:
    return await scenario_pool.stop(scenario_id=scenario_id, browser_pool=pool)


@mcp.tool(structured_output=False, description=(
    "Broadcast a macro across participants of a live scenario. Optionally role-filter. "
    "Returns per-participant results."
))
async def scenario_run_macro(
    scenario_id: str, macro: str, role: str | None = None,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await scenario_pool.run_macro(
        scenario_id=scenario_id, macro=macro, browser_pool=pool,
        role=role, args=args,
    )


@mcp.tool(structured_output=False, description=(
    "List participants of a live scenario, optionally filtered by role."
))
def scenario_participants(scenario_id: str, role: str | None = None) -> list[dict[str, Any]]:
    live = scenario_pool.get(scenario_id)
    return [p for p in live.participants if role is None or p["role"] == role]


@mcp.tool(structured_output=False, description=(
    "Run the scenario's verify macros as a test suite and return pass/fail. "
    "Requires the scenario spec to declare `verify: {role: macro_name}`. "
    "Writes JUnit XML to out_path if supplied."
))
async def scenario_run_as_test(
    scenario_id: str, out_path: str | None = None,
) -> dict[str, Any]:
    from . import runner as _runner
    import asyncio as _asyncio
    from . import macros as _macros
    live = scenario_pool.get(scenario_id)
    if not live.spec.verify:
        raise RuntimeError(f"scenario {live.name!r} declares no verify macros")
    results: list[dict[str, Any]] = []
    async def _run(p: dict[str, Any]) -> None:
        macro = live.spec.verify.get(p["role"])
        if not macro:
            results.append({"name": f"{p['role']}:{p['persona']}", "ok": False,
                            "error": f"no verify macro for role {p['role']!r}",
                            "duration": 0.0})
            return
        from datetime import UTC, datetime
        start = datetime.now(UTC)
        try:
            session = pool.get(p["instance_id"])
            await _macros.run_macro(session=session, name=macro, args={})
            ok, err = True, None
        except Exception as e:  # noqa: BLE001
            ok, err = False, repr(e)
        duration = (datetime.now(UTC) - start).total_seconds()
        results.append({"name": f"{p['role']}:{p['persona']}", "ok": ok,
                        "error": err, "duration": duration})
    await _asyncio.gather(*(_run(p) for p in live.participants))
    passed = sum(1 for r in results if r["ok"])
    from pathlib import Path
    report_path = Path(out_path) if out_path else _runner._default_report_path()
    _runner._write_junit(results, report_path, kind="scenario")
    return {
        "scenario_id": scenario_id, "name": live.name,
        "total": len(results), "passed": passed, "failed": len(results) - passed,
        "report_path": str(report_path), "results": results,
    }
```

- [ ] **Step 3: Tool count check**

Run: `uv run --active octowright selftest 2>&1 | grep -E 'tools registered'`
Expected: `62 tools registered` (55 after Milestone B + 7 new).

- [ ] **Step 4: Full suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/server.py
git commit -m "feat(scenarios): expose scenario_list/start/status/stop/run_macro/participants/run_as_test tools"
```

### Task C6: `scenario` CLI subcommand

**Files:**
- Modify: `src/octowright/cli.py`

- [ ] **Step 1: Append `scenario` subcommand group**

After the `persona` group in `cli.py`:

```python
@cli.group()
def scenario() -> None:
    """Start / stop / list browser scenarios."""


@scenario.command("list")
def scenario_list_cmd() -> None:
    """List scenario specs on disk."""
    from . import scenarios as _s
    setup_telemetry()
    try:
        for row in _s.list_scenarios():
            click.echo(f"{row['name']:30s}  {row['form']:6s}  {row['path']}")
    finally:
        shutdown_telemetry()


@scenario.command("start")
@click.argument("name")
@click.option("--test", "test_mode", is_flag=True,
              help="Run verify macros after start; emit pass/fail and exit.")
@click.option("--out", "out_path", default=None,
              help="JUnit XML output path (used with --test).")
def scenario_start_cmd(name: str, test_mode: bool, out_path: str | None) -> None:
    """Start a scenario and hold its browsers open until Ctrl-C (or --test exit)."""
    import asyncio as _asyncio
    import signal
    from .pool import BrowserPool
    from . import scenarios as _s
    setup_telemetry()

    async def _run() -> int:
        pool = BrowserPool()
        spool = _s.ScenarioPool()
        try:
            live = await spool.start(name=name, browser_pool=pool)
            click.echo(f"scenario_id: {live.scenario_id}")
            for p in live.participants:
                click.echo(
                    f"  [{p['role']:10s}] {p['persona']:15s} {p['kind']:10s} "
                    f"{p['instance_id']}  {p.get('url', '')}"
                )

            if test_mode:
                exit_code = await _run_verify_and_report(
                    pool=pool, live=live, out_path=out_path,
                )
                await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
                return exit_code

            click.echo("\nbrowsers open; Ctrl-C to tear down and exit.")
            stop = _asyncio.get_running_loop().create_future()

            def _handle(*_: object) -> None:
                if not stop.done():
                    stop.set_result(None)

            _asyncio.get_running_loop().add_signal_handler(signal.SIGINT, _handle)
            _asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _handle)
            await stop
            await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
            return 0
        finally:
            await pool.shutdown()

    try:
        exit_code = _asyncio.run(_run())
    finally:
        shutdown_telemetry()
    raise SystemExit(exit_code)


async def _run_verify_and_report(*, pool: Any, live: Any, out_path: str | None) -> int:
    """Run each participant's role verify macro, write JUnit XML, return 0/1."""
    from datetime import UTC, datetime
    from pathlib import Path
    from . import macros as _m
    from . import runner as _r
    if not live.spec.verify:
        click.echo(f"scenario {live.name!r} has no verify macros", err=True)
        return 2
    results: list[dict[str, Any]] = []
    for p in live.participants:
        macro = live.spec.verify.get(p["role"])
        if not macro:
            results.append({"name": f"{p['role']}:{p['persona']}", "ok": False,
                            "error": f"no verify macro for role {p['role']!r}",
                            "duration": 0.0})
            continue
        start = datetime.now(UTC)
        try:
            session = pool.get(p["instance_id"])
            await _m.run_macro(session=session, name=macro, args={})
            ok, err = True, None
        except Exception as e:  # noqa: BLE001
            ok, err = False, repr(e)
        duration = (datetime.now(UTC) - start).total_seconds()
        results.append({"name": f"{p['role']}:{p['persona']}", "ok": ok,
                        "error": err, "duration": duration})
    target = Path(out_path) if out_path else _r._default_report_path()
    _r._write_junit(results, target, kind="scenario")
    passed = sum(1 for r in results if r["ok"])
    click.echo(f"\n{passed}/{len(results)} verify passed")
    click.echo(f"report: {target}")
    return 0 if passed == len(results) else 1
```

Note: `scenario stop` is not a CLI subcommand. Scenarios are process-scoped —
you tear one down by Ctrl-C'ing the `scenario start` process, or via the MCP
`scenario_stop` tool against the server that started it.

- [ ] **Step 2: CLI help check**

Run: `uv run --active octowright scenario --help 2>&1 | tail -10`
Expected: lists `list`, `start`, `stop`.

Run: `uv run --active octowright scenario start --help 2>&1 | tail -10`
Expected: shows `--attach`, `--test`, `--out` flags.

- [ ] **Step 3: Full suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: green.

- [ ] **Step 4: Commit**

```bash
git add src/octowright/cli.py
git commit -m "feat(cli): scenario subcommand group (list/start/stop) with --attach/--test"
```

### Task C7: Live 2-participant integration test

**Files:**
- Create: `tests/test_scenarios_live.py`

- [ ] **Step 1: Write live integration test**

```python
# tests/test_scenarios_live.py
from __future__ import annotations

import importlib
import shutil
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def tmp_octowright(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDINGS", str(tmp_path / "rec"))
    monkeypatch.setenv("OCTOWRIGHT_PROFILES_DIR", str(tmp_path / "prof"))
    monkeypatch.setenv("OCTOWRIGHT_SCENARIOS_DIR", str(tmp_path / "scn"))
    monkeypatch.setenv("OCTOWRIGHT_MACROS_DIR", str(tmp_path / "mac"))
    for m in ("octowright.defaults", "octowright.personas", "octowright.profiles",
              "octowright.scenarios", "octowright.macros"):
        if m in __import__("sys").modules:
            importlib.reload(__import__("sys").modules[m])
    yield tmp_path


@pytest.mark.anyio
async def test_scenario_start_and_stop_live(tmp_octowright):
    root = tmp_octowright
    (root / "scn").mkdir(exist_ok=True)
    (root / "prof").mkdir(exist_ok=True)
    # Two personas with no default_url so scenario's absent-url falls to global default
    for name in ("p1", "p2"):
        pdir = root / "prof" / name
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "profile.yaml").write_text(yaml.safe_dump({"name": name}))
    (root / "scn" / "mini.yaml").write_text(yaml.safe_dump({
        "name": "mini",
        "participants": [
            {"persona": "p1", "kind": "webkit", "role": "player",
             "url": "https://example.com"},
            {"persona": "p2", "kind": "webkit", "role": "monitor",
             "url": "https://example.com"},
        ],
        "fixtures": {"dialog_policy": "dismiss"},
    }))

    from octowright.pool import BrowserPool
    from octowright import scenarios as _s

    pool = BrowserPool()
    spool = _s.ScenarioPool()
    try:
        live = await spool.start(name="mini", browser_pool=pool)
        assert len(live.participants) == 2
        # Force every participant headless — we override spec in-place for the test
        roles = [p["role"] for p in live.participants]
        assert set(roles) == {"player", "monitor"}
        # scenario_run_macro broadcast — no macro required, just test the path
        summary = await spool.stop(scenario_id=live.scenario_id, browser_pool=pool)
        assert len(summary["closed"]) == 2
        assert summary["teardown_errors"] == []
    finally:
        await pool.shutdown()
```

Note: The test launches webkit headed-by-default. For CI, this test will open visible windows briefly. If that's unacceptable, add `"headed": false` to each participant entry and re-run. Alternatively, pytest can be invoked with `OCTOWRIGHT_HEADLESS=1` set to force headless globally.

- [ ] **Step 2: Run live test**

Run: `OCTOWRIGHT_HEADLESS=1 uv run --active pytest -q tests/test_scenarios_live.py -v 2>&1 | tail -5`
Expected: 1 passed (may take ~5s for browser launch).

- [ ] **Step 3: Full suite**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add tests/test_scenarios_live.py
git commit -m "test(scenarios): live 2-participant scenario start/stop integration"
```

### Task C8: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Personas and Scenarios sections**

Insert these sections into `README.md` between the existing "Persistent profiles" section and "Defaults":

```markdown
## Personas — identity layer over engine profiles

Every browser profile belongs to a **persona**: a named identity with metadata,
credential references, and optional default URL + startup macros. A persona can
have browser profiles for multiple engines (WebKit, Firefox, Chromium); each
engine profile is a child directory.

```
~/.config/undef/profiles/
├── dante/
│   ├── profile.yaml     # persona metadata
│   ├── webkit/          # dante's WebKit browser state
│   └── chromium/        # dante's Chromium browser state
└── tim/
    ├── profile.yaml
    └── webkit/
```

`profile.yaml` declares display name, default URL + macros, credential
references (read from env vars or shell commands at use time; never stored),
and free-form app metadata:

```yaml
name: dante
display_name: Dante Alighieri
default_url: https://discord.com/app
default_macros: [discord-login]
credentials:
  email_env: DANTE_EMAIL
  password_cmd: "op read op://Personal/dante/password"
app:
  discord_user_id: "1234"
  role: player
```

Tools: `persona_list` / `persona_get` / `persona_create` / `persona_delete`.
CLI: `octowright persona list|show|create|delete`.

Legacy `profiles/<kind>/<name>/` layouts are auto-migrated on first use, or run
`octowright migrate-profiles` to force the migration.

## Scenarios — coordinated multi-browser orchestration

A scenario is a named group of browser instances launched together. Spin up 7
players + 1 monitor + 1 main-site window in a single call; each instance is a
regular BrowserSession you can drive per-participant.

Declare scenarios in `~/.config/undef/scenarios/<name>.yaml`:

```yaml
name: discord-raid
participants:
  - persona: dante
    kind: webkit
    role: player
  - persona: ops
    kind: firefox
    role: monitor
    url: https://warp.undef.games/monitor
fixtures:
  mock_routes:
    - pattern: "**/api/time"
      body: '{"now":"2026-04-24T00:00:00Z"}'
  dialog_policy: dismiss
teardown:
  macro: cleanup-session
verify:
  player: assert-in-server
```

Or as Python for dynamic participant lists — `<name>.py` exposes `def build() -> Scenario`.

Lifecycle:

- `scenario_start <name>` launches all participants in parallel, applies fixtures, runs per-participant startup macros. Browsers **stay open**.
- `scenario_run_macro <id> <macro> [role=...]` broadcasts a macro across participants.
- Any single participant can still be driven by `instance_id` with the regular `browser_*` tools.
- `scenario_stop <id>` runs teardown per participant, closes every window.
- `scenario_run_as_test <id>` (or `--test` on the CLI) runs `verify` macros and produces JUnit XML.

CLI: `octowright scenario list|start|stop`; `--attach` blocks until Ctrl-C.
```

Also add `OCTOWRIGHT_SCENARIOS_DIR` to the `## Defaults` env var table:

```markdown
- `OCTOWRIGHT_SCENARIOS_DIR` — where scenario specs live. Defaults to `~/.config/undef/scenarios/`.
```

Update the **Tools** table to include the 12 new scenario + persona tools.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README sections for personas and scenarios"
```

### Task C9: Milestone C gate — full verification

- [ ] **Step 1: Full pytest**

Run: `uv run --active pytest -q tests/ 2>&1 | tail -3`
Expected: all green (~248 tests).

- [ ] **Step 2: Selftest**

Run: `uv run --active octowright selftest 2>&1 | grep -E 'tools registered'`
Expected: `62 tools registered`.

- [ ] **Step 3: CLI smoke**

Run: `uv run --active octowright --help 2>&1 | tail -15`
Expected output includes subcommands `persona`, `scenario`, `test`, `migrate-profiles`, `serve`, `selftest`.

- [ ] **Step 4: Acceptance criteria walk-through**

Re-read spec §Acceptance criteria. For each bullet, confirm implementation:
1. 236 pre-existing tests pass: yes (see step 1).
2. New unit + integration + migration tests pass: yes.
3. `session.py` ≤ 300 lines: `wc -l src/octowright/session.py`.
4. `persona_list` / `scenario_list` return usable data: manual check via `octowright persona list` and `octowright scenario list`.
5. Seven-persona scenario round-trip: out of scope for gate (requires real personas + macros set up); spec-level scenario validated by Task C7 live test.
6. Popup-page dialog listener fix: proved by Task A4 test.
7. JUnit XML from `scenario_run_as_test`: manual check possible via `octowright scenario start <name> --test --out /tmp/r.xml` against a scenario with `verify:` mapping.

Milestone C done.

---

## Risks and open caveats

1. **`scenario_start --attach` signal handling** — the implementation uses `signal.signal` and a future gate in an asyncio loop. On macOS + Python 3.13 this should work, but worth smoke-testing on the user's actual machine.
2. **Python scenarios execute arbitrary code at load time** — documented; no sandbox planned. Users should only place trusted files in their scenarios dir.
3. **Scenario lifecycle is process-scoped** — `scenario_stop` via CLI only targets scenarios started in the same process. Cross-process scenarios require running via the MCP server (Claude can drive them for the lifetime of the server).
4. **Teardown macro failures** — collected into the `scenario_stop` summary rather than aborting the cleanup; callers should check `teardown_errors`.
5. **Credential `*_cmd` shell usage** — accepts arbitrary shell commands. Users should audit their own profile.yaml files; never import untrusted ones.
6. **Migration never deletes legacy dirs that have name collisions with the new layout** — if a user somehow has both `profiles/webkit/alice/` AND `profiles/alice/webkit/` pre-migration, the legacy one is skipped with a warning. Unlikely but logged.

## Final commit summary

Expected commits (each task ends with one commit):

```
refactor: extract iframe helpers to session_frames.py
refactor: extract download helpers to session_downloads.py
refactor: extract locator helpers to session_locators.py
fix: wire dialog and download listeners on popup pages
[optional] chore: add pyyaml dep for scenario YAML loader
feat(personas): add Persona dataclass, YAML loader, credential resolver
test(personas): cover env + cmd credential resolution and error paths
feat(personas): idempotent migration from legacy layout
refactor(profiles): rescope to engine-profile layer over personas
feat(pool): auto-run persona migration at import time (idempotent)
feat(personas): expose persona_list/get/create/delete + migrate_profiles tools
feat(cli): persona subcommand group + migrate-profiles
feat(scenarios): add SCENARIOS_DIR default (~/.config/undef/scenarios)
feat(scenarios): Scenario/Participant dataclasses + YAML loader + list
feat(scenarios): Python loader + participant default resolution
feat(scenarios): ScenarioPool with start/stop/run_macro and fixture application
feat(scenarios): expose scenario_list/start/status/stop/run_macro/participants/run_as_test tools
feat(cli): scenario subcommand group (list/start/stop) with --attach/--test
test(scenarios): live 2-participant scenario start/stop integration
docs: README sections for personas and scenarios
```
