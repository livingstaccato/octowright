# Session-Kind Plugins Step 5: Extract Terminal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move terminal support out of octowright core into a separate `octowright-terminal` package that reaches core only through the session-kind plugin API, so core stops knowing a terminal exists.

**Architecture:** Build the plugin first against the contract steps 1–4 landed, prove it passes the same contract suite the in-repo reference plugin does, and only then delete terminal from core. The plugin lives in-repo at `packages/octowright-terminal/` (a second workspace member beside `packages/octowright-frontend/`), declares an `octowright.session_kinds` entry point, and is enabled by name through `OCTOWRIGHT_PLUGINS` like any third-party plugin. Nothing about it is privileged.

**Tech Stack:** Python 3.11+, `provide-uterm` 0.5.x (local editable sibling checkout), pytest, uv workspace, TypeScript + vitest + `@xterm/xterm` for the renderer.

**Spec:** `docs/superpowers/specs/2026-08-22-session-kind-plugins-design.md` — §10 (the uterm caveat), §11 (testing obligations), §12 step 5 (build order).

## Global Constraints

- **Delete nothing from core until the external plugin passes the contract suite.** Spec §12: "if the contract is inadequate, it is discovered while the working implementation is still in the tree." Tasks 1–8 add; Tasks 9–12 remove. Do not reorder.
- **Extraction does not make terminal installable.** Spec §10: `provide-uterm` and `provide-uterm-server` both 404 from PyPI. `octowright-terminal` is exactly as uninstallable from PyPI as `octowright[terminal]` is today. What changes is that the blocker leaves octowright's release path. Do not write any doc claiming otherwise.
- **Core must never import uterm.** Every uterm import stays inside `packages/octowright-terminal/`. A test asserts this.
- **Nothing loads by default.** Installing the package makes the kind *discoverable*; an operator enables it by name via `OCTOWRIGHT_PLUGINS=terminal`.
- File LOC cap: **777** (`scripts/check_max_loc.py`, `src/**/*.py` only).
- SPDX headers required on new Python files; `.ci/vulture-baseline.json` is deliberately empty, so any unused symbol fails the gate.
- Ruff: `select = ["E","F","I","UP","B","SIM","ARG","RUF","TID"]`, line length 120. mypy strict over `src/octowright`.
- Commits GPG-signed; never `--no-gpg-sign` / `--no-verify`. No AI-assistance mention, no `Co-Authored-By` trailer.
- `pyproject.toml` bakes `-q` into pytest `addopts`. **Never pass another `-q`** — `-qq` suppresses the summary line.
- `uv.lock` and `.octowright/config.yaml` show modified after ordinary test runs. Known churn: never stage them; `UV_FROZEN=1` is the standing remedy when pre-commit trips.

## Environment note (already done, do not redo)

The sibling uterm checkout is installed editable into the active venv:
`provide-uterm`, `provide-uterm-client`, `provide-uterm-platform`, `provide-uterm-server` at 0.5.1, plus `asyncssh`. `octowright.terminal.is_available()` returns `True` and `tests/terminal/` runs 62/62 green. Use `uv run --active --no-sync` so a sync does not evict those editable installs.

## File Structure

**New package — `packages/octowright-terminal/`:**

| Path | Responsibility |
|---|---|
| `pyproject.toml` | Package metadata, uterm deps **including the ssh extra**, the `octowright.session_kinds` entry point |
| `src/octowright_terminal/__init__.py` | Exports `plugin`; no uterm import at module scope |
| `src/octowright_terminal/plugin.py` | `TerminalPlugin` descriptor — the entry point target |
| `src/octowright_terminal/pool.py` | `TerminalPool`, conformed to `SessionPool` and routed through `ctx.begin_session` |
| `src/octowright_terminal/session.py` | `TerminalSession` (relocated) |
| `src/octowright_terminal/engine.py` | Poll loop (relocated) |
| `src/octowright_terminal/translate.py`, `redact.py`, `errors.py`, `supervision.py`, `connector_config.py`, `availability.py` | Relocated unchanged |
| `src/octowright_terminal/tools.py` | The 7 `terminal_*` MCP tools (relocated from `server/terminal/lifecycle.py`) |
| `src/octowright_terminal/scenario.py` | `TerminalScenarioAdapter` + the participant validation/resolution moved out of core `scenarios.py` |
| `src/octowright_terminal/assets/renderer.js` | `mountStream` wrapping xterm |
| `tests/` | The 62 relocated tests plus the contract-parity suite |

**Core files that lose terminal (Tasks 9–12):** `scenario_kinds.py`, `scenarios.py`, `scenarios_pool.py`, `server/scenarios.py`, `server/_state.py`, `server/_optional_tools.py`, `http/state.py`, `http/routes/sessions.py`, `http/discovery.py`, `cli/serve.py`, `cli/scenario.py`, `defaults.py`, plus frontend `types.ts`, `session.ts`, and the deletion of `session-terminal.ts` / `terminal-view.ts`.

---

### Task 1: The package skeleton and its entry point

Creates the workspace member and proves core can *discover* it before it does anything.

**Files:**
- Create: `packages/octowright-terminal/pyproject.toml`
- Create: `packages/octowright-terminal/src/octowright_terminal/__init__.py`
- Create: `packages/octowright-terminal/src/octowright_terminal/plugin.py`
- Modify: `pyproject.toml` (root — add the workspace member and a dev dependency)
- Test: `packages/octowright-terminal/tests/test_entry_point.py`

**Interfaces:**
- Produces: `octowright_terminal.plugin` — a module-level `TerminalPlugin()` instance named `plugin`, resolved by the `octowright.session_kinds` entry point named `terminal`.

- [ ] **Step 1: Write the failing test**

