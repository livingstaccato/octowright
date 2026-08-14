# Terminal Sessions — Phase 2: Agent Surface (MCP tools + SSH) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Expose the Phase 1 terminal primitive to agents as `terminal_*` MCP tools (PTY + SSH), registered only when the optional `octowright[terminal]` extra is installed, and surface terminal sessions in the dashboard session list.

**Architecture:** Tools live in `server/terminal/lifecycle.py` and register via import side-effects in `server/__init__.py`, **conditionally imported only when `_state.terminal_pool is not None`** — so a core install never imports uterm and the tools simply don't appear (same model as a profile-filtered tool). A new `terminals` capability profile gates the LLM-visible surface. The session-list route merges `terminal_pool` (its summaries already fit `_live_summary`'s defensive shape). SSH is the same engine path as PTY with a different `connector_type` + config.

**Tech stack:** Python 3.11+, FastMCP `@mcp.tool`, Starlette routes, `provide-uterm` SSH connector (`asyncssh`). Builds on Phase 1 (`octowright/terminal/`).

**Depends on:** Phase 1 (committed on `feat/terminal-sessions`). **Pre-merge gate still open:** the GPLv3 §7 linking exception in `provide-uterm`.

**Conventions:** identical to the Phase 1 plan header — SPDX header with blank line after the `#` block; `from __future__ import annotations`; run everything via `uv run --active`; per-task pytest with `--no-cov`; no pyproject changes (extra still deferred to publish time); conventional-commit subjects that **start lowercase** (commitlint rejects PascalCase/Start-case subjects); never mention AI in commits. The uterm packages are editable-installed (re-run the Phase 1 header command if `uv sync` pruned them). For SSH: confirm `asyncssh` is importable (`uv run --active python -c "import asyncssh"`); if absent, `uv pip install -e ../provide-uterm/packages/provide-uterm-client` or add asyncssh to the editable set.

---

## Task 1: `terminal_*` MCP tools + `terminals` profile + conditional registration

**Files:**
- Create: `src/octowright/server/terminal/__init__.py`
- Create: `src/octowright/server/terminal/lifecycle.py`
- Modify: `src/octowright/server/profiles.py` (add `terminals` profile)
- Modify: `src/octowright/server/__init__.py` (conditional registration)
- Test: `tests/terminal/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_mcp_tools.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_terminal_tool_lifecycle() -> None:
    from octowright.server.terminal import lifecycle

    launched = await lifecycle.terminal_launch(kind="pty", command="/bin/cat", label="t")
    iid = launched["instance_id"]
    try:
        assert launched["kind"] == "terminal"
        listed = await lifecycle.terminal_list()
        assert any(s["instance_id"] == iid for s in listed)

        await lifecycle.terminal_send_input(instance_id=iid, text="hi-tools\n")
        waited = await lifecycle.terminal_wait_for(instance_id=iid, text="hi-tools", timeout=5.0)
        assert waited["matched"] is True
        snap = await lifecycle.terminal_snapshot(instance_id=iid)
        assert "hi-tools" in snap["screen"]
    finally:
        closed = await lifecycle.terminal_close(instance_id=iid)
        assert closed["closed"] is True


async def test_terminal_close_refuses_protected_without_force() -> None:
    from octowright.server.terminal import lifecycle

    launched = await lifecycle.terminal_launch(kind="pty", command="/bin/cat", protected=True)
    iid = launched["instance_id"]
    try:
        result = await lifecycle.terminal_close(instance_id=iid)
        assert result["closed"] is False
        assert "protected" in result["reason"]
    finally:
        await lifecycle.terminal_close(instance_id=iid, force=True)


def test_terminals_profile_registered() -> None:
    from octowright.server.profiles import PROFILES

    assert "terminal_launch" in PROFILES["terminals"]
    assert "terminal_close" in PROFILES["terminals"]
```

- [ ] **Step 2: Run, expect FAIL** — `uv run --active pytest tests/terminal/test_mcp_tools.py --no-cov -v` → `ModuleNotFoundError: octowright.server.terminal`.