Create `packages/octowright-terminal/tests/test_entry_point.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The entry point is the whole interface between core and this package.

Core finds the plugin by name in the ``octowright.session_kinds`` group and
never imports ``octowright_terminal`` directly. If this test fails, the package
is invisible no matter how correct everything inside it is.
"""

from __future__ import annotations

from importlib.metadata import entry_points

from octowright.plugins.contract import PLUGIN_API_VERSION


def test_the_entry_point_is_discoverable_by_name():
    eps = [e for e in entry_points(group="octowright.session_kinds") if e.name == "terminal"]
    assert len(eps) == 1, "expected exactly one 'terminal' entry point in octowright.session_kinds"


def test_the_entry_point_resolves_to_a_descriptor_core_accepts():
    (ep,) = [e for e in entry_points(group="octowright.session_kinds") if e.name == "terminal"]
    descriptor = ep.load()
    assert descriptor.kind == "terminal"
    assert descriptor.plugin_api_version == PLUGIN_API_VERSION
    assert descriptor.display_name
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_entry_point.py -v --no-cov`
Expected: FAIL — no such entry point (the assert on `len(eps) == 1` sees `0`).

- [ ] **Step 3: Write the package metadata**

Create `packages/octowright-terminal/pyproject.toml`. Read the root `pyproject.toml` first and match its `[build-system]`, license and Python floor rather than inventing them.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "octowright-terminal"
version = "0.1.0"
description = "Terminal session kind for octowright (PTY, SSH, telnet), as a session-kind plugin."
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = [
    "octowright",
    "provide-uterm>=0.4.0",
    "provide-uterm-platform>=0.4.0",
    # [ssh] not [gateway]: the SSH connector imports asyncssh at module scope and
    # raises ImportError without it. Core's `terminal` extra asked for [gateway]
    # and therefore shipped an SSH connector that could not be constructed --
    # a latent runtime failure this package must not inherit.
    "provide-uterm-server[ssh,gateway]>=0.4.0",
]

# The only interface between core and this package. Core resolves the name from
# OCTOWRIGHT_PLUGINS and loads this attribute; nothing else is imported.
[project.entry-points."octowright.session_kinds"]
terminal = "octowright_terminal.plugin:plugin"

[tool.hatch.build.targets.wheel]
packages = ["src/octowright_terminal"]
```

- [ ] **Step 4: Write the package init and a minimal descriptor**

Create `packages/octowright-terminal/src/octowright_terminal/__init__.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal session kind for octowright, as a session-kind plugin.

Deliberately imports nothing at module scope: core resolves
``octowright_terminal.plugin:plugin`` from an entry point, and the uterm import
must not happen until a pool is actually built.
"""
```

Create `packages/octowright-terminal/src/octowright_terminal/plugin.py`. This is the skeleton; Tasks 4–7 fill in `create_pool`, `create_scenario_adapter`, `session_detail`, and `frontend`.

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The package-level descriptor core's loader resolves.

Everything except ``create_pool`` / ``create_scenario_adapter`` /
``session_detail`` is metadata core validates BEFORE running any of this
package's logic, which is why the class body carries no uterm import.
"""

from __future__ import annotations

from typing import Any

from octowright.plugins.contract import PLUGIN_API_VERSION

KIND = "terminal"

#: The seven tools that moved out of core's `server/terminal/lifecycle.py`.
#: Declared here and registered by importing `tool_module`; core refuses the
#: plugin at validation if any name collides with a core tool.
TOOL_NAMES = frozenset(
    {
        "terminal_launch",
        "terminal_send_input",
        "terminal_snapshot",
        "terminal_read",
        "terminal_wait_for",
        "terminal_close",
        "terminal_list",
    }
)


class TerminalPlugin:
    kind = KIND
    display_name = "Terminal"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = TOOL_NAMES
    tool_module = "octowright_terminal.tools"
    profile_name = "terminals"
    frontend = None  # Task 7

    def create_pool(self, ctx: Any) -> Any:
        from octowright_terminal.pool import TerminalPool

        return TerminalPool(ctx)

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None  # Task 5

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}  # Task 6


plugin = TerminalPlugin()
```

- [ ] **Step 5: Register the workspace member and install**

In the root `pyproject.toml`, add `packages/octowright-terminal` to the uv workspace members list (find the existing `[tool.uv.workspace]` table — `packages/octowright-frontend` is not a Python member, so if no table exists, create one) and add a dev-group dependency on `octowright-terminal` so the test suite can import it:

```toml
[tool.uv.workspace]
members = ["packages/octowright-terminal"]

[tool.uv.sources]
octowright-terminal = { workspace = true }
```

Then install it editable **without evicting the uterm editables**:

Run: `uv pip install -e packages/octowright-terminal`

- [ ] **Step 6: Run the test to verify it passes**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_entry_point.py -v --no-cov`
Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/octowright-terminal/pyproject.toml \
        packages/octowright-terminal/src/octowright_terminal/__init__.py \
        packages/octowright-terminal/src/octowright_terminal/plugin.py \
        packages/octowright-terminal/tests/test_entry_point.py \
        pyproject.toml
git commit -m "feat(terminal): package skeleton with a session-kind entry point

Core reaches this package through the octowright.session_kinds entry point and
nothing else, so the entry point is tested before anything it points at exists.

Depends on provide-uterm-server[ssh], not [gateway]: the SSH connector imports
asyncssh at module scope and raises ImportError without it, so core's terminal
extra has been shipping an SSH connector that could not be constructed."
```

---

### Task 2: Relocate the terminal engine modules

Pure relocation. No behaviour changes — that is what makes it reviewable.

**Files:**
- Move: `src/octowright/terminal/{session,engine,translate,redact,errors,supervision,connector_config,availability}.py` → `packages/octowright-terminal/src/octowright_terminal/`
- Modify: the moved files' imports
- Test: existing `tests/terminal/` retargeted

**Interfaces:**
- Consumes: nothing from Task 1 beyond the package existing.
- Produces: `octowright_terminal.session.TerminalSession`, `octowright_terminal.engine.TerminalEngine`, `octowright_terminal.errors.ProtectedTerminalCloseError`, `octowright_terminal.connector_config` builders.

- [ ] **Step 1: Move the files with git mv so history follows**

```bash
cd packages/octowright-terminal/src/octowright_terminal
for m in session engine translate redact errors supervision connector_config availability; do
  git mv ../../../../src/octowright/terminal/$m.py ./$m.py
done
```

Leave `src/octowright/terminal/pool.py` and `__init__.py` in place for now — Task 3 handles the pool, and core still imports `octowright.terminal.is_available` until Task 9.

- [ ] **Step 2: Rewrite intra-package imports**

In every moved file, rewrite `from octowright.terminal.X import` → `from octowright_terminal.X import`, and `from octowright.terminal import` → `from octowright_terminal import`.

Run: `grep -rn "octowright\.terminal" packages/octowright-terminal/src/` — expected: no hits.

Imports of **core** (`octowright.recorder`, `octowright.defaults`, `octowright._tracing`, …) stay exactly as they are: this package depends on octowright, which is the normal direction.

- [ ] **Step 3: Retarget the tests**

Move the test modules that cover only the relocated code:

```bash
mkdir -p packages/octowright-terminal/tests
git mv tests/terminal/test_engine.py tests/terminal/test_redact.py \
       tests/terminal/test_connector_config.py tests/terminal/test_connector_canonical_order.py \
       tests/terminal/test_pty_connector_contract.py tests/terminal/conftest.py \
       packages/octowright-terminal/tests/
```

Rewrite their imports the same way. Leave `tests/terminal/test_pool.py`, `test_mcp_tools.py`, `test_dashboard_sessions.py`, `test_session.py`, `test_ssh_args.py`, `test_telemetry.py` where they are until the tasks that move their subjects.

- [ ] **Step 4: Verify nothing regressed**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/ tests/terminal/ -v --no-cov`
Expected: the same 62 tests pass, now split across two directories. If a count drops, a module stopped being collected — find it before continuing.

- [ ] **Step 5: Commit**

```bash
git add -A packages/octowright-terminal src/octowright/terminal tests/terminal
git commit -m "refactor(terminal): relocate the engine modules into the plugin package

Pure relocation via git mv so history follows; the only edits are intra-package
import rewrites. The pool and __init__ stay behind until their own tasks, since
core still imports is_available until the deletion phase."
```

---

### Task 3: Conform `TerminalPool` to `SessionPool` and route it through `ctx.begin_session`

The substantive one. Today `TerminalPool()` builds its own `Recorder` and returns bare values; the contract requires a `LaunchResult`/`CloseResult` and a core-owned launch transaction.

**Files:**
- Move: `src/octowright/terminal/pool.py` → `packages/octowright-terminal/src/octowright_terminal/pool.py`
- Test: `packages/octowright-terminal/tests/test_pool_contract.py` (new), plus relocated `tests/terminal/test_pool.py`

**Interfaces:**
- Consumes: `octowright.plugins.session_launch.PluginContext` — `ctx.begin_session(...)` yields a `SessionLaunch` with `.recorder`, `.log_path`, `.instance_id`, `.kind`, and `.commit(record) -> LaunchResult`. `ctx.redaction_mode() -> str`.
- Produces: `TerminalPool(ctx)` implementing `SessionPool`: `async launch(**kwargs) -> LaunchResult`, `get(id) -> TerminalSession`, `maybe_get(id) -> TerminalSession | None`, `iter_sessions() -> Iterator[TerminalSession]`, `async close(id, *, force=False) -> CloseResult`, `async close_all(*, force=False) -> None`.

- [ ] **Step 1: Write the failing contract test**

Create `packages/octowright-terminal/tests/test_pool_contract.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""``TerminalPool`` against the SessionPool contract, not against its old shape.

The pool used to build its own Recorder and return bare values. Core now owns
the launch transaction, so a launch that fails must leave no orphan recording
and a commit must go through ``SessionLaunch.commit`` -- which is also what
enforces cross-pool id uniqueness.
"""

from __future__ import annotations

import pytest

from octowright.plugins.contract import SessionPool
from octowright.plugins.session_launch import PluginContext
from octowright_terminal.pool import TerminalPool


@pytest.fixture
def ctx(tmp_path):
    return PluginContext(kind="terminal", recordings_dir=tmp_path, id_in_use=lambda _id: False)


def test_the_pool_satisfies_the_session_pool_protocol(ctx):
    pool = TerminalPool(ctx)
    assert isinstance(pool, SessionPool)


@pytest.mark.asyncio
async def test_launch_returns_a_launch_result_with_the_contract_keys(ctx):
    pool = TerminalPool(ctx)
    result = await pool.launch(kind="pty", command="/bin/sh")
    try:
        assert result["kind"] == "terminal", "session kind is terminal; pty is the CONNECTOR type"
        assert result["instance_id"]
        assert result["log_path"]
    finally:
        await pool.close(result["instance_id"], force=True)


@pytest.mark.asyncio
async def test_close_returns_a_close_result(ctx):
    pool = TerminalPool(ctx)
    result = await pool.launch(kind="pty", command="/bin/sh")
    closed = await pool.close(result["instance_id"], force=True)
    assert closed["instance_id"] == result["instance_id"]
    assert closed["closed"] is True


@pytest.mark.asyncio
async def test_a_failed_launch_leaves_no_orphan_recording(ctx, tmp_path):
    pool = TerminalPool(ctx)
    with pytest.raises(Exception):
        await pool.launch(kind="pty", command="/nonexistent/shell/that/cannot/exec")
    # The transaction discards an opening-row-only recording. Any .jsonl left
    # behind here is the orphan the launch transaction exists to prevent.
    assert list(tmp_path.glob("*.jsonl")) == []
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_pool_contract.py -v --no-cov`
Expected: FAIL — `TerminalPool` does not accept a `ctx` argument yet.

- [ ] **Step 3: Move the pool and rewire it**

```bash
git mv src/octowright/terminal/pool.py packages/octowright-terminal/src/octowright_terminal/pool.py
```

Then change three things and nothing else:

1. `__init__(self, ctx)` stores `self._ctx = ctx` (it currently takes no arguments).
2. `launch` opens `async with self._ctx.begin_session(...) as launch:` instead of constructing a `Recorder` and calling `recorder.new_log_path` itself, uses `launch.recorder` / `launch.log_path` / `launch.instance_id` to build the `TerminalSession`, and returns `launch.commit(session)`. Delete `_discard_failed_launch` — the transaction does that now, and leaving it means two things own the same rollback.
3. `close` returns `{"instance_id": instance_id, "kind": "terminal", "closed": True}`; `iter_sessions` returns `iter(tuple(self._sessions.values()))` so it satisfies `Iterator` rather than returning a tuple.

Read `src/octowright/plugins/session_launch.py` for `begin_session`'s exact signature before writing the call — do not guess it.

- [ ] **Step 4: Run the contract test and the relocated pool test**

```bash
git mv tests/terminal/test_pool.py tests/terminal/test_session.py tests/terminal/test_telemetry.py \
       packages/octowright-terminal/tests/
```

Rewrite their imports, then run:
`uv run --active --no-sync pytest packages/octowright-terminal/tests/ -v --no-cov`
Expected: all pass, including the four new contract tests.

- [ ] **Step 5: Commit**

```bash
git add -A packages/octowright-terminal src/octowright/terminal tests/terminal
git commit -m "refactor(terminal): conform the pool to SessionPool and the launch transaction

The pool built its own Recorder and returned bare values. Core owns the launch
transaction now, so launch goes through ctx.begin_session and commits the record
-- which is also what enforces cross-pool instance-id uniqueness, something the
pool could not do for itself.

_discard_failed_launch is deleted rather than kept alongside: the transaction
already discards an opening-row-only recording, and two owners of one rollback
is how they drift."
```

---

### Task 4: Relocate the MCP tools

**Files:**
- Move: `src/octowright/server/terminal/lifecycle.py` → `packages/octowright-terminal/src/octowright_terminal/tools.py`
- Delete: `src/octowright/server/terminal/__init__.py`
- Test: relocated `tests/terminal/test_mcp_tools.py`

**Interfaces:**
- Consumes: `TerminalPool` from Task 3.
- Produces: module `octowright_terminal.tools`, which on import registers the seven tools named in `TerminalPlugin.tool_names`.

- [ ] **Step 1: Move the module**

```bash
git mv src/octowright/server/terminal/lifecycle.py packages/octowright-terminal/src/octowright_terminal/tools.py
git rm src/octowright/server/terminal/__init__.py
```

- [ ] **Step 2: Rewire how it reaches the pool**

The tools currently read `from octowright.server._state import terminal_pool`. That global goes away in Task 9. Read how `tests/plugins/reference/tools.py` gets its pool — it resolves through the plugin registry — and follow that exact pattern rather than inventing a second one.

Registration decorator stays `@mcp.tool` from `octowright.server._state`; the tools are still MCP tools, they are just contributed by a plugin now.

- [ ] **Step 3: Verify the tools still register and work**

```bash
git mv tests/terminal/test_mcp_tools.py packages/octowright-terminal/tests/
```

Rewrite imports, then run:
`uv run --active --no-sync pytest packages/octowright-terminal/tests/test_mcp_tools.py -v --no-cov`
Expected: pass.

- [ ] **Step 4: Verify the declared names match what actually registers**

Add to `packages/octowright-terminal/tests/test_mcp_tools.py`:

```python
def test_declared_tool_names_match_what_the_module_registers():
    """A declared name core validates against, and a registered name that does
    not match it, is a collision check that checks the wrong thing."""
    import octowright_terminal.tools  # noqa: F401  (import registers them)
    from octowright.server._state import mcp
    from octowright_terminal.plugin import TOOL_NAMES

    registered = {name for name in mcp._tool_manager._tools if name.startswith("terminal_")}
    assert registered == set(TOOL_NAMES)
```

- [ ] **Step 5: Commit**

```bash
git add -A packages/octowright-terminal src/octowright/server tests/terminal
git commit -m "refactor(terminal): move the terminal_* MCP tools into the plugin

They reach their pool through the plugin registry now rather than a core global,
and a test pins that the names the descriptor declares are exactly the names the
module registers -- a declaration core validates against that does not match
reality is a collision check aimed at the wrong target."
```

---

### Task 5: The scenario adapter

Terminal has run on a hardcoded scenario branch since step 3; `scenario_kinds.adapter_for` returns `None` for it by design. It now gets a real adapter, and the participant validation and launch-kwarg resolution move out of core's `scenarios.py` with it.

**Files:**
- Create: `packages/octowright-terminal/src/octowright_terminal/scenario.py`
- Move out of: `src/octowright/scenarios.py` — `_validate_terminal_options` (line ~70) and `resolve_terminal_launch` (line ~507)
- Test: `packages/octowright-terminal/tests/test_scenario_adapter.py`

**Interfaces:**
- Consumes: `octowright.plugins.contract.ScenarioAdapter` — the mandatory floor is `resolve_participant(spec, persona) -> dict[str, Any]`.
- Produces: `TerminalScenarioAdapter(pool)`.

**Capability shape, and it is deliberate:** the adapter implements **only** `resolve_participant`. It does not implement `run_macro`, `wait_for_sync`, `set_dialog_policy`, or `install_mock_routes`. Core derives capabilities from which Protocols the adapter satisfies (`_CAPABILITY_PROTOCOLS`), so a terminal participant declaring `startup_macros` still gets a validation error and `scenario_run_macro` against a terminal still reports the missing capability by name — exactly the behaviour core hardcoded before, now falling out of the contract instead.

- [ ] **Step 1: Write the failing test**