- [ ] **Step 3: Create `src/octowright/server/terminal/__init__.py`** (export-surface only — no logic, per the `__init__` convention test):

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP tool surface for terminal sessions (registered only when available)."""

from __future__ import annotations

from octowright.server.terminal.lifecycle import (
    terminal_close,
    terminal_launch,
    terminal_list,
    terminal_read,
    terminal_send_input,
    terminal_snapshot,
    terminal_wait_for,
)

__all__ = [
    "terminal_close",
    "terminal_launch",
    "terminal_list",
    "terminal_read",
    "terminal_send_input",
    "terminal_snapshot",
    "terminal_wait_for",
]
```

- [ ] **Step 4: Create `src/octowright/server/terminal/lifecycle.py`.** The pool is `terminal_pool` from `_state` (non-None here — this module is imported only when available). `_pool()` narrows the `TerminalPool | None` for the type checker. Mirrors `server/browser/lifecycle.py`'s `@mcp.tool` + `publish_dashboard_invalidation_nowait` pattern.

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""@mcp.tool surface for driving terminal sessions (PTY / SSH)."""

from __future__ import annotations

from typing import Any

from octowright.dashboard_events import publish_dashboard_invalidation_nowait
from octowright.server._state import mcp, terminal_pool
from octowright.terminal.errors import ProtectedTerminalCloseError
from octowright.terminal.pool import TerminalPool


def _pool() -> TerminalPool:
    # This module is imported only when _state.terminal_pool is not None
    # (server/__init__ gates it on terminal availability), so this never raises.
    assert terminal_pool is not None, "terminal tools imported without an available terminal_pool"
    return terminal_pool


def _connector_config(
    *,
    command: str | None,
    host: str | None,
    port: int,
    user: str | None,
    key_path: str | None,
    password: str | None,
    known_hosts: str | None,
    insecure_no_host_check: bool,
    cols: int,
    rows: int,
) -> dict[str, Any]:
    """Build the uterm connector_config from explicit launch args (Task 2 fills SSH)."""
    cfg: dict[str, Any] = {"cols": cols, "rows": rows}
    if command is not None:
        cfg["command"] = command
    return cfg


@mcp.tool(
    structured_output=False,
    description=(
        "Launch a terminal session and start recording. kind='pty' runs a local "
        "shell (command=, default /bin/bash); kind='ssh' connects to a remote host "
        "(host/user/key_path/known_hosts). Returns instance_id for the other terminal_* tools."
    ),
)
async def terminal_launch(
    kind: str = "pty",
    command: str | None = None,
    host: str | None = None,
    port: int = 22,
    user: str | None = None,
    key_path: str | None = None,
    password: str | None = None,
    known_hosts: str | None = None,
    insecure_no_host_check: bool = False,
    cols: int = 80,
    rows: int = 24,
    label: str | None = None,
    profile: str | None = None,
    protected: bool = False,
) -> dict[str, Any]:
    if kind == "pty" and command is None:
        command = "/bin/bash"
    cfg = _connector_config(
        command=command,
        host=host,
        port=port,
        user=user,
        key_path=key_path,
        password=password,
        known_hosts=known_hosts,
        insecure_no_host_check=insecure_no_host_check,
        cols=cols,
        rows=rows,
    )
    result = await _pool().launch(kind=kind, connector_config=cfg, label=label, profile=profile, protected=protected)
    publish_dashboard_invalidation_nowait("sessions")
    return result


@mcp.tool(structured_output=False, description="Send input text (e.g. a command + '\\n') to a terminal session.")
async def terminal_send_input(instance_id: str, text: str, password: bool = False) -> dict[str, Any]:
    session = _pool().get(instance_id)
    await session.engine.send_input(text, password=password)
    return {"ok": True, "event_count": session.recorder.event_count}


@mcp.tool(structured_output=False, description="Return the current screen text + cursor of a terminal session.")
async def terminal_snapshot(instance_id: str) -> dict[str, Any]:
    return await _pool().get(instance_id).engine.snapshot()