Create `packages/octowright-terminal/tests/test_scenario_adapter.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The adapter that replaces core's hardcoded terminal branch.

Terminal is the case the capability vocabulary was designed around: a kind that
can JOIN a scenario but cannot run macros or sync. The negative assertions below
are the point of the file -- they pin that the narrowing actually narrows.
"""

from __future__ import annotations

from octowright.plugins.contract import (
    ScenarioAdapter,
    SupportsDialogPolicy,
    SupportsMacros,
    SupportsMockRoutes,
    SupportsSync,
)
from octowright_terminal.scenario import TerminalScenarioAdapter


def test_the_adapter_satisfies_the_mandatory_floor():
    adapter = TerminalScenarioAdapter(pool=object())
    assert isinstance(adapter, ScenarioAdapter)


def test_the_adapter_claims_no_capability_it_cannot_honour():
    adapter = TerminalScenarioAdapter(pool=object())
    assert not isinstance(adapter, SupportsMacros)
    assert not isinstance(adapter, SupportsSync)
    assert not isinstance(adapter, SupportsDialogPolicy)
    assert not isinstance(adapter, SupportsMockRoutes)


def test_resolve_participant_returns_pty_launch_kwargs():
    from octowright.scenarios import Participant

    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ops", kind="terminal", role="operator", options={"connector_type": "pty"})
    kwargs = adapter.resolve_participant(p, persona=None)
    assert kwargs["kind"] == "pty", "connector type, not the session kind"
    assert kwargs["profile"] == "ops"


def test_an_unsupported_connector_type_is_refused():
    import pytest

    from octowright.scenarios import Participant

    adapter = TerminalScenarioAdapter(pool=object())
    p = Participant(persona="ops", kind="terminal", role="operator", options={"connector_type": "carrier-pigeon"})
    with pytest.raises(ValueError, match="connector_type"):
        adapter.resolve_participant(p, persona=None)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_scenario_adapter.py -v --no-cov`
Expected: FAIL — `ModuleNotFoundError: octowright_terminal.scenario`.

- [ ] **Step 3: Write the adapter**

Create `packages/octowright-terminal/src/octowright_terminal/scenario.py`. Move the bodies of `_validate_terminal_options` and `resolve_terminal_launch` from core's `scenarios.py` verbatim — do not rewrite them, the behaviour is already correct and reviewed. Wrap them:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Terminal's scenario participation.

Implements the mandatory floor and NOTHING else. Core derives capabilities from
the Protocols an adapter satisfies, so the absence of run_macro here is what
makes a terminal participant declaring startup_macros a validation error -- the
behaviour core used to hardcode, now falling out of the contract.

The validation and kwarg resolution below moved verbatim out of core's
scenarios.py: they are terminal's rules about terminal's own options, and core
had no business knowing that `cols` must be an int.
"""

from __future__ import annotations

from typing import Any


class TerminalScenarioAdapter:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]:
        _validate_options(spec)
        return _resolve_launch(spec, persona)
```

with `_validate_options` and `_resolve_launch` holding the moved bodies. `_validate_options` raises `ValueError` naming `connector_type` on an unsupported value, matching what the test expects and what core did.

Wire it up in `plugin.py`: `create_scenario_adapter` returns `TerminalScenarioAdapter(pool)` instead of `None`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_scenario_adapter.py -v --no-cov`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add -A packages/octowright-terminal src/octowright/scenarios.py
git commit -m "feat(terminal): a real scenario adapter, replacing the hardcoded branch

Implements the mandatory floor and nothing else, which is the point: core
derives capabilities from the Protocols an adapter satisfies, so terminal's
inability to run macros now falls out of the contract instead of being a
hardcoded special case. The option validation and launch-kwarg resolution move
here verbatim -- they were always terminal's rules about terminal's options."
```

---

### Task 6: `session_detail`

**Files:**
- Modify: `packages/octowright-terminal/src/octowright_terminal/plugin.py`
- Move out of: `src/octowright/http/routes/sessions.py` — `_terminal_session_detail` (line ~192)
- Test: `packages/octowright-terminal/tests/test_session_detail.py`

**Interfaces:**
- Produces: `TerminalPlugin.session_detail(session) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing test**

Create `packages/octowright-terminal/tests/test_session_detail.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""What the dashboard shows for a live terminal.

A terminal has no page, console, download, video or trace artefacts, so the
payload is the summary plus terminal-relevant fields. Core used to short-circuit
before its browser detail builder to achieve this; now the plugin simply says
what its own sessions look like.
"""

from __future__ import annotations

from octowright_terminal.plugin import plugin


class _FakeSession:
    instance_id = "abc123"
    kind = "terminal"
    label = "ops-box"
    connector_type = "ssh"
    log_path = "/tmp/rec/abc123.jsonl"
    protected = False


def test_detail_carries_the_terminal_fields():
    detail = plugin.session_detail(_FakeSession())
    assert detail["id"] == "abc123"
    assert detail["kind"] == "terminal"
    assert detail["connector_type"] == "ssh"


def test_detail_carries_no_browser_only_fields():
    """A terminal has none of these. Reporting them as null invites a dashboard
    to render an empty video player rather than omit the pane."""
    detail = plugin.session_detail(_FakeSession())
    for browser_only in ("video", "console", "downloads", "trace", "pages"):
        assert browser_only not in detail
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_session_detail.py -v --no-cov`
Expected: FAIL — `session_detail` returns `{}`.

- [ ] **Step 3: Implement it**

Read `_terminal_session_detail` in `src/octowright/http/routes/sessions.py` and move its field set into `TerminalPlugin.session_detail`. Keep the field names byte-identical — the dashboard reads them, and a rename here is a silent UI break.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_session_detail.py -v --no-cov`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add -A packages/octowright-terminal
git commit -m "feat(terminal): the plugin says what its own session detail looks like

Field names are byte-identical to core's _terminal_session_detail: the dashboard
reads them, so a rename here would be a silent UI break rather than a refactor."
```

---

### Task 7: The renderer

The frontend half. `session-terminal.ts` and `terminal-view.ts` become the plugin's `mountStream`, and the xterm dependency leaves core's bundle.

**Files:**
- Create: `packages/octowright-terminal/src/octowright_terminal/assets/renderer.js`
- Create: `packages/octowright-terminal/assets-src/` (TypeScript source + its vitest test), or build the renderer as plain JS — see Step 1
- Modify: `packages/octowright-terminal/src/octowright_terminal/plugin.py` (set `frontend`)
- Test: `packages/octowright-terminal/tests/test_frontend_asset.py`

**Interfaces:**
- Consumes: `octowright.plugins.contract.FrontendAsset(renderer_api_version, asset_dir, module_path, layout)`; the renderer contract in `packages/octowright-frontend/src/plugin-contract.d.ts` — `mountStream(el, ctx) -> StreamHandle | Promise<StreamHandle>`, `StreamHandle = {feed(events), destroy()}`.
- Produces: `TerminalPlugin.frontend`.

**Decide and record: how xterm reaches the renderer.** Core's page no longer bundles xterm, and the plugin's module is served as a bare file — there is no bundler step on the serving path. Read `packages/octowright-frontend/package.json` and `vite.config.ts`, then pick one and **say which you picked and why in your report**:
  (a) bundle xterm into `renderer.js` at build time from an `assets-src/` TypeScript source, producing a self-contained module — larger file, no runtime resolution;
  (b) ship a plain-JS renderer with no xterm and render the output stream as pre-formatted text — loses ANSI emulation, which is most of terminal's value.
  (a) is almost certainly right; (b) is listed so the trade is explicit rather than assumed. If you choose (a), the build must be reproducible from a committed source and wired into the package's build, not a one-off paste.

- [ ] **Step 1: Write the failing test**

Create `packages/octowright-terminal/tests/test_frontend_asset.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The plugin's declared renderer, and that it is actually on disk.