@mcp.tool(
    structured_output=False,
    description="Return the current screen text of a terminal session (alias of snapshot's screen).",
)
async def terminal_read(instance_id: str) -> dict[str, Any]:
    snap = await _pool().get(instance_id).engine.snapshot()
    return {"screen": snap["screen"]}


@mcp.tool(
    structured_output=False,
    description="Wait until a regex (prompt=) or substring (text=) appears on the terminal screen, or timeout.",
)
async def terminal_wait_for(
    instance_id: str, prompt: str | None = None, text: str | None = None, timeout: float = 10.0
) -> dict[str, Any]:
    matched = await _pool().get(instance_id).engine.wait_for(prompt=prompt, text=text, timeout=timeout)
    snap = await _pool().get(instance_id).engine.snapshot()
    return {"matched": matched, "screen": snap["screen"]}


@mcp.tool(
    structured_output=False, description="Close a terminal session. Refuses a protected session unless force=True."
)
async def terminal_close(instance_id: str, force: bool = False) -> dict[str, Any]:
    try:
        await _pool().close(instance_id, force=force)
    except ProtectedTerminalCloseError as exc:
        return {"closed": False, "reason": str(exc)}
    publish_dashboard_invalidation_nowait("sessions")
    return {"closed": True}


@mcp.tool(structured_output=False, description="List live terminal sessions.")
async def terminal_list() -> list[dict[str, Any]]:
    return _pool().list_sessions()
```

- [ ] **Step 5: Add the `terminals` profile** in `src/octowright/server/profiles.py` — a new key in `PROFILES`:

```python
    # Terminal sessions (PTY / SSH). Only register when the optional
    # `octowright[terminal]` extra is installed (see server/__init__).
    "terminals": [
        "terminal_launch",
        "terminal_send_input",
        "terminal_snapshot",
        "terminal_read",
        "terminal_wait_for",
        "terminal_close",
        "terminal_list",
    ],
```

- [ ] **Step 6: Conditionally register in `src/octowright/server/__init__.py`.** After the existing submodule imports (after line 27, the `scenarios` import) and the `_state` import, add:

```python
# Terminal tools are optional: register them only when the uterm-backed
# `octowright[terminal]` extra is installed (terminal_pool is then non-None).
# Importing the module triggers @mcp.tool registration via decorator side effects.
from octowright.server._state import terminal_pool as _terminal_pool

if _terminal_pool is not None:
    from octowright.server import terminal as _terminal_tools  # noqa: F401
```

(Do not add the terminal tools to the top-level `__all__` re-export block — they don't exist on a core install, and a missing name there would break `from octowright import server`. Tests import them via `octowright.server.terminal.lifecycle` directly.)

- [ ] **Step 7: Run + quality** — `uv run --active pytest tests/terminal/test_mcp_tools.py --no-cov -v` (PASS), then `uv run --active ruff check --fix src/octowright/server/terminal && uv run --active ruff format src/octowright/server/terminal && uv run --active mypy src/octowright/server/terminal && uv run --active ty check src/octowright/server/terminal`.

- [ ] **Step 8: Commit**

```bash
git add src/octowright/server/terminal/ src/octowright/server/profiles.py src/octowright/server/__init__.py tests/terminal/test_mcp_tools.py
git commit -m "feat(terminal): add terminal_* MCP tools + terminals profile (optional)"
```

---

## Task 2: SSH connector args in `terminal_launch`

**Files:**
- Modify: `src/octowright/server/terminal/lifecycle.py` (`_connector_config` SSH branch)
- Test: `tests/terminal/test_ssh_args.py`

**Pre-req:** `uv run --active python -c "import asyncssh"` must succeed (editable-install `provide-uterm-client` if not). The uterm `SshSessionConnector` config keys are `host, port, username, password, client_key_path, known_hosts, insecure_no_host_check, input_mode`; it **raises `ValueError` without `known_hosts` unless `insecure_no_host_check=true`**.

- [ ] **Step 1: Write the failing test** — verify `_connector_config` maps SSH args to the connector's exact keys, and that the host-key requirement surfaces as a clean tool error.

```python
# tests/terminal/test_ssh_args.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.server.terminal import lifecycle


def test_ssh_connector_config_maps_to_uterm_keys() -> None:
    cfg = lifecycle._ssh_connector_config(
        host="h",
        port=2222,
        user="me",
        key_path="/k",
        password=None,
        known_hosts="/kh",
        insecure_no_host_check=False,
        cols=80,
        rows=24,
    )
    assert cfg["host"] == "h"
    assert cfg["port"] == 2222
    assert cfg["username"] == "me"
    assert cfg["client_key_path"] == "/k"
    assert cfg["known_hosts"] == "/kh"
    assert "command" not in cfg  # SSH config must not carry a PTY 'command'


async def test_ssh_launch_without_known_hosts_returns_clean_error() -> None:
    result = await lifecycle.terminal_launch(kind="ssh", host="h", user="me")
    assert result.get("ok") is False
    assert "known_hosts" in result["error"]
```

- [ ] **Step 2: Run, expect FAIL** (no `_ssh_connector_config`; launch raises instead of returning a clean error).

- [ ] **Step 3: Implement.** Split `_connector_config` into PTY vs SSH and wrap `launch` to convert the connector's `ValueError` into a tool error. In `lifecycle.py`:

```python
def _ssh_connector_config(
    *,
    host: str | None,
    port: int,
    user: str | None,
    key_path: str | None,
    password: str | None,
    known_hosts: str | None,
    insecure_no_host_check: bool,
    cols: int,
    rows: int,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {"cols": cols, "rows": rows, "port": port}
    if host is not None:
        cfg["host"] = host
    if user is not None:
        cfg["username"] = user
    if key_path is not None:
        cfg["client_key_path"] = key_path
    if password is not None:
        cfg["password"] = password
    if known_hosts is not None:
        cfg["known_hosts"] = known_hosts
    if insecure_no_host_check:
        cfg["insecure_no_host_check"] = True
    return cfg
```

Then route by `kind` in `terminal_launch`, and convert the connector's `ValueError` (raised in `TerminalEngine.__init__` → `build_connector`) into a clean error dict:

```python
if kind == "ssh":
    cfg = _ssh_connector_config(
        host=host,
        port=port,
        user=user,
        key_path=key_path,
        password=password,
        known_hosts=known_hosts,
        insecure_no_host_check=insecure_no_host_check,
        cols=cols,
        rows=rows,
    )
else:
    if command is None:
        command = "/bin/bash"
    cfg = {"cols": cols, "rows": rows, "command": command}
try:
    result = await _pool().launch(kind=kind, connector_config=cfg, label=label, profile=profile, protected=protected)
except ValueError as exc:
    return {"ok": False, "error": str(exc)}
publish_dashboard_invalidation_nowait("sessions")
return result
```

(Note: `TerminalEngine.__init__` calls `build_connector`, which constructs the connector and raises `ValueError` for a missing `known_hosts` before any network I/O — so the error is synchronous and catchable here. Add `asyncssh` to the `[terminal]` extra in the publish-time follow-up.)

- [ ] **Step 4: Run PASS, ruff/mypy/ty, commit** `feat(terminal): map ssh launch args to the uterm ssh connector`.

---

## Task 3: Session-list visibility (dashboard) + detail guard

**Files:**
- Modify: `src/octowright/http/routes/sessions.py` (`list_sessions`; guard the detail builder)
- Test: `tests/terminal/test_dashboard_sessions.py`

`_live_summary` is already defensive (getattr defaults) so terminal sessions serialize cleanly in the **list**. The **detail** builder (`_build_live_session_detail`) reads `live.console`/`.downloads`/`.pages`/`.video_path` directly and must be guarded for `kind == "terminal"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/terminal/test_dashboard_sessions.py
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="PTY is POSIX-only")