Mirrors tests/plugins/test_reference_frontend.py in core: a FrontendAsset whose
module_path does not exist produces a 404 in the dashboard and no error anywhere
else, so the declaration is checked against the filesystem.
"""

from __future__ import annotations

from octowright_terminal.plugin import plugin


def test_the_plugin_declares_a_frontend_asset():
    fa = plugin.frontend
    assert fa is not None
    assert fa.layout == "stream"
    assert fa.renderer_api_version == 1


def test_the_declared_module_exists_on_disk():
    fa = plugin.frontend
    assert fa.asset_dir.is_dir()
    assert (fa.asset_dir / fa.module_path).is_file()


def test_the_renderer_exports_mount_stream():
    fa = plugin.frontend
    source = (fa.asset_dir / fa.module_path).read_text(encoding="utf-8")
    assert "mountStream" in source
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/test_frontend_asset.py -v --no-cov`
Expected: FAIL — `plugin.frontend` is `None`.

- [ ] **Step 3: Write the renderer**

Port `terminal-view.ts` and the feed half of `session-terminal.ts` into a `mountStream`. The page chrome — header, footer, auth notice, timeline, tail WebSocket, event fetch — is core's now and must **not** be reimplemented; `bootStreamSession` already does all eight. What is left is genuinely small: mount an xterm instance into the element, and on each `feed(events)` batch write each `terminal_output` event's `data` verbatim, calling `term.reset()` first when the delta carries `reset: true`.

Honour the three contract rules stated in `plugin-contract.d.ts`: mount may be async; history arrives before live in the same `feed` stream; delivery is **at-least-once**, so a replayed batch re-writes its bytes, which for a terminal is the honest behaviour.

- [ ] **Step 4: Write a runtime test for the renderer**

Core's Task 9 lesson applies here: asserting the file contains `"mountStream"` proves the declaration, not the contract. Add a vitest test that imports the built `renderer.js`, drives `mountStream` → `feed` → `destroy`, and asserts the DOM. Put it where the frontend package's vitest `include` already reaches (`tests/**/*.test.ts`), following `packages/octowright-frontend/tests/reference-plugin-renderer.test.ts`.

- [ ] **Step 5: Run both suites**

```
uv run --active --no-sync pytest packages/octowright-terminal/tests/test_frontend_asset.py -v --no-cov
cd packages/octowright-frontend && npm test
```
Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add -A packages/octowright-terminal packages/octowright-frontend
git commit -m "feat(terminal): the plugin ships its own renderer

Core owns the page chrome -- header, footer, auth notice, timeline, tail and
event fetch -- so what moved here is only the xterm mount and the feed loop.
Tested by execution rather than by string-matching the file: a renderer that
imports cleanly and violates the runtime contract is exactly what a
string-match misses."
```

---

### Task 8: The contract-parity gate

**This is the task that authorises deletion.** Spec §12: nothing leaves core until the external plugin passes the same suite the reference plugin does.

**Files:**
- Create: `packages/octowright-terminal/tests/test_contract_parity.py`
- Test: itself

**Interfaces:**
- Consumes: everything Tasks 1–7 produced.

- [ ] **Step 1: Find the reference plugin's contract suite**

Read `tests/plugins/` and identify the tests that assert *contract* conformance rather than reference-specific behaviour — in particular the one spec §11.1 describes, which asserts the reference plugin covers every capability-vocabulary member and every `SessionPool` method. Report which files you judged to be the contract suite and why.

- [ ] **Step 2: Write the parity test**

Create `packages/octowright-terminal/tests/test_contract_parity.py` asserting, for `octowright_terminal.plugin.plugin`:

- every `SessionPool` method exists on the pool with a compatible signature;
- every member of the capability vocabulary is either implemented by the adapter or **deliberately absent**, with the absence asserted (terminal implements only the floor, so four absences are asserted);
- `tool_names` is non-empty, collides with no core tool name, and matches what `tool_module` registers;
- `session_detail` returns a mapping carrying `id` and `kind`;
- `frontend` is a `FrontendAsset` whose `module_path` exists on disk.

Drive it off the contract's own `_CAPABILITY_PROTOCOLS` table rather than a hand-written list, so a capability added to core without being considered here fails this test rather than passing silently.

- [ ] **Step 3: Run it**

Run: `uv run --active --no-sync pytest packages/octowright-terminal/tests/ -v --no-cov`
Expected: all pass. **If anything fails, stop and report — the contract is inadequate, and that discovery is what this ordering exists to produce.** Do not proceed to Task 9.

- [ ] **Step 4: Prove the plugin actually works end to end, enabled by name**

Run the daemon's own path with the plugin enabled:

```bash
OCTOWRIGHT_PLUGINS=terminal uv run --active --no-sync pytest tests/plugins/ -v --no-cov
OCTOWRIGHT_PLUGINS=terminal uv run --active --no-sync octowright selftest
```

Expected: the plugin loads, the seven `terminal_*` tools appear in `selftest`'s output, and core's plugin suite still passes. Report the tool count with and without `OCTOWRIGHT_PLUGINS=terminal` set.

- [ ] **Step 5: Commit**

```bash
git add packages/octowright-terminal/tests/test_contract_parity.py
git commit -m "test(terminal): contract parity with the reference plugin

The gate the deletion phase is allowed to proceed past. Driven off the
contract's own capability table rather than a hand-written list, so a capability
added to core without being considered here fails rather than passing silently."
```

---

### Task 9: Delete terminal from core's Python

Only after Task 8 is green.

**Files:**
- Modify: `src/octowright/scenario_kinds.py`, `scenarios.py`, `scenarios_pool.py`, `server/scenarios.py`, `server/_state.py`, `server/_optional_tools.py`, `http/state.py`, `http/routes/sessions.py`, `http/discovery.py`, `cli/serve.py`, `cli/scenario.py`, `defaults.py`
- Delete: `src/octowright/terminal/` (the remaining `__init__.py`)

**Interfaces:**
- Produces: a core with no `terminal_pool`, no `TERMINAL_KIND`, and no `kind == "terminal"` branch.

Each site and what replaces it:

| File | What goes | What replaces it |
|---|---|---|
| `scenario_kinds.py:30,59,82-92,114` | `TERMINAL_KIND`, the `adapter_for` special case, `pool_for_kind`'s terminal branch, `known_kinds`' union | plugin-registry lookup only |
| `scenarios.py:70-101,115-118,507+` | `_validate_terminal_options`, the `p.kind == TERMINAL_KIND` branch, `resolve_terminal_launch` | the adapter (Task 5) |
| `scenarios_pool.py:92,110,142,162,173,184-197,209,229,237-267` | every `terminal_pool` parameter and `terminal_specs` partition | the existing plugin-kind path |
| `server/scenarios.py:38,59-62,103,122,157,239` | `terminal_pool` import and `p.kind == "terminal"` branch | plugin path |
| `server/_state.py:21,35,87-96` | the `_terminal.is_available()` block and `terminal_pool` global | nothing |
| `server/_optional_tools.py:15-21` | the whole conditional import | nothing — delete the file if it has no other purpose |
| `http/state.py:150-158,183,207` | the `terminal_pool` module property | nothing |
| `http/routes/sessions.py:47,74-81,192-212` | `ProtectedTerminalCloseError` import, the pool branch, `_terminal_session_detail` | registry iteration + `plugin.session_detail` |
| `http/discovery.py:80,95-100` | the `terminal_start` classification | see the note below |
| `cli/serve.py:548,557-568` | `_close_terminal_pool_on_shutdown` | plugin pools' `close_all` |
| `cli/scenario.py:39-49,69-126` | `_make_terminal_pool` and its wiring | plugin path |
| `defaults.py:216-220` | `SUPPORTED_TERMINAL_KINDS`, the SSH port default | moved into the plugin |

**`http/discovery.py` needs a decision, not a deletion.** It classifies a *closed recording* off disk by its opening row, and a closed terminal recording exists whether or not the plugin is currently enabled. Deleting the `terminal_start` branch outright makes historical terminal recordings classify as `unknown` — which, after step 4's fix, renders the generic fallback rather than the browser debugger, but still loses the kind. Decide between: (a) keep a generic "opening row names its own kind" rule that reads `kind` off the row rather than hardcoding `terminal`; (b) let the plugin contribute a discovery classifier. **(a) is recommended** — it is less machinery and it generalises to every plugin kind, where (b) makes disk-discovery depend on which plugins happen to be enabled today. Record the choice and reasoning in your report.

- [ ] **Step 1: Delete, one file at a time, running the suite after each**

Work down the table. After each file: `uv run --active --no-sync pytest -m "not live_browser and not memory_isolated" --no-cov`. Deleting all twelve and then debugging is how a whole afternoon disappears.

- [ ] **Step 2: Assert core never imports uterm**

Add to `tests/test_plugin_isolation.py` (create it):

```python
def test_core_never_imports_uterm():
    """The whole point of the extraction. A core install has no uterm, so an
    import anywhere under src/octowright is an ImportError for every user who
    did not install the plugin."""
    import pathlib

    hits = []
    for path in pathlib.Path("src/octowright").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "provide.uterm" in text or "provide_uterm" in text:
            hits.append(str(path))
    assert hits == [], f"core must not reference uterm: {hits}"
```

- [ ] **Step 3: Full gates**

```
uv run --active --no-sync pytest -m "not live_browser and not memory_isolated" --no-cov
make lint
```
Expected: both green. `make lint` catches things the suite does not — including `scripts/check_operation_gate_architecture.py`.

- [ ] **Step 4: Commit**

```bash
git add -A src/octowright tests
git commit -m "refactor(core): core stops knowing a terminal exists

Every terminal branch, the terminal_pool global, and the hardcoded kind checks
are gone; a change to scenarios, session detail, discovery or close now reasons
about a registry rather than a second hardcoded kind. A test asserts core
references uterm nowhere, which is the property the extraction was for."
```

---

### Task 10: Delete terminal from core's frontend

**Files:**
- Delete: `packages/octowright-frontend/src/session-terminal.ts`, `terminal-view.ts`, and their tests
- Modify: `packages/octowright-frontend/src/types.ts` (drop `connector_type` and the `telnet` member), `session.ts` (drop the terminal branch), `package.json` (drop the xterm dependencies)

**Interfaces:**
- Produces: a frontend with no terminal-specific code, where `session.ts` dispatches terminal through the same plugin path as any other kind.

- [ ] **Step 1: Drop the terminal branch from `session.ts`**

The branch checked terminal *first*, before the plugin path, precisely so terminal's behaviour was unchanged while it lived in core. It is now a plugin kind like any other, so the branch goes and terminal flows through `resolveRenderer` → `importRenderer` → `bootStreamSession`.

**Verified before this plan was written, so you do not have to re-derive it:** `terminal` is **not** in core's `RESERVED_KINDS` (`src/octowright/plugins/identity.py:61` — `chromium`, `firefox`, `webkit`, `browser`, `unknown`, `session`), and it is **not** in the frontend's `CORE_RESERVED_KINDS` mirror (`session.ts:71`). So the loader accepts a plugin declaring `kind = "terminal"` today, and neither set needs editing. The only thing to remove is the explicit terminal branch that sits *ahead* of the plugin path in `bootSession` — it exists precisely so terminal's behaviour stayed unchanged while it lived in core, and it is what currently prevents terminal reaching `resolveRenderer` at all.

- [ ] **Step 2: Drop `connector_type` and `telnet` from `types.ts`**

Spec §8.8: they leave *with the plugin*. That is now.

- [ ] **Step 3: Remove the xterm dependencies**

Drop `@xterm/xterm` and the `addon-fit` / `addon-web-links` / `addon-unicode11` addons from `packages/octowright-frontend/package.json`, then `npm install` to update the lockfile. Report the change in built bundle size — core's dashboard should get smaller by roughly the ~250 KB chunk the lazy import used to pull.

- [ ] **Step 4: Verify**

```
cd packages/octowright-frontend && npm test && npx tsc --noEmit && npm run lint
```
Expected: all green. Tests referencing the deleted modules must be deleted, not skipped.

- [ ] **Step 5: Commit**

```bash
git add -A packages/octowright-frontend
git commit -m "refactor(frontend): terminal leaves core's bundle

session.ts dispatches terminal through the plugin path like any other kind, and
connector_type and the telnet member leave types.ts with it per spec 8.8. The
xterm dependency goes with them, so a dashboard that never opens a terminal no
longer carries the emulator."
```

---

### Task 11: Documentation

**Files:**
- Modify: `CLAUDE.md` / `AGENTS.md` (the "Terminal Sessions (optional)" section, the key-files table, the capability-profiles paragraph, `OCTOWRIGHT_PLUGINS`), `README.md`

**Spec §10 is the constraint here:** extraction does **not** make terminal installable. Do not write anything implying `pip install octowright-terminal` works — `provide-uterm` still 404s from PyPI. What changed is that the blocker left octowright's release path.

- [ ] **Step 1: Rewrite the terminal section**

It becomes a short pointer: terminal is a session-kind plugin, install it from the source checkout, enable it with `OCTOWRIGHT_PLUGINS=terminal`. The long connector documentation moves to the package's own README. Spec §10 notes the `AGENTS.md` reduction is "disproportionately valuable because that file is read on every task" — so the goal is genuinely fewer lines in core's docs, not the same text relocated within them.

- [ ] **Step 2: Verify the docs gate**

Run: `uv run --active --no-sync python scripts/check_agent_docs_sync.py`
Expected: in sync. Also re-run `make lint`, which runs it plus `check_telemetry_docs.py` — the terminal metrics table entries need to match reality.

- [ ] **Step 3: Commit**

```bash
git add -A CLAUDE.md AGENTS.md README.md packages/octowright-terminal
git commit -m "docs: terminal is a plugin, and still is not installable from PyPI

The extra's long connector documentation moves to the package's own README;
core keeps a pointer. Deliberately does not claim pip install works -- uterm
still 404s from PyPI, and what changed is that the blocker left octowright's
release path, not that it went away."
```

---

### Task 12: CI

**Files:**
- Modify: `.github/workflows/ci.yml`

The terminal suite has **never run in CI** — it skips at collection because uterm was never installed. That is why three failures sat unnoticed until this session installed it locally (two were `asyncssh` missing, which Task 1 fixes at the dependency level).

- [ ] **Step 1: Decide what CI can actually run**

uterm is unpublished, so CI cannot `pip install` it. Options: (a) leave the plugin's suite skipped in CI and say so plainly in the workflow with a comment naming the reason; (b) check out the sibling repo in CI if it is reachable. Read the existing workflow and pick what is honest for this repo. **Do not fake it** — a suite that silently skips is what produced this situation.

- [ ] **Step 2: Ensure core's suite proves the isolation**

Whatever is decided above, core's own CI legs **must** run `tests/test_plugin_isolation.py` from Task 9 — that test needs no uterm and is the one that catches a core file re-importing it.

- [ ] **Step 3: Verify and commit**

```bash
make lint
git add .github/workflows/ci.yml
git commit -m "ci: say plainly whether the terminal plugin's suite runs

It never ran before -- it skipped at collection without uterm, which is how
three failures went unnoticed. Whatever the answer, the workflow now states it
rather than leaving a silent skip to look like a pass."
```

---

## Self-Review

**1. Spec coverage.** §10's uterm caveat is a Global Constraint and Task 11's explicit subject. §11.1's reference-plugin contract suite is Task 8's parity gate. §11.2's obligations — launch transaction, control rows, ID uniqueness, tool collision — are covered by Tasks 3, 4 and 8 against the plugin. §12's step 5 ordering ("deletes nothing until the external plugin passes") is the Tasks 1–8 / 9–12 split and is stated as a Global Constraint.

**2. Placeholder scan.** Task 7's xterm-bundling decision and Task 9's `discovery.py` decision are deliberate open choices with a recommendation and a reason, not TBDs — both name the options and ask for the reasoning to be reported. Task 12's CI choice is the same. These are judgement calls that depend on reading files the plan cannot embed, not gaps.

**3. Type consistency.** `TerminalPool(ctx)` in Task 3 matches `create_pool` in Task 1's descriptor. `TerminalScenarioAdapter(pool)` in Task 5 matches `create_scenario_adapter(pool)`. `TOOL_NAMES` in Task 1 is what Task 4's registration test asserts against. `FrontendAsset(renderer_api_version, asset_dir, module_path, layout)` in Task 7 matches core's dataclass as read from `plugins/contract.py`.

**Known risk, stated rather than hidden:** Task 9 is the largest single task and touches twelve files. It is one task because it is one atomic change — core does not compile in a half-deleted state — but its Step 1 deliberately says to work file by file with the suite after each, and a reviewer should expect that discipline in the diff rather than one twelve-file rewrite.