async def test_list_sessions_includes_terminals() -> None:
    import octowright.http.state as state
    from octowright.http.routes.sessions import list_sessions
    from starlette.requests import Request

    launched = await state.terminal_pool.launch(kind="pty", connector_config={"command": "/bin/cat"}, label="t")
    iid = launched["instance_id"]
    try:
        req = Request({"type": "http", "method": "GET", "headers": [], "query_string": b""})
        resp = await list_sessions(req)
        import json

        body = json.loads(resp.body)
        assert any(s["id"] == iid and s["kind"] == "terminal" for s in body["live"])
    finally:
        await state.terminal_pool.close(iid, force=True)
```

- [ ] **Step 2: Run, expect FAIL** (terminals absent from the list).

- [ ] **Step 3: Implement.** In `sessions.py`, merge the terminal pool (guarded on availability). `http/state.py` must expose `terminal_pool` (re-export from `_state`). Update `list_sessions`:

```python
async def list_sessions(_request: Request) -> JSONResponse:
    pool = state.pool
    live = [_live_summary(s) for s in pool.iter_sessions()]
    terminal_pool = getattr(state, "terminal_pool", None)
    if terminal_pool is not None:
        live += [_live_summary(s) for s in terminal_pool.iter_sessions()]
    live_paths = {s["log_path"] for s in live}
    closed = _closed_sessions(state.RECORDINGS_DIR, live_paths)
    return JSONResponse({"live": live, "closed": closed})
```

Add to `http/state.py`: `from octowright.server._state import terminal_pool` (re-export). In the detail endpoint, short-circuit for terminal sessions before the browser-only `_build_live_session_detail` (return the `_live_summary` plus terminal-relevant fields only). Verify `octowright.http.state` exposes `terminal_pool`.

- [ ] **Step 4: Run PASS, ruff/mypy/ty, commit** `feat(terminal): surface terminal sessions in the dashboard session list`.

---

## Task 4: Telemetry spans + metrics

**Files:**
- Modify: `src/octowright/terminal/engine.py` (wrap launch/send/close in spans; counters)
- Test: `tests/terminal/test_telemetry.py` (assert the noop path doesn't error; spans emitted when enabled)

Follow the existing `octowright._tracing` `span()` / `counter()` helpers (see CLAUDE.md telemetry section). Add spans `octowright.terminal.launch` / `.send_input` / `.close` and counters `octowright_terminal_launched_total` / `_closed_total` (label `connector_type`). Keep it noop-safe (default off).

- [ ] Steps: write test → fail → wrap engine methods in `with span(...)` + `counter(...).add(...)` → pass → commit `feat(terminal): add terminal OTel spans and counters`.

(Read `src/octowright/_tracing.py` for the exact `span`/`counter` signatures before writing — do not guess.)

---

## Task 5 (carry-over from Phase 1 review): robust EOF / child-exit detection

**Files:**
- Modify: `src/octowright/terminal/engine.py`
- Test: `tests/terminal/test_engine.py` (tighten the `{"eof","closed"}` assertion to `"eof"` once cross-platform)

The connector's `is_connected()` doesn't flip on macOS child-exit (PTY read returns `b""`, not `EIO`). Add a child-liveness check so `terminal_stop("eof")` fires cross-platform. Options to evaluate at implementation time (read `provide-uterm-platform/.../pty/connector.py` first): (a) detect a run of consecutive empty polls while the underlying child has exited; (b) propose a small upstream uterm change exposing child-exit. Until resolved, Phase 2 external-close eviction must not assume EOF on macOS. **If this needs an upstream uterm change, STOP and surface it** rather than reaching into connector internals from Octowright.

- [ ] Steps: write the cross-platform EOF test → implement the chosen approach → pass on both platforms → commit `fix(terminal): detect child-exit EOF cross-platform`.

---

## Final review

After all tasks: dispatch a code-quality reviewer over the Phase 2 diff, run the full `make test` (EXIT 0 + coverage), and confirm a core install (uterm absent) still imports `octowright.server` cleanly with no terminal tools registered.
