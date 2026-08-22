# Session-Kind Plugins — Step 1: Contract, Loader, Launch Transaction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the session-kind plugin contract, its discovery/load machinery, and the core-owned launch transaction, with a backend-only reference plugin proving every seam — while the built-in terminal subsystem keeps working on its existing path.

**Architecture:** A new `src/octowright/plugins/` package holds structural `Protocol`s, identity validation, entry-point discovery, a registry of per-kind session pools, and a load transaction that rolls back cleanly. Core gains `Recorder.record_control` so its own metadata rows survive the byte ceiling, and a `begin_session` async context manager that is the only way a plugin can obtain a `Recorder`. Nothing in this step changes terminal, scenarios, the dashboard, or closed-session discovery.

**Tech Stack:** Python 3.11+, `importlib.metadata` entry points, `typing.Protocol` / `runtime_checkable`, `TypedDict`, pytest, the `mcp` SDK's `MCPServer` tool manager.

**Spec:** `docs/superpowers/specs/2026-08-22-session-kind-plugins-design.md`

## Global Constraints

- **SPDX header** on every new `.py` file, verbatim:
  ```python
  # SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
  # SPDX-License-Identifier: Apache-2.0
  # SPDX-Comment: Part of octowright.
  #
  ```
- **`from __future__ import annotations`** as the first import in every new module.
- **777-line hard ceiling** on any `src/**/*.py` (`scripts/check_max_loc.py`, runs in `make lint`). Target under 300 per new module.
- **No file over 500 lines** — split before adding code that would cross it.
- **`make lint` must pass**: ruff format + lint, mypy strict, ty, bandit, codespell, SPDX, LOC, vulture (baseline `allow_findings: 0`), xenon (baseline `allow_violations: 20`), detect-secrets.
- **Commits must be signed.** Never pass `--no-gpg-sign` or `--no-verify`. If signing stalls, stop and ask.
- **Never** add a `Co-Authored-By: Claude` trailer or any AI-assistance mention to a commit message.
- **Do not touch `CHANGELOG.md`** — it moves only at release-version bump time.
- **Do not push and do not open a PR** unless explicitly asked.
- Run tests with `uv run --active pytest`. Nothing in this plan needs a real browser; add `-m "not live_browser and not memory_isolated"` when running broad suites locally.
- Terminal keeps working unchanged throughout. `tests/terminal/` must stay green after every task.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `src/octowright/plugins/__init__.py` | Public re-exports only. No logic. |
| `src/octowright/plugins/errors.py` | `ProtectedSessionCloseError`, `PluginLoadError`, `DuplicatePluginNameError`, `ControlBudgetExceededError`, `SessionIdInUseError`. |
| `src/octowright/plugins/contract.py` | `SessionKindPlugin`, `SessionPool`, `ScenarioAdapter` + capability Protocols, `FrontendAsset`, `SessionRecord`, `LaunchResult`, `CloseResult`, `PLUGIN_API_VERSION`, `RENDERER_API_VERSION`. |
| `src/octowright/plugins/identity.py` | Name/kind syntax, reserved kinds, tool-name prefix rule. |
| `src/octowright/plugins/session_launch.py` | `PluginContext`, `SessionLaunch`, `begin_session`, failed-launch rule. |
| `src/octowright/plugins/discovery.py` | Metadata-only entry-point enumeration + enable resolution. |
| `src/octowright/plugins/registry.py` | `PluginRegistry` — pools, states, cross-pool ID lookup. |
| `src/octowright/plugins/loader.py` | Two-phase load transaction with delta rollback. |
| `tests/plugins/` | Test package for everything below. |
| `tests/plugins/reference/` | The backend-only reference plugin (`plugin.py`, `pool.py`, `tools.py`). |
| `tests/plugins/_import_probe.py` | Module-scope counter proving a disabled plugin is never imported. |

**Modified:**

| Path | Change |
|---|---|
| `src/octowright/recorder.py` | Add `CONTROL_ACTIONS`, control budget, `record_control`. |
| `src/octowright/server/profiles.py` | Add `register_plugin_profile` / `plugin_profile_names`; `build_allowed_set` consults them. |
| `src/octowright/server/_state.py` | Resolve descriptors + register plugin profiles **before** computing `_allowed_tools`. |
| `src/octowright/server/_optional_tools.py` | Activate enabled plugins (tool import + pool). |
| `src/octowright/server/meta.py` | Add the `plugins` block to `octowright_status()`. |

---

## Task 1: Durable control rows in the recorder

Core's own metadata rows must survive `OCTOWRIGHT_RECORDING_MAX_BYTES`. Today `Recorder.record()` returns early once `_truncated` is set, so a `session_start` written under a small ceiling never lands, and an `artifact_registered` row committed late in a long session is dropped while `commit()` reports success.

**Files:**
- Modify: `src/octowright/recorder.py` (add near `_recording_max_bytes`, and to the `Recorder` class)
- Test: `tests/test_recorder_control_rows.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `octowright.recorder.CONTROL_ACTIONS: frozenset[str]` == `{"session_start", "artifact_registered", "recording_truncated"}`
  - `octowright.recorder.CONTROL_BUDGET_BYTES: int` == `65536`
  - `Recorder.record_control(action: str, **fields: Any) -> None` — raises `ValueError` for a non-control action, `ControlBudgetExceededError` when the budget is exhausted.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recorder_control_rows.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from pathlib import Path

import pytest

from octowright.plugins.errors import ControlBudgetExceededError
from octowright.recorder import CONTROL_BUDGET_BYTES, Recorder


def _actions(path: Path) -> list[str]:
    return [json.loads(line)["action"] for line in path.read_text().splitlines() if line.strip()]


def test_control_row_written_when_ceiling_already_hit(tmp_path, monkeypatch):
    # A ceiling smaller than any row: the first ordinary record truncates.
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "10")
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    rec.record("click", selector="#a")
    assert _actions(log) == ["recording_truncated"]

    rec.record_control("session_start", kind="refkind", label=None, profile=None)
    rec.close()

    assert _actions(log) == ["recording_truncated", "session_start"]


def test_control_rows_do_not_consume_the_action_ceiling(tmp_path, monkeypatch):
    monkeypatch.setenv("OCTOWRIGHT_RECORDING_MAX_BYTES", "500")
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    rec.record_control("session_start", kind="refkind", label=None, profile=None)
    # The control row must not have eaten the action budget.
    for i in range(3):
        rec.record("click", selector=f"#a{i}")
    rec.close()

    assert _actions(log).count("click") == 3
    assert "recording_truncated" not in _actions(log)


def test_record_control_rejects_a_non_control_action(tmp_path):
    rec = Recorder(tmp_path / "r.jsonl")
    with pytest.raises(ValueError, match="not a control action"):
        rec.record_control("click", selector="#a")
    rec.close()


def test_control_budget_is_bounded(tmp_path):
    log = tmp_path / "r.jsonl"
    rec = Recorder(log)
    payload = "x" * 1024
    with pytest.raises(ControlBudgetExceededError):
        for i in range(CONTROL_BUDGET_BYTES // 1024 + 2):
            rec.record_control("artifact_registered", artifact_id=f"a{i}", path=payload)
    rec.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --active pytest tests/test_recorder_control_rows.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins'` (the errors module lands in Task 2; create the minimal package now, see Step 3).

- [ ] **Step 3: Create the errors module and the package**

Create `src/octowright/plugins/__init__.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Session-kind plugin API.

Core supports exactly one built-in session kind (browsers). Every other kind
arrives through this package: a third-party distribution declares an
``octowright.session_kinds`` entry point, an operator enables it by name, and
core loads it into a registry of per-kind session pools.

See ``docs/superpowers/specs/2026-08-22-session-kind-plugins-design.md``.
"""

from __future__ import annotations
```

Create `src/octowright/plugins/errors.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Exception types shared across the plugin surface.

These live in their own module rather than beside their raisers because both
core and third-party plugin code import them, and a plugin importing
``octowright.plugins.loader`` to reach an exception would pull the whole load
machinery into its import graph.
"""

from __future__ import annotations


class PluginError(Exception):
    """Base for every plugin-surface error."""


class PluginLoadError(PluginError):
    """A plugin failed to resolve, validate, or activate."""


class DuplicatePluginNameError(PluginLoadError):
    """Two installed distributions declare the same entry-point name.

    Refused outright rather than resolved by enumeration order, which is
    installation-dependent and would make behaviour vary by machine.
    """


class ProtectedSessionCloseError(PluginError):
    """A close was refused because the session is protected and ``force`` was not set."""


class SessionIdInUseError(PluginLoadError):
    """A launch committed an ``instance_id`` another registered pool already holds.

    Core resolves a session by id alone across every pool, so ids must be
    globally unique, not merely unique within one pool.
    """


class ControlBudgetExceededError(PluginError):
    """A control row would exceed the recorder's separate control-row budget.

    Raised rather than silently dropped: the whole point of a control row is
    that its absence is never invisible.
    """
```

- [ ] **Step 4: Add control rows to the recorder**

In `src/octowright/recorder.py`, add after the `_PRIVATE_OFF` constant:

```python
#: Rows core writes about a session rather than about a page action. They
#: bypass ``OCTOWRIGHT_RECORDING_MAX_BYTES`` because the ceiling exists to
#: bound a firehose *page*, and dropping a metadata row instead loses the
#: recording's identity: no ``session_start`` means discovery cannot report
#: the kind, and a dropped ``artifact_registered`` means ``commit()`` returned
#: success for a registration that does not exist. ``_write_truncation_marker``
#: already bypasses the ceiling for exactly this reason; this generalizes it.
#: Core-only — ``record()`` stays a plugin's sole surface, ceiling and all.
CONTROL_ACTIONS: frozenset[str] = frozenset(
    {
        "session_start",
        "artifact_registered",
        "recording_truncated",
    }
)

#: Separate bounded budget for control rows. Bounded so a plugin cannot evade
#: the disk-fill guard by committing artifacts in a loop; a commit that would
#: exceed it fails visibly instead of vanishing.
CONTROL_BUDGET_BYTES = 64 * 1024
```

In `Recorder.__init__`, after `self._truncated = False`:

```python
        # Control rows are budgeted separately from the action ceiling, so a
        # truncated recording still carries its metadata. Counted from zero on
        # every open: the budget bounds one process's writes, not the file.
        self._control_bytes = 0
```

Add the method immediately after `record`:

```python
    def record_control(self, action: str, **fields: Any) -> None:
        """Append a core-owned metadata row, bypassing the action ceiling.

        Raises ``ValueError`` for an action outside :data:`CONTROL_ACTIONS` and
        ``ControlBudgetExceededError`` when the control budget is exhausted.
        """
        if action not in CONTROL_ACTIONS:
            raise ValueError(f"{action!r} is not a control action")
        entry = {"ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "action": action, **fields}
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        encoded = len(line.encode("utf-8"))
        if self._control_bytes + encoded > CONTROL_BUDGET_BYTES:
            raise ControlBudgetExceededError(
                f"control row {action!r} would exceed the {CONTROL_BUDGET_BYTES}-byte control budget"
            )
        self._control_bytes += encoded
        self._fh.write(line)
        self._fh.flush()
        self._event_count += 1
```

Add the import near the top of `recorder.py`, after the `provide.telemetry` import:

```python
from octowright.plugins.errors import ControlBudgetExceededError
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --active pytest tests/test_recorder_control_rows.py -v`
Expected: 4 passed.

- [ ] **Step 6: Verify nothing else regressed**

Run: `uv run --active pytest tests/test_recorder.py tests/terminal -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/octowright/plugins/__init__.py src/octowright/plugins/errors.py \
        src/octowright/recorder.py tests/test_recorder_control_rows.py
git commit -m "feat(recorder): control rows that bypass the byte ceiling

The ceiling exists to bound a firehose page, but record() drops every row
once it trips — so a session_start written under a small ceiling never
lands, and an artifact_registered committed late in a long session is
dropped while commit() reports success. Core metadata rows now go through
record_control on their own bounded budget, generalizing the rule
_write_truncation_marker already applied to its own marker."
```

---

## Task 2: The plugin contract

**Files:**
- Create: `src/octowright/plugins/contract.py`
- Test: `tests/plugins/test_contract.py`

**Interfaces:**
- Consumes: `octowright.plugins.errors.ProtectedSessionCloseError` (Task 1).
- Produces:
  - `PLUGIN_API_VERSION: int` == `1`, `RENDERER_API_VERSION: int` == `1`
  - `CAPABILITIES: frozenset[str]` == `{"macros", "sync", "dialog_policy", "mock_routes"}`
  - `LaunchResult` / `CloseResult` `TypedDict`s
  - `SessionRecord` `Protocol`
  - `SessionPool` `Protocol`
  - `ScenarioAdapter`, `SupportsMacros`, `SupportsSync`, `SupportsDialogPolicy`, `SupportsMockRoutes` (all `@runtime_checkable`)
  - `FrontendAsset` frozen dataclass
  - `SessionKindPlugin` `Protocol`
  - `capabilities_of(adapter: object) -> frozenset[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/__init__.py` (empty file with the SPDX header) and `tests/plugins/test_contract.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.plugins.contract import (
    CAPABILITIES,
    PLUGIN_API_VERSION,
    ScenarioAdapter,
    SupportsMacros,
    SupportsSync,
    capabilities_of,
)


def _shape(proto: type) -> set[str]:
    """Every member a Protocol declares in its own body.

    ``dir()`` is no good here: an annotation-only member like
    ``SessionKindPlugin.kind`` never appears in it.
    """
    annotated = set(getattr(proto, "__annotations__", {}))
    methods = {name for name, value in vars(proto).items() if not name.startswith("_") and callable(value)}
    return annotated | methods


class _FloorOnly:
    def resolve_participant(self, spec, persona):  # noqa: ANN001, ANN201
        return {}


class _WithMacros(_FloorOnly):
    async def run_macro(self, instance_id, *, name, args):  # noqa: ANN001, ANN201
        return None


def test_floor_only_adapter_satisfies_the_base_protocol():
    # The whole point of splitting capabilities into separate Protocols: an
    # adapter implementing only the floor must still type-check as one.
    assert isinstance(_FloorOnly(), ScenarioAdapter)


def test_capabilities_are_derived_not_declared():
    assert capabilities_of(_FloorOnly()) == frozenset()
    assert capabilities_of(_WithMacros()) == frozenset({"macros"})


def test_capability_protocols_are_runtime_checkable():
    assert isinstance(_WithMacros(), SupportsMacros)
    assert not isinstance(_WithMacros(), SupportsSync)


def test_vocabulary_is_closed_and_matches_the_protocols():
    # Guards against a capability Protocol being added without extending the
    # vocabulary, or vice versa — the drift this design exists to prevent.
    assert CAPABILITIES == frozenset({"macros", "sync", "dialog_policy", "mock_routes"})
    assert capabilities_of(object()) == frozenset()


def test_api_version_is_tied_to_the_contract_shape():
    # A contract change that forgets to bump the version fails here: every
    # Protocol's declared member set is spelled out, so adding, renaming, or
    # removing one forces a deliberate edit alongside the version bump.
    from octowright.plugins import contract

    assert PLUGIN_API_VERSION == 1
    assert _shape(contract.SessionPool) == {
        "launch",
        "get",
        "maybe_get",
        "iter_sessions",
        "close",
        "close_all",
    }
    assert _shape(contract.SessionRecord) == {
        "instance_id",
        "kind",
        "label",
        "profile",
        "url",
        "recorder",
        "log_path",
        "protected",
        "extra",
    }
    assert _shape(contract.SessionKindPlugin) == {
        "kind",
        "display_name",
        "plugin_api_version",
        "tool_names",
        "tool_module",
        "profile_name",
        "frontend",
        "create_pool",
        "create_scenario_adapter",
        "session_detail",
    }
    assert _shape(contract.ScenarioAdapter) == {"resolve_participant"}
    assert _shape(contract.SupportsMacros) == {"run_macro"}
    assert _shape(contract.SupportsSync) == {"wait_for_sync"}
    assert _shape(contract.SupportsDialogPolicy) == {"set_dialog_policy"}
    assert _shape(contract.SupportsMockRoutes) == {"install_mock_routes"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_contract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.contract'`.

- [ ] **Step 3: Write the contract module**

Create `src/octowright/plugins/contract.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Structural contract a session-kind plugin implements.

Protocols, not base classes. A plugin implements shapes and never inherits
core's lifecycle assumptions — the same deliberate choice that makes
``TerminalSession`` a parallel dataclass rather than a ``BrowserSession``
subclass.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from octowright.recorder import Recorder

#: Backend contract version. Bumped when any Protocol in this module changes
#: shape. Checked by the loader; a mismatched plugin is refused.
PLUGIN_API_VERSION = 1

#: Frontend renderer contract version. Deliberately separate: collapsing the
#: two makes the dashboard's mismatch fallback unreachable, because a
#: version-mismatched plugin would be refused at load and never reach
#: ``/api/plugins``. Unused until the frontend step; declared here so both
#: versions live in one place.
RENDERER_API_VERSION = 1

#: Closed, core-defined scenario-capability vocabulary. Core must know what a
#: capability means in order to skip it, so a plugin cannot invent one.
#: ``fixtures`` is deliberately absent: ``_validate_fixtures`` accepts exactly
#: ``dialog_policy`` and ``mock_routes``, so a container capability alongside
#: its own constituents means undefined precedence or a double-applied fixture.
CAPABILITIES: frozenset[str] = frozenset({"macros", "sync", "dialog_policy", "mock_routes"})


class LaunchResult(TypedDict, total=False):
    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    log_path: str
    extra: dict[str, Any]


class CloseResult(TypedDict, total=False):
    instance_id: str
    kind: str
    closed: bool
    extra: dict[str, Any]


class SessionRecord(Protocol):
    """One live session owned by a plugin's pool."""

    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    url: str | None
    recorder: Recorder
    log_path: Path
    protected: bool
    extra: dict[str, Any]


class SessionPool(Protocol):
    """The single registry for one kind's live sessions.

    Core keeps no parallel session table: the live list, session detail, and
    close all resolve by iterating registered pools. Instance ids must be
    unique across *all* pools; core enforces that at ``SessionLaunch.commit``.
    """

    async def launch(self, **kwargs: Any) -> LaunchResult: ...

    def get(self, instance_id: str) -> SessionRecord:
        """Return the session, or raise ``KeyError``."""

    def maybe_get(self, instance_id: str) -> SessionRecord | None: ...

    def iter_sessions(self) -> Iterator[SessionRecord]:
        """Iterate a *snapshot* — core iterates while other tasks may launch."""

    async def close(self, instance_id: str, *, force: bool = False) -> CloseResult:
        """Close one session.

        Raises ``KeyError`` for an unknown id and
        ``ProtectedSessionCloseError`` when the session is protected and
        ``force`` is not set.
        """

    async def close_all(self, *, force: bool = False) -> None:
        """Close every session, continuing past individual failures."""


@runtime_checkable
class ScenarioAdapter(Protocol):
    """The mandatory floor for scenario participation."""

    def resolve_participant(self, spec: Any, persona: Any) -> dict[str, Any]: ...


@runtime_checkable
class SupportsMacros(Protocol):
    async def run_macro(self, instance_id: str, *, name: str, args: dict[str, Any]) -> None: ...


@runtime_checkable
class SupportsSync(Protocol):
    async def wait_for_sync(
        self,
        instance_id: str,
        *,
        selector: str | None,
        text: str | None,
        url: str | None,
        timeout_ms: int | None,
    ) -> None: ...


@runtime_checkable
class SupportsDialogPolicy(Protocol):
    async def set_dialog_policy(self, instance_id: str, policy: str) -> None: ...


@runtime_checkable
class SupportsMockRoutes(Protocol):
    async def install_mock_routes(self, instance_id: str, routes: list[dict[str, Any]]) -> None: ...


#: capability name -> the Protocol whose presence *is* the capability. Core
#: derives support from this table; a plugin never declares a capability
#: string, so it cannot claim one it did not implement.
_CAPABILITY_PROTOCOLS: dict[str, type] = {
    "macros": SupportsMacros,
    "sync": SupportsSync,
    "dialog_policy": SupportsDialogPolicy,
    "mock_routes": SupportsMockRoutes,
}


def capabilities_of(adapter: object) -> frozenset[str]:
    """Derive the capability set an adapter actually implements."""
    return frozenset(name for name, proto in _CAPABILITY_PROTOCOLS.items() if isinstance(adapter, proto))


@dataclass(frozen=True)
class FrontendAsset:
    """Prebuilt dashboard UI a plugin ships. Consumed in the frontend step."""

    renderer_api_version: int
    asset_dir: Path
    module_path: str
    layout: Literal["browser", "stream"]


class SessionKindPlugin(Protocol):
    """The package-level descriptor an entry point resolves to.

    Every member except ``create_pool`` / ``create_scenario_adapter`` /
    ``session_detail`` is metadata core validates *before* running plugin
    logic.
    """

    kind: str
    display_name: str
    plugin_api_version: int
    tool_names: frozenset[str]
    tool_module: str | None
    profile_name: str | None
    frontend: FrontendAsset | None

    def create_pool(self, ctx: Any) -> SessionPool: ...

    def create_scenario_adapter(self, pool: SessionPool) -> ScenarioAdapter | None:
        """Build the scenario adapter, or ``None`` if the kind cannot participate.

        A factory rather than an attribute because the adapter resolves
        instance ids against the pool, which does not exist until
        ``create_pool`` has run.
        """

    def session_detail(self, session: SessionRecord) -> dict[str, Any]: ...
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_contract.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/contract.py tests/plugins/__init__.py tests/plugins/test_contract.py
git commit -m "feat(plugins): session-kind contract protocols

Capabilities are separate runtime-checkable Protocols rather than optional
methods on one, because a Protocol that declares a method requires it — a
combined Protocol with optional handlers would reject the very
floor-only adapter its capability derivation depends on. capabilities_of
derives support from the Protocols an adapter satisfies, so a plugin
cannot claim a capability it did not implement."
```

---

## Task 3: Identity and namespace validation

**Files:**
- Create: `src/octowright/plugins/identity.py`
- Test: `tests/plugins/test_identity.py`

**Interfaces:**
- Consumes: `octowright.plugins.errors.PluginLoadError` (Task 1).
- Produces:
  - `NAME_RE: re.Pattern[str]`
  - `RESERVED_KINDS: frozenset[str]`
  - `validate_name(value: str, *, label: str) -> None`
  - `validate_kind(kind: str) -> None`
  - `validate_tool_names(kind: str, tool_names: frozenset[str]) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_identity.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.plugins.errors import PluginLoadError
from octowright.plugins.identity import validate_kind, validate_name, validate_tool_names


@pytest.mark.parametrize("value", ["terminal", "a", "my-kind", "my_kind2", "a" * 64])
def test_valid_names(value):
    validate_name(value, label="entry-point name")


@pytest.mark.parametrize(
    "value",
    ["", "Terminal", "2fast", "-lead", "has space", "has/slash", "a" * 65, "with.dot"],
)
def test_invalid_names(value):
    with pytest.raises(PluginLoadError, match="entry-point name"):
        validate_name(value, label="entry-point name")


@pytest.mark.parametrize("kind", ["chromium", "firefox", "webkit", "browser", "unknown", "session"])
def test_reserved_kinds_are_refused(kind):
    with pytest.raises(PluginLoadError, match="reserved"):
        validate_kind(kind)


def test_non_reserved_kind_passes():
    validate_kind("refkind")


def test_tool_names_must_carry_the_kind_prefix():
    validate_tool_names("refkind", frozenset({"refkind_launch", "refkind_close"}))
    with pytest.raises(PluginLoadError, match="must start with 'refkind_'"):
        validate_tool_names("refkind", frozenset({"browser_launch"}))


def test_empty_tool_names_is_allowed():
    validate_tool_names("refkind", frozenset())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.identity'`.

- [ ] **Step 3: Write the identity module**

Create `src/octowright/plugins/identity.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Namespace rules for plugin identifiers.

Several identifiers flow into URLs, recording metadata, status output, and
lookup dictionaries, so they are validated before any plugin logic runs. The
entry-point *name* is the configured identity — what an operator writes in
``OCTOWRIGHT_PLUGINS`` and what appears in the asset route — while ``kind`` is
runtime metadata stamped into recordings.
"""

from __future__ import annotations

import re

from octowright.plugins.errors import PluginLoadError

#: One safe syntax for every plugin identifier: lowercase, starts with a
#: letter, no separators that could escape a path segment or a URL segment.
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

#: Kinds core owns. A plugin claiming one would shadow a browser engine in the
#: registry, or collide with the ``unknown`` classification closed-session
#: discovery emits for a recording it cannot identify.
RESERVED_KINDS: frozenset[str] = frozenset({"chromium", "firefox", "webkit", "browser", "unknown", "session"})


def validate_name(value: str, *, label: str) -> None:
    """Raise ``PluginLoadError`` unless ``value`` matches :data:`NAME_RE`."""
    if not NAME_RE.match(value):
        raise PluginLoadError(f"{label} {value!r} must match {NAME_RE.pattern}")


def validate_kind(kind: str) -> None:
    """Validate a session kind's syntax and reject reserved names."""
    validate_name(kind, label="plugin kind")
    if kind in RESERVED_KINDS:
        raise PluginLoadError(f"plugin kind {kind!r} is reserved by core")


def validate_tool_names(kind: str, tool_names: frozenset[str]) -> None:
    """Require every declared tool name to carry the ``{kind}_`` prefix.

    Enforced rather than advisory: it makes the cross-plugin collision check a
    fast path and keeps a third-party tool from squatting a name core may
    want later.
    """
    prefix = f"{kind}_"
    for name in sorted(tool_names):
        if not name.startswith(prefix):
            raise PluginLoadError(f"plugin tool {name!r} must start with {prefix!r}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_identity.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/identity.py tests/plugins/test_identity.py
git commit -m "feat(plugins): identifier namespace validation

Entry-point name is the configured identity; kind is runtime metadata.
Both share one syntax, reserved kinds are refused, and declared tool names
must carry the {kind}_ prefix so a third-party tool cannot squat a name
core may want."
```

---

## Task 4: The launch transaction

The transaction is the only way a plugin can obtain a `Recorder`, which is what makes the recording guarantees structural rather than a documented obligation.

**Files:**
- Create: `src/octowright/plugins/session_launch.py`
- Test: `tests/plugins/test_session_launch.py`

**Interfaces:**
- Consumes: `Recorder`, `new_log_path`, `CONTROL_ACTIONS` (Task 1); `LaunchResult`, `SessionRecord` (Task 2); `SessionIdInUseError` (Task 1).
- Produces:
  - `PluginContext(kind, recordings_dir, id_in_use, log)` — dataclass with `begin_session`, `redaction_mode`
  - `SessionLaunch` with `.recorder`, `.log_path`, `.instance_id`, `.kind`, `.commit(record) -> LaunchResult`
  - `PluginContext.begin_session(*, instance_id, label, profile, extra=None)` — async context manager

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_session_launch.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from octowright.plugins.errors import SessionIdInUseError
from octowright.plugins.session_launch import PluginContext
from octowright.recorder import Recorder


@dataclass
class _Record:
    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    url: str | None
    recorder: Recorder
    log_path: Path
    protected: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


def _ctx(tmp_path: Path, *, in_use: set[str] | None = None) -> PluginContext:
    taken = in_use or set()
    return PluginContext(
        kind="refkind",
        recordings_dir=tmp_path,
        id_in_use=lambda instance_id: instance_id in taken,
    )


def _actions(path: Path) -> list[str]:
    return [json.loads(line)["action"] for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_opening_row_is_written_with_kind_label_and_profile(tmp_path):
    ctx = _ctx(tmp_path)
    async with ctx.begin_session(instance_id="abc123", label="demo", profile="tanuki") as launch:
        record = _Record("abc123", "refkind", "demo", "tanuki", None, launch.recorder, launch.log_path)
        result = launch.commit(record)

    assert result["instance_id"] == "abc123"
    assert result["kind"] == "refkind"
    rows = [json.loads(line) for line in launch.log_path.read_text().splitlines() if line.strip()]
    assert rows[0]["action"] == "session_start"
    assert rows[0]["kind"] == "refkind"
    assert rows[0]["label"] == "demo"
    assert rows[0]["profile"] == "tanuki"


@pytest.mark.asyncio
async def test_failed_launch_discards_an_opening_row_only_recording(tmp_path):
    ctx = _ctx(tmp_path)
    log_path: Path | None = None
    with pytest.raises(RuntimeError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            log_path = launch.log_path
            raise RuntimeError("connector refused")

    assert log_path is not None
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_failed_launch_keeps_a_partial_recording(tmp_path):
    ctx = _ctx(tmp_path)
    log_path: Path | None = None
    with pytest.raises(RuntimeError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            log_path = launch.log_path
            launch.recorder.record("terminal_output", data="boot")
            raise RuntimeError("died mid-boot")

    assert log_path is not None
    assert _actions(log_path) == ["session_start", "terminal_output"]


@pytest.mark.asyncio
async def test_cancellation_behaves_as_a_failed_launch(tmp_path):
    import asyncio

    ctx = _ctx(tmp_path)
    log_path: Path | None = None
    with pytest.raises(asyncio.CancelledError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            log_path = launch.log_path
            raise asyncio.CancelledError

    assert log_path is not None
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_exiting_without_commit_is_a_failure(tmp_path):
    ctx = _ctx(tmp_path)
    async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
        log_path = launch.log_path
    assert not log_path.exists()


@pytest.mark.asyncio
async def test_commit_refuses_a_mismatched_record(tmp_path):
    ctx = _ctx(tmp_path)
    with pytest.raises(ValueError, match="does not match the transaction"):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            other = Recorder(tmp_path / "other.jsonl")
            record = _Record("abc123", "refkind", None, None, None, other, launch.log_path)
            launch.commit(record)


@pytest.mark.asyncio
async def test_commit_enforces_cross_pool_id_uniqueness(tmp_path):
    ctx = _ctx(tmp_path, in_use={"abc123"})
    with pytest.raises(SessionIdInUseError):
        async with ctx.begin_session(instance_id="abc123", label=None, profile=None) as launch:
            record = _Record("abc123", "refkind", None, None, None, launch.recorder, launch.log_path)
            launch.commit(record)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_session_launch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.session_launch'`.

- [ ] **Step 3: Write the launch transaction**

Create `src/octowright/plugins/session_launch.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Core-owned launch transaction.

A plugin cannot open a recording. It asks for a transaction, records into the
recorder the transaction hands it, and commits. That is what makes the disk
guarantees — 0600 under a 0700 parent, containment, the byte ceiling, the
failed-launch rule — structural rather than a documented obligation on the
plugin author.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.contract import LaunchResult, SessionRecord
from octowright.plugins.errors import SessionIdInUseError
from octowright.recorder import Recorder, new_log_path

_LOG = get_logger("octowright.plugins.launch")

#: A recording holding nothing but this is an orphan of a failed launch and is
#: deleted. Anything the plugin recorded — even one row — is kept: a real if
#: orphaned recording beats destroying diagnostic data. Content, not size, is
#: the test, because core writes ``session_start`` before the plugin runs, so
#: the file is never zero bytes by the time a launch can fail.
_OPENING_ONLY: frozenset[str] = frozenset({"session_start"})


@dataclass
class SessionLaunch:
    """One in-flight launch. Yielded by :meth:`PluginContext.begin_session`."""

    recorder: Recorder
    log_path: Path
    instance_id: str
    kind: str
    _id_in_use: Callable[[str], bool]
    _committed: bool = False
    _result: LaunchResult | None = None

    def commit(self, record: SessionRecord) -> LaunchResult:
        """Validate and finalize. The plugin's own pool holds ``record``.

        Core keeps no parallel session table, so this does not register the
        record anywhere — it checks that the record is the one this
        transaction issued, enforces cross-pool id uniqueness, and marks the
        transaction successful.
        """
        if (
            record.instance_id != self.instance_id
            or record.kind != self.kind
            or record.recorder is not self.recorder
            or Path(record.log_path) != self.log_path
        ):
            raise ValueError(
                f"committed record for {record.instance_id!r} does not match the transaction "
                f"({self.instance_id!r}/{self.kind!r})"
            )
        if self._id_in_use(self.instance_id):
            raise SessionIdInUseError(
                f"instance_id {self.instance_id!r} is already held by another registered pool"
            )
        self._committed = True
        self._result = LaunchResult(
            instance_id=self.instance_id,
            kind=self.kind,
            label=record.label,
            profile=record.profile,
            log_path=str(self.log_path),
        )
        return self._result


@dataclass
class PluginContext:
    """What a plugin is handed at ``create_pool``."""

    kind: str
    recordings_dir: Path
    id_in_use: Callable[[str], bool]
    log: Any = field(default_factory=lambda: _LOG)

    def redaction_mode(self) -> str:
        """The resolved ``OCTOWRIGHT_REDACT_INPUTS`` policy.

        Plugins are handed the resolved policy and never read the environment
        themselves — the same reasoning as ``redact_headers_for_report``
        flooring at ``passwords`` rather than trusting a caller.
        """
        raw = os.environ.get("OCTOWRIGHT_REDACT_INPUTS", "").strip().lower()
        return raw if raw in {"off", "passwords", "all"} else "passwords"

    @contextlib.asynccontextmanager
    async def begin_session(
        self,
        *,
        instance_id: str,
        label: str | None,
        profile: str | None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[SessionLaunch]:
        """Open a recording, write the opening row, and guard the launch.

        Takes no ``kind``: the context already holds the validated descriptor
        kind, so accepting one would let a plugin stamp a recording with a
        kind core never approved.
        """
        log_path = new_log_path(self.recordings_dir, instance_id, label, self.kind)
        recorder = Recorder(log_path)
        recorder.record_control(
            "session_start",
            kind=self.kind,
            label=label,
            profile=profile,
            **(extra or {}),
        )
        launch = SessionLaunch(
            recorder=recorder,
            log_path=log_path,
            instance_id=instance_id,
            kind=self.kind,
            _id_in_use=self.id_in_use,
        )
        try:
            yield launch
        except BaseException:
            _discard_failed_launch(recorder, log_path)
            raise
        if not launch._committed:
            # A block that returns without committing did not produce a
            # session; treat it exactly as a raised launch.
            _discard_failed_launch(recorder, log_path)


def _discard_failed_launch(recorder: Recorder, log_path: Path) -> None:
    """Close the recorder and drop the file if it holds only core's opening row."""
    recorder.close()
    with contextlib.suppress(OSError):
        if not log_path.exists():
            return
        actions = set()
        for raw in log_path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                actions.add(json.loads(raw).get("action"))
            except json.JSONDecodeError:
                # An unparsable line is data we did not write; keep the file.
                return
        if actions and actions <= _OPENING_ONLY:
            log_path.unlink()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_session_launch.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/session_launch.py tests/plugins/test_session_launch.py
git commit -m "feat(plugins): core-owned launch transaction

begin_session is the only source of a Recorder, so the recording
guarantees are structural rather than a documented obligation. The
failed-launch rule tests content, not size: core writes session_start
before the plugin runs, so the old st_size == 0 heuristic would leave an
opening-row-only orphan behind every failed launch. commit validates the
record against the transaction and enforces cross-pool id uniqueness
instead of requiring it and declining to check."
```

---

## Task 5: Metadata-only discovery and enable resolution

**Files:**
- Create: `src/octowright/plugins/discovery.py`
- Test: `tests/plugins/test_discovery.py`, `tests/plugins/_import_probe.py`

**Interfaces:**
- Consumes: `PluginLoadError`, `DuplicatePluginNameError` (Task 1); `validate_name` (Task 3).
- Produces:
  - `ENTRY_POINT_GROUP: str` == `"octowright.session_kinds"`
  - `DiscoveredPlugin` frozen dataclass: `name`, `distribution`, `version`, `entry_point`, `ep`, plus `status_row(state)`
  - `discover(entry_points=None) -> list[DiscoveredPlugin]`
  - `enabled_names(*, env=None, config_path=None) -> list[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/_import_probe.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Module-scope counter proving a disabled plugin is never imported."""

from __future__ import annotations

IMPORTS = 0
IMPORTS += 1

MARKER = "imported"
```

Create `tests/plugins/test_discovery.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys
from importlib.metadata import EntryPoint

import pytest

from octowright.plugins.discovery import DiscoveredPlugin, discover, enabled_names
from octowright.plugins.errors import DuplicatePluginNameError


def _ep(name: str, value: str = "tests.plugins._import_probe:MARKER") -> EntryPoint:
    return EntryPoint(name=name, value=value, group="octowright.session_kinds")


def test_discovery_reports_metadata_without_importing(monkeypatch):
    sys.modules.pop("tests.plugins._import_probe", None)
    found = discover(entry_points=[_ep("refkind")])

    assert [p.name for p in found] == ["refkind"]
    assert found[0].entry_point == "tests.plugins._import_probe:MARKER"
    # The whole trust boundary: discovery must not execute plugin code.
    assert "tests.plugins._import_probe" not in sys.modules


def test_duplicate_entry_point_names_are_refused():
    with pytest.raises(DuplicatePluginNameError, match="refkind"):
        discover(entry_points=[_ep("refkind"), _ep("refkind")])


def test_invalid_entry_point_name_is_skipped_not_fatal():
    # One malformed package must not take out discovery for every other one.
    found = discover(entry_points=[_ep("Bad Name"), _ep("refkind")])
    assert [p.name for p in found] == ["refkind"]


def test_env_var_wins_over_config(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("plugins:\n  - fromfile\n")
    assert enabled_names(env={"OCTOWRIGHT_PLUGINS": "fromenv"}, config_path=cfg) == ["fromenv"]


def test_config_file_used_when_env_unset(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("plugins:\n  - fromfile\n  - second\n")
    assert enabled_names(env={}, config_path=cfg) == ["fromfile", "second"]


def test_nothing_enabled_by_default(tmp_path):
    assert enabled_names(env={}, config_path=tmp_path / "absent.yaml") == []


def test_malformed_config_is_not_fatal(tmp_path):
    cfg = tmp_path / "plugins.yaml"
    cfg.write_text("plugins: not-a-list\n")
    assert enabled_names(env={}, config_path=cfg) == []


def test_project_config_does_not_enable_plugins(tmp_path, monkeypatch):
    # Enable is daemon-scoped. `.octowright/config.yaml` is found by walking up
    # from CWD, so honouring it here would make the MCP tool surface depend on
    # which directory the daemon was spawned in.
    project = tmp_path / "proj" / ".octowright"
    project.mkdir(parents=True)
    (project / "config.yaml").write_text("plugins:\n  - refkind\n")
    monkeypatch.chdir(tmp_path / "proj")

    assert enabled_names(env={}, config_path=tmp_path / "absent.yaml") == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_discovery.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.discovery'`.

- [ ] **Step 3: Write the discovery module**

Create `src/octowright/plugins/discovery.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Entry-point discovery and daemon-scoped enable resolution.

Discovery is deliberately **metadata only**. ``importlib.metadata`` yields an
entry point's name, target, and owning distribution without importing it, and
that is the whole trust boundary: installing a package — including a
transitive dependency — must not silently extend a browser-driving daemon.
Resolving the descriptor (and with it ``kind`` and ``plugin_api_version``)
requires an import, so those fields exist only for a plugin an operator
explicitly enabled.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint
from importlib.metadata import entry_points as _entry_points
from pathlib import Path

import yaml
from provide.telemetry import get_logger

from octowright import config_paths
from octowright.plugins.errors import DuplicatePluginNameError, PluginLoadError
from octowright.plugins.identity import validate_name

log = get_logger("octowright.plugins.discovery")

ENTRY_POINT_GROUP = "octowright.session_kinds"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """What core knows about a plugin before deciding to load it."""

    name: str
    distribution: str | None
    version: str | None
    entry_point: str
    ep: EntryPoint

    def status_row(self, state: str) -> dict[str, object]:
        """The status shape for a plugin whose descriptor has not been resolved."""
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "entry_point": self.entry_point,
            "state": state,
        }


def discover(entry_points: Iterable[EntryPoint] | None = None) -> list[DiscoveredPlugin]:
    """Enumerate installed session-kind plugins without importing any of them.

    ``entry_points`` is injectable so tests can supply fakes; production passes
    nothing and reads the real group.
    """
    eps = list(entry_points) if entry_points is not None else list(_entry_points(group=ENTRY_POINT_GROUP))
    found: dict[str, DiscoveredPlugin] = {}
    for ep in eps:
        try:
            validate_name(ep.name, label="entry-point name")
        except PluginLoadError as exc:
            # A malformed name is one bad package, not a broken daemon.
            log.warning("octowright.plugins.bad_entry_point_name", name=ep.name, error=str(exc))
            continue
        if ep.name in found:
            raise DuplicatePluginNameError(
                f"two distributions declare the {ENTRY_POINT_GROUP} entry point {ep.name!r}; "
                "resolving by enumeration order would vary by machine"
            )
        dist = getattr(ep, "dist", None)
        found[ep.name] = DiscoveredPlugin(
            name=ep.name,
            distribution=getattr(dist, "name", None),
            version=getattr(dist, "version", None),
            entry_point=ep.value,
            ep=ep,
        )
    return [found[name] for name in sorted(found)]


def default_config_path() -> Path:
    """User-level plugin config. ``user_config_dir()`` already ends in ``octowright``."""
    return config_paths.user_config_dir() / "plugins.yaml"


def enabled_names(
    *,
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> list[str]:
    """Resolve which plugins an operator enabled, by entry-point name.

    Daemon-scoped on purpose. ``.octowright/config.yaml`` is found by walking
    up from CWD, so enabling plugins there would make the MCP tool surface
    depend on which directory the daemon happened to be spawned in.
    """
    source = env if env is not None else dict(os.environ)
    raw = source.get("OCTOWRIGHT_PLUGINS", "").strip()
    if raw:
        return [part.strip() for part in raw.split(",") if part.strip()]

    path = config_path if config_path is not None else default_config_path()
    if not path.exists():
        return []
    with contextlib.suppress(OSError, yaml.YAMLError):
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        plugins = loaded.get("plugins") if isinstance(loaded, dict) else None
        if isinstance(plugins, list):
            return [str(item).strip() for item in plugins if str(item).strip()]
        log.warning("octowright.plugins.bad_config", path=str(path))
    return []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_discovery.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/discovery.py tests/plugins/test_discovery.py tests/plugins/_import_probe.py
git commit -m "feat(plugins): metadata-only discovery and daemon-scoped enable

Entry-point metadata yields name, target, and distribution without an
import; kind and plugin_api_version need one, so they exist only for a
plugin an operator enabled. Enable resolves from OCTOWRIGHT_PLUGINS then
a user-level plugins.yaml — never the CWD-walked project config, which
would make the tool surface depend on the daemon's spawn directory."
```

---

## Task 6: The plugin registry

**Files:**
- Create: `src/octowright/plugins/registry.py`
- Test: `tests/plugins/test_registry.py`

**Interfaces:**
- Consumes: `SessionPool`, `SessionRecord`, `SessionKindPlugin`, `capabilities_of` (Task 2).
- Produces:
  - `LoadedPlugin` frozen dataclass: `descriptor`, `pool`, `adapter`, `capabilities`, `discovered`
  - `PluginRegistry` with `register`, `record_failure`, `record_state`, `pools()`, `kinds()`, `get_plugin(kind)`, `maybe_get(instance_id)`, `id_in_use(instance_id)`, `status_rows()`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_registry.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from octowright.plugins.registry import PluginRegistry


@dataclass
class _FakeSession:
    instance_id: str
    kind: str = "refkind"


@dataclass
class _FakePool:
    sessions: dict[str, _FakeSession] = field(default_factory=dict)

    def maybe_get(self, instance_id: str) -> _FakeSession | None:
        return self.sessions.get(instance_id)

    def iter_sessions(self):  # noqa: ANN201
        return iter(list(self.sessions.values()))


class _FakeDescriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = 1
    tool_names = frozenset({"refkind_launch"})
    tool_module = None
    profile_name = "refkinds"
    frontend = None

    def create_pool(self, ctx: Any) -> Any:
        return _FakePool()

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


def test_registered_plugin_is_reachable_by_kind():
    reg = PluginRegistry()
    pool = _FakePool()
    reg.register(_FakeDescriptor(), pool=pool, adapter=None, discovered=None)

    assert reg.kinds() == ["refkind"]
    assert reg.pools() == {"refkind": pool}
    assert reg.get_plugin("refkind").pool is pool


def test_id_lookup_spans_every_pool():
    reg = PluginRegistry()
    pool = _FakePool({"abc": _FakeSession("abc")})
    reg.register(_FakeDescriptor(), pool=pool, adapter=None, discovered=None)

    assert reg.id_in_use("abc") is True
    assert reg.id_in_use("zzz") is False
    assert reg.maybe_get("abc").instance_id == "abc"
    assert reg.maybe_get("zzz") is None


def test_status_rows_carry_enabled_and_failed_states():
    reg = PluginRegistry()
    reg.register(_FakeDescriptor(), pool=_FakePool(), adapter=None, discovered=None)
    reg.record_failure(name="broken", reason="boom", discovered=None)
    reg.record_state(name="ghost", state="missing")

    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "enabled"
    assert rows["refkind"]["kind"] == "refkind"
    assert rows["refkind"]["plugin_api_version"] == 1
    assert rows["broken"]["state"] == "failed"
    assert rows["broken"]["reason"] == "boom"
    # A plugin that raised while importing its own module has no descriptor.
    assert "kind" not in rows["broken"]
    assert rows["ghost"]["state"] == "missing"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.registry'`.

- [ ] **Step 3: Write the registry**

Create `src/octowright/plugins/registry.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""The live registry of enabled session kinds.

Core keeps no parallel session table: a plugin's ``SessionPool`` is the single
registry for its kind, and this object is the map from kind to pool plus the
status ledger for every plugin core knows about, whatever its state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.contract import SessionKindPlugin, SessionPool, SessionRecord, capabilities_of
from octowright.plugins.discovery import DiscoveredPlugin

log = get_logger("octowright.plugins.registry")


@dataclass(frozen=True)
class LoadedPlugin:
    """An enabled plugin and everything core built from it."""

    descriptor: SessionKindPlugin
    pool: SessionPool
    adapter: Any
    capabilities: frozenset[str]
    discovered: DiscoveredPlugin | None


@dataclass
class PluginRegistry:
    """Kind → loaded plugin, plus a status row for every plugin core saw."""

    _loaded: dict[str, LoadedPlugin] = field(default_factory=dict)
    _states: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(
        self,
        descriptor: SessionKindPlugin,
        *,
        pool: SessionPool,
        adapter: Any,
        discovered: DiscoveredPlugin | None,
    ) -> LoadedPlugin:
        loaded = LoadedPlugin(
            descriptor=descriptor,
            pool=pool,
            adapter=adapter,
            capabilities=capabilities_of(adapter) if adapter is not None else frozenset(),
            discovered=discovered,
        )
        self._loaded[descriptor.kind] = loaded
        row: dict[str, Any] = dict(discovered.status_row("enabled")) if discovered else {"state": "enabled"}
        row.update(
            {
                "name": discovered.name if discovered else descriptor.kind,
                "kind": descriptor.kind,
                "display_name": descriptor.display_name,
                "plugin_api_version": descriptor.plugin_api_version,
                "tool_names": sorted(descriptor.tool_names),
                "capabilities": sorted(loaded.capabilities),
            }
        )
        self._states[row["name"]] = row
        return loaded

    def record_failure(
        self,
        *,
        name: str,
        reason: str,
        discovered: DiscoveredPlugin | None,
        descriptor: SessionKindPlugin | None = None,
    ) -> None:
        """Record a failed load.

        Descriptor fields are optional here on purpose: a plugin that raised
        while importing its own module has no descriptor to report, and that
        is the earliest and most common failure.
        """
        row: dict[str, Any] = dict(discovered.status_row("failed")) if discovered else {"name": name, "state": "failed"}
        row["name"] = name
        row["state"] = "failed"
        row["reason"] = reason
        if descriptor is not None:
            row["kind"] = descriptor.kind
            row["display_name"] = descriptor.display_name
            row["plugin_api_version"] = descriptor.plugin_api_version
            row["tool_names"] = sorted(descriptor.tool_names)
        self._states[name] = row

    def record_state(self, *, name: str, state: str, discovered: DiscoveredPlugin | None = None) -> None:
        """Record a non-loading state — ``disabled`` or ``missing``."""
        row: dict[str, Any] = dict(discovered.status_row(state)) if discovered else {"name": name, "state": state}
        self._states[name] = row

    def kinds(self) -> list[str]:
        return sorted(self._loaded)

    def pools(self) -> dict[str, SessionPool]:
        return {kind: loaded.pool for kind, loaded in self._loaded.items()}

    def get_plugin(self, kind: str) -> LoadedPlugin:
        return self._loaded[kind]

    def maybe_get(self, instance_id: str) -> SessionRecord | None:
        """Resolve a session by id across every registered pool."""
        for loaded in self._loaded.values():
            found = loaded.pool.maybe_get(instance_id)
            if found is not None:
                return found
        return None

    def id_in_use(self, instance_id: str) -> bool:
        return self.maybe_get(instance_id) is not None

    def status_rows(self) -> list[dict[str, Any]]:
        return [self._states[name] for name in sorted(self._states)]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_registry.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/registry.py tests/plugins/test_registry.py
git commit -m "feat(plugins): kind registry with a status ledger

The pool is the single session registry, so this maps kind to pool and
resolves an id across every pool rather than duplicating a session table.
Failed rows omit descriptor fields because a plugin that raised during its
own import has none to report."
```

---

## Task 7: The load transaction with delta rollback

**Files:**
- Create: `src/octowright/plugins/loader.py`
- Test: `tests/plugins/test_loader.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces:
  - `ResolvedDescriptor` frozen dataclass: `discovered`, `descriptor`
  - `resolve_descriptors(*, registry, discovered, enabled) -> list[ResolvedDescriptor]`
  - `activate(*, registry, resolved, ctx_factory, tool_manager) -> None`
  - `ToolDelta` helper: `snapshot(tool_manager) -> set[str]`, `remove(tool_manager, names) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_loader.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from octowright.plugins.contract import PLUGIN_API_VERSION
from octowright.plugins.discovery import DiscoveredPlugin
from octowright.plugins.loader import activate, resolve_descriptors
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext


class _FakeToolManager:
    def __init__(self) -> None:
        self._tools: dict[str, object] = {"browser_launch": object()}


class _Descriptor:
    kind = "refkind"
    display_name = "Reference Kind"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = frozenset({"refkind_launch"})
    tool_module = None
    profile_name = None
    frontend = None

    def __init__(self, **overrides: Any) -> None:
        for key, value in overrides.items():
            setattr(self, key, value)

    def create_pool(self, ctx: Any) -> Any:
        return _Pool()

    def create_scenario_adapter(self, pool: Any) -> Any:
        return None

    def session_detail(self, session: Any) -> dict[str, Any]:
        return {}


class _Pool:
    def maybe_get(self, instance_id: str) -> None:
        return None

    def iter_sessions(self):  # noqa: ANN201
        return iter(())

    async def close_all(self, *, force: bool = False) -> None:
        return None


def _discovered(name: str = "refkind") -> DiscoveredPlugin:
    return DiscoveredPlugin(name=name, distribution="d", version="1", entry_point="m:p", ep=_FakeEP())


class _FakeEP:
    name = "refkind"
    value = "m:p"

    def __init__(self, target: Any = None, raises: BaseException | None = None) -> None:
        self._target = target
        self._raises = raises

    def load(self) -> Any:
        if self._raises is not None:
            raise self._raises
        return self._target


def _ctx_factory(kind: str, registry: PluginRegistry, tmp_path) -> PluginContext:  # noqa: ANN001
    return PluginContext(kind=kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)


def test_disabled_plugin_is_never_loaded():
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor())
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=[])

    assert resolved == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "disabled"
    assert "kind" not in rows["refkind"]


def test_enabled_name_with_no_entry_point_reports_missing():
    reg = PluginRegistry()
    resolve_descriptors(registry=reg, discovered=[], enabled=["typo"])
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["typo"]["state"] == "missing"


def test_api_version_mismatch_is_refused():
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(plugin_api_version=PLUGIN_API_VERSION + 1))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    assert resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"]) == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"
    assert "plugin_api_version" in rows["refkind"]["reason"]


def test_descriptor_import_failure_reports_without_descriptor_fields():
    reg = PluginRegistry()
    ep = _FakeEP(raises=RuntimeError("bad import"))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    assert resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"]) == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"
    assert "bad import" in rows["refkind"]["reason"]
    assert "kind" not in rows["refkind"]


def test_tool_name_colliding_with_core_is_refused_before_import():
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(kind="browser", tool_names=frozenset({"browser_launch"})))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)

    assert resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"]) == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert "reserved" in rows["refkind"]["reason"]


def test_activate_registers_the_pool(tmp_path):
    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor())
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=_FakeToolManager(),
    )

    assert reg.kinds() == ["refkind"]


def test_rollback_removes_the_actual_delta_not_the_declaration(tmp_path):
    # A module that registers an UNDECLARED tool and then raises: rolling back
    # by declared name alone would leak it.
    manager = _FakeToolManager()
    module_name = "octowright_test_rogue_tool_module"

    def _install() -> None:
        module = types.ModuleType(module_name)

        def _side_effect() -> None:
            manager._tools["refkind_launch"] = object()
            manager._tools["refkind_undeclared"] = object()
            raise RuntimeError("registered then died")

        module.__dict__["_boot"] = _side_effect
        sys.modules[module_name] = module
        _side_effect()

    reg = PluginRegistry()
    ep = _FakeEP(target=_Descriptor(tool_module=module_name))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=lambda name: _install(),
    )

    assert "refkind_launch" not in manager._tools
    assert "refkind_undeclared" not in manager._tools
    assert manager._tools.keys() == {"browser_launch"}
    assert reg.kinds() == []
    rows = {row["name"]: row for row in reg.status_rows()}
    assert rows["refkind"]["state"] == "failed"


def test_create_pool_failure_rolls_back_tools(tmp_path):
    manager = _FakeToolManager()

    class _BadPoolDescriptor(_Descriptor):
        def create_pool(self, ctx: Any) -> Any:
            raise RuntimeError("pool refused")

    reg = PluginRegistry()
    ep = _FakeEP(target=_BadPoolDescriptor(tool_module="whatever"))
    found = DiscoveredPlugin(name="refkind", distribution="d", version="1", entry_point="m:p", ep=ep)
    resolved = resolve_descriptors(registry=reg, discovered=[found], enabled=["refkind"])

    def _register(name: str) -> None:
        manager._tools["refkind_launch"] = object()

    activate(
        registry=reg,
        resolved=resolved,
        ctx_factory=lambda kind: _ctx_factory(kind, reg, tmp_path),
        tool_manager=manager,
        import_module=_register,
    )

    assert manager._tools.keys() == {"browser_launch"}
    assert reg.kinds() == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octowright.plugins.loader'`.

- [ ] **Step 3: Write the loader**

Create `src/octowright/plugins/loader.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Two-phase plugin load with rollback.

Phase one resolves descriptors and validates metadata. It must run **before**
the profile filter is computed, because a plugin's capability profile has to
be registered before any ``@mcp.tool`` decorator fires. Phase two imports the
plugin's tool module and builds its pool.

A plugin loads completely or not at all. Partial load is the failure mode
worth designing against: a plugin whose tools registered but whose pool does
not exist would answer MCP calls with internal errors forever.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from provide.telemetry import get_logger

from octowright.plugins.contract import PLUGIN_API_VERSION, SessionKindPlugin
from octowright.plugins.discovery import DiscoveredPlugin
from octowright.plugins.identity import validate_kind, validate_tool_names
from octowright.plugins.registry import PluginRegistry

log = get_logger("octowright.plugins.loader")


@dataclass(frozen=True)
class ResolvedDescriptor:
    discovered: DiscoveredPlugin
    descriptor: SessionKindPlugin


def _tool_names(tool_manager: Any) -> set[str]:
    """Snapshot the tool manager's registered names.

    Reaches into the SDK's private mapping. Narrow and deliberate: rolling back
    by declared name alone would leak an *undeclared* tool a module registered
    before raising, which is the one case rollback exists for.
    """
    return set(getattr(tool_manager, "_tools", {}))


def _remove_tools(tool_manager: Any, names: Iterable[str]) -> None:
    tools = getattr(tool_manager, "_tools", None)
    if tools is None:  # pragma: no cover - defensive
        return
    for name in names:
        tools.pop(name, None)


def resolve_descriptors(
    *,
    registry: PluginRegistry,
    discovered: list[DiscoveredPlugin],
    enabled: list[str],
    tool_manager: Any | None = None,
) -> list[ResolvedDescriptor]:
    """Import and validate descriptors for the enabled plugins only.

    Everything a *disabled* plugin reports comes from metadata; resolving its
    descriptor would execute exactly the code explicit enable exists to gate.
    """
    by_name = {found.name: found for found in discovered}
    for found in discovered:
        if found.name not in enabled:
            registry.record_state(name=found.name, state="disabled", discovered=found)

    core_tools = _tool_names(tool_manager) if tool_manager is not None else set()
    claimed: set[str] = set()
    resolved: list[ResolvedDescriptor] = []

    for name in enabled:
        found = by_name.get(name)
        if found is None:
            registry.record_state(name=name, state="missing")
            log.warning("octowright.plugins.enabled_but_not_installed", name=name)
            continue
        try:
            descriptor = found.ep.load()
        except Exception as exc:  # noqa: BLE001 - a bad package must not kill the daemon
            registry.record_failure(name=name, reason=f"descriptor import failed: {exc!r}", discovered=found)
            log.warning("octowright.plugins.descriptor_import_failed", name=name, error=repr(exc))
            continue
        try:
            _validate(descriptor, core_tools=core_tools, claimed=claimed)
        except Exception as exc:  # noqa: BLE001
            registry.record_failure(
                name=name, reason=str(exc), discovered=found, descriptor=_safe_descriptor(descriptor)
            )
            log.warning("octowright.plugins.validation_failed", name=name, error=str(exc))
            continue
        claimed |= set(descriptor.tool_names)
        resolved.append(ResolvedDescriptor(discovered=found, descriptor=descriptor))

    return resolved


def _safe_descriptor(descriptor: Any) -> SessionKindPlugin | None:
    """Return the descriptor only if its metadata is readable enough to report."""
    required = ("kind", "display_name", "plugin_api_version", "tool_names")
    return descriptor if all(hasattr(descriptor, attr) for attr in required) else None


def _validate(descriptor: Any, *, core_tools: set[str], claimed: set[str]) -> None:
    if getattr(descriptor, "plugin_api_version", None) != PLUGIN_API_VERSION:
        raise ValueError(
            f"plugin_api_version {getattr(descriptor, 'plugin_api_version', None)!r} "
            f"does not match core's {PLUGIN_API_VERSION}"
        )
    validate_kind(descriptor.kind)
    validate_tool_names(descriptor.kind, frozenset(descriptor.tool_names))
    collisions = set(descriptor.tool_names) & (core_tools | claimed)
    if collisions:
        raise ValueError(f"tool name collision: {sorted(collisions)}")


def activate(
    *,
    registry: PluginRegistry,
    resolved: list[ResolvedDescriptor],
    ctx_factory: Callable[[str], Any],
    tool_manager: Any,
    import_module: Callable[[str], Any] | None = None,
) -> None:
    """Import each plugin's tool module and build its pool, or roll back."""
    importer = import_module or importlib.import_module

    for item in resolved:
        descriptor = item.descriptor
        before = _tool_names(tool_manager)
        delta: set[str] = set()
        pool = None
        try:
            if descriptor.tool_module:
                importer(descriptor.tool_module)
                delta = _tool_names(tool_manager) - before
            pool = descriptor.create_pool(ctx_factory(descriptor.kind))
            adapter = descriptor.create_scenario_adapter(pool)
            registry.register(descriptor, pool=pool, adapter=adapter, discovered=item.discovered)
        except Exception as exc:  # noqa: BLE001
            delta = delta or (_tool_names(tool_manager) - before)
            _remove_tools(tool_manager, delta)
            registry.record_failure(
                name=item.discovered.name,
                reason=f"activation failed: {exc!r}",
                discovered=item.discovered,
                descriptor=_safe_descriptor(descriptor),
            )
            log.warning(
                "octowright.plugins.activation_failed",
                name=item.discovered.name,
                error=repr(exc),
                rolled_back_tools=sorted(delta),
            )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_loader.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/plugins/loader.py tests/plugins/test_loader.py
git commit -m "feat(plugins): two-phase load transaction with delta rollback

Rollback removes what was actually registered, not what was declared: a
module that registers an undeclared tool and then raises is the one case
rollback exists for, and unwinding by declaration would leak it. Descriptor
resolution is phase one so a plugin's capability profile can register
before any tool decorator fires."
```

---

## Task 8: Plugin capability profiles

**Files:**
- Modify: `src/octowright/server/profiles.py`
- Test: `tests/plugins/test_plugin_profiles.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `octowright.server.profiles.register_plugin_profile(name: str, tool_names: Iterable[str]) -> None`
  - `octowright.server.profiles.plugin_profile_names() -> list[str]`
  - `octowright.server.profiles.reset_plugin_profiles() -> None` (test seam)
  - `build_allowed_set` consults plugin profiles and no longer misdiagnoses one as unknown.

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_plugin_profiles.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import pytest

from octowright.server import profiles


@pytest.fixture(autouse=True)
def _clean_plugin_profiles():
    profiles.reset_plugin_profiles()
    yield
    profiles.reset_plugin_profiles()


def test_plugin_profile_widens_the_allowed_set():
    profiles.register_plugin_profile("refkinds", ["refkind_launch", "refkind_close"])
    allowed = profiles.build_allowed_set("refkinds")
    assert {"refkind_launch", "refkind_close"} <= allowed
    assert profiles.ALWAYS_ON_TOOLS <= allowed


def test_plugin_profile_does_not_trigger_unknown_diagnostics(caplog):
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    with caplog.at_level("WARNING"):
        profiles.build_allowed_set("refkinds")
    messages = [record.getMessage() for record in caplog.records]
    assert not any("profile.unknown" in message for message in messages)
    assert not any("profile.all_unknown" in message for message in messages)


def test_a_genuinely_unknown_profile_still_warns(caplog):
    with caplog.at_level("WARNING"):
        profiles.build_allowed_set("nosuchprofile")
    messages = [record.getMessage() for record in caplog.records]
    assert any("profile.unknown" in message for message in messages)


def test_plugin_profile_may_not_shadow_a_core_profile():
    with pytest.raises(ValueError, match="core profile"):
        profiles.register_plugin_profile("core", ["refkind_launch"])


def test_registered_names_are_listed():
    profiles.register_plugin_profile("refkinds", ["refkind_launch"])
    assert profiles.plugin_profile_names() == ["refkinds"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_plugin_profiles.py -v`
Expected: FAIL — `AttributeError: module 'octowright.server.profiles' has no attribute 'reset_plugin_profiles'`.

- [ ] **Step 3: Add plugin profiles to `profiles.py`**

Add after the `PROFILES` dict definition:

```python
#: Capability profiles contributed by enabled plugins. Kept separate from
#: PROFILES so core's static table stays static and a plugin cannot mutate a
#: core profile's membership. Populated by the loader BEFORE the active filter
#: is computed — the ordering is load-bearing, see build_allowed_set.
_PLUGIN_PROFILES: dict[str, frozenset[str]] = {}


def register_plugin_profile(name: str, tool_names: Iterable[str]) -> None:
    """Register a plugin's capability profile.

    A plugin may create a profile; it may not extend or shadow a core one,
    because a third-party package silently widening ``core`` would defeat the
    point of picking a narrow profile.
    """
    if name in PROFILES:
        raise ValueError(f"plugin profile {name!r} collides with a core profile")
    _PLUGIN_PROFILES[name] = frozenset(tool_names)


def plugin_profile_names() -> list[str]:
    return sorted(_PLUGIN_PROFILES)


def reset_plugin_profiles() -> None:
    """Clear registered plugin profiles. Test seam; the daemon never calls it."""
    _PLUGIN_PROFILES.clear()
```

Add `from collections.abc import Iterable` to the imports.

Then change the resolution loop inside `build_allowed_set` from:

```python
    for name in names:
        if name not in PROFILES:
            log.warning(
                "octowright.profile.unknown",
                profile=name,
                known=sorted(PROFILES.keys()),
            )
            continue
        matched_any = True
        allowed.update(PROFILES[name])
```

to:

```python
    for name in names:
        if name in PROFILES:
            matched_any = True
            allowed.update(PROFILES[name])
            continue
        if name in _PLUGIN_PROFILES:
            matched_any = True
            allowed.update(_PLUGIN_PROFILES[name])
            continue
        # Diagnose against BOTH tables. Checking PROFILES alone made
        # `OCTOWRIGHT_PROFILE=<plugin profile>` log profile.unknown and then
        # profile.all_unknown at ERROR — the loudest signal the daemon emits,
        # fired at a correct configuration.
        log.warning(
            "octowright.profile.unknown",
            profile=name,
            known=sorted([*PROFILES.keys(), *_PLUGIN_PROFILES.keys()]),
        )
```

And update the docstring's first paragraph to note that plugin profiles participate.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --active pytest tests/plugins/test_plugin_profiles.py tests/test_profiles.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/server/profiles.py tests/plugins/test_plugin_profiles.py
git commit -m "feat(profiles): plugin-contributed capability profiles

build_allowed_set now resolves and DIAGNOSES against both tables.
Checking PROFILES alone meant OCTOWRIGHT_PROFILE naming a plugin profile
logged profile.unknown and then profile.all_unknown at ERROR — the
loudest signal the daemon emits, fired at a correct configuration."
```

---

## Task 9: The backend-only reference plugin

The reference plugin is the in-repo consumer that keeps the contract from rotting. At this step it covers the seams Step 1 actually inverts: descriptor metadata, pool, launch transaction, protected close, and MCP tools.

**Files:**
- Create: `tests/plugins/reference/__init__.py`, `tests/plugins/reference/pool.py`, `tests/plugins/reference/plugin.py`, `tests/plugins/reference/tools.py`
- Test: `tests/plugins/test_reference_plugin.py`

**Interfaces:**
- Consumes: `PluginContext` (Task 4), `SessionPool`/`SessionKindPlugin`/`LaunchResult`/`CloseResult` (Task 2), `ProtectedSessionCloseError` (Task 1).
- Produces:
  - `tests.plugins.reference.plugin.plugin` — the descriptor instance an entry point would resolve to
  - `ReferencePool` implementing the full `SessionPool` surface
  - kind `refkind`, tools `refkind_launch` / `refkind_close`, profile `refkinds`

- [ ] **Step 1: Write the reference plugin**

Create `tests/plugins/reference/__init__.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""A deliberately partial reference session-kind plugin.

It exists so every seam of the plugin API has a consumer inside core CI
without core depending on a third-party package. Partial on purpose: it
declares fewer capabilities than browsers do, so the skip paths are exercised
rather than only the happy path.
"""

from __future__ import annotations
```

Create `tests/plugins/reference/pool.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from octowright.plugins.contract import CloseResult, LaunchResult
from octowright.plugins.errors import ProtectedSessionCloseError
from octowright.plugins.session_launch import PluginContext
from octowright.recorder import Recorder

KIND = "refkind"


@dataclass
class ReferenceSession:
    instance_id: str
    kind: str
    label: str | None
    profile: str | None
    url: str | None
    recorder: Recorder
    log_path: Path
    protected: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


class ReferencePool:
    """A pool with no external dependency — it records and nothing else."""

    def __init__(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        self._sessions: dict[str, ReferenceSession] = {}

    async def launch(
        self,
        *,
        label: str | None = None,
        profile: str | None = None,
        protected: bool = False,
        fail: bool = False,
        **_: Any,
    ) -> LaunchResult:
        instance_id = uuid4().hex[:12]
        async with self._ctx.begin_session(instance_id=instance_id, label=label, profile=profile) as launch:
            if fail:
                # Exercised by the failed-launch test: nothing recorded, so the
                # opening-row-only recording must be discarded.
                raise RuntimeError("reference launch asked to fail")
            launch.recorder.record("ref_ready", note="reference session up")
            session = ReferenceSession(
                instance_id=instance_id,
                kind=KIND,
                label=label,
                profile=profile,
                url=None,
                recorder=launch.recorder,
                log_path=launch.log_path,
                protected=protected,
            )
            result = launch.commit(session)
        self._sessions[instance_id] = session
        return result

    def get(self, instance_id: str) -> ReferenceSession:
        if instance_id not in self._sessions:
            raise KeyError(f"no refkind session {instance_id!r}")
        return self._sessions[instance_id]

    def maybe_get(self, instance_id: str) -> ReferenceSession | None:
        return self._sessions.get(instance_id)

    def iter_sessions(self) -> Iterator[ReferenceSession]:
        return iter(list(self._sessions.values()))

    async def close(self, instance_id: str, *, force: bool = False) -> CloseResult:
        session = self.maybe_get(instance_id)
        if session is None:
            raise KeyError(f"no refkind session {instance_id!r}")
        if session.protected and not force:
            raise ProtectedSessionCloseError(
                f"refkind {instance_id!r} is protected; pass force=True to close it"
            )
        session.recorder.close()
        del self._sessions[instance_id]
        return CloseResult(instance_id=instance_id, kind=KIND, closed=True)

    async def close_all(self, *, force: bool = False) -> None:
        failures: list[tuple[str, Exception]] = []
        for instance_id in list(self._sessions):
            try:
                await self.close(instance_id, force=force)
            except Exception as exc:  # noqa: BLE001 - continue past one failure
                failures.append((instance_id, exc))
        if failures:
            raise ExceptionGroup("refkind close_all had failures", [exc for _, exc in failures])
```

Create `tests/plugins/reference/tools.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""MCP tools the reference plugin registers on import.

Registration is an import-time side effect, exactly as core's own tool
modules do it — the loader snapshots the tool manager around this import so a
partial registration can be rolled back.
"""

from __future__ import annotations

from typing import Any

from octowright.server._state import mcp
from octowright.server import plugin_state


@mcp.tool
async def refkind_launch(label: str | None = None, protected: bool = False) -> dict[str, Any]:
    """Launch a reference session."""
    pool = plugin_state.pool_for("refkind")
    return dict(await pool.launch(label=label, protected=protected))


@mcp.tool
async def refkind_close(instance_id: str, force: bool = False) -> dict[str, Any]:
    """Close a reference session."""
    pool = plugin_state.pool_for("refkind")
    return dict(await pool.close(instance_id, force=force))
```

Create `tests/plugins/reference/plugin.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from typing import Any

from octowright.plugins.contract import PLUGIN_API_VERSION
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.pool import KIND, ReferencePool, ReferenceSession


class ReferencePlugin:
    kind = KIND
    display_name = "Reference Kind"
    plugin_api_version = PLUGIN_API_VERSION
    tool_names = frozenset({"refkind_launch", "refkind_close"})
    tool_module = "tests.plugins.reference.tools"
    profile_name = "refkinds"
    frontend = None

    def create_pool(self, ctx: PluginContext) -> ReferencePool:
        return ReferencePool(ctx)

    def create_scenario_adapter(self, pool: ReferencePool) -> None:
        # Scenario participation arrives in build step 3. Returning None here
        # is the honest state, not a placeholder: this kind cannot appear in a
        # scenario yet.
        return None

    def session_detail(self, session: ReferenceSession) -> dict[str, Any]:
        return {
            "id": session.instance_id,
            "kind": session.kind,
            "label": session.label,
            "log_path": str(session.log_path),
        }


plugin = ReferencePlugin()
```

- [ ] **Step 2: Add the tiny plugin-state accessor the tools use**

Create `src/octowright/server/plugin_state.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Process-global plugin registry accessor for plugin tool modules.

A plugin's ``@mcp.tool`` functions need their pool at call time, but they are
imported *before* ``create_pool`` runs (the loader registers tools first so a
tool failure never has to tear a pool down). So they look the pool up through
this seam instead of closing over it.
"""

from __future__ import annotations

from octowright.plugins.contract import SessionPool
from octowright.plugins.registry import PluginRegistry

_registry = PluginRegistry()


def registry() -> PluginRegistry:
    return _registry


def set_registry(value: PluginRegistry) -> None:
    """Replace the process-global registry. Used at daemon start and by tests."""
    global _registry  # noqa: PLW0603 - one process-global, same seam as http/state
    _registry = value


def pool_for(kind: str) -> SessionPool:
    return _registry.get_plugin(kind).pool
```

- [ ] **Step 3: Write the failing test**

Create `tests/plugins/test_reference_plugin.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import json

import pytest

from pathlib import Path

from octowright.plugins.contract import CAPABILITIES, SessionPool, capabilities_of
from octowright.plugins.errors import ProtectedSessionCloseError
from octowright.plugins.registry import PluginRegistry
from octowright.plugins.session_launch import PluginContext
from tests.plugins.reference.plugin import plugin


@pytest.fixture
def pool(tmp_path):
    registry = PluginRegistry()
    ctx = PluginContext(kind=plugin.kind, recordings_dir=tmp_path, id_in_use=registry.id_in_use)
    return plugin.create_pool(ctx)


@pytest.mark.asyncio
async def test_launch_writes_a_session_start_row(pool):
    result = await pool.launch(label="demo")
    log_path = Path(result["log_path"])
    rows = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]

    assert rows[0]["action"] == "session_start"
    assert rows[0]["kind"] == "refkind"
    assert rows[1]["action"] == "ref_ready"


@pytest.mark.asyncio
async def test_failed_launch_leaves_no_recording(pool, tmp_path):
    with pytest.raises(RuntimeError):
        await pool.launch(fail=True)
    assert list(tmp_path.glob("*.jsonl")) == []


@pytest.mark.asyncio
async def test_protected_close_is_refused_without_force(pool):
    result = await pool.launch(protected=True)
    with pytest.raises(ProtectedSessionCloseError):
        await pool.close(result["instance_id"])
    closed = await pool.close(result["instance_id"], force=True)
    assert closed["closed"] is True


@pytest.mark.asyncio
async def test_unknown_id_raises_key_error(pool):
    with pytest.raises(KeyError):
        pool.get("nope")
    with pytest.raises(KeyError):
        await pool.close("nope")
    assert pool.maybe_get("nope") is None


@pytest.mark.asyncio
async def test_close_all_empties_the_pool(pool):
    await pool.launch()
    await pool.launch()
    await pool.close_all(force=True)
    assert list(pool.iter_sessions()) == []


def test_reference_pool_covers_every_session_pool_method(pool):
    # The anti-decay guard: adding a method to the SessionPool contract
    # without covering it in the reference plugin fails CI.
    required = {"launch", "get", "maybe_get", "iter_sessions", "close", "close_all"}
    declared = {name for name, value in vars(SessionPool).items() if not name.startswith("_") and callable(value)}
    assert declared == required, "SessionPool changed shape; update the reference pool and this set"
    for name in sorted(required):
        assert callable(getattr(pool, name)), f"reference pool is missing {name}"

    # Every non-async method must also be reachable without a running loop.
    assert pool.maybe_get("nope") is None
    assert list(pool.iter_sessions()) == []


def test_reference_plugin_declares_no_capabilities_yet():
    # Partial on purpose: scenario participation arrives in build step 3, and
    # a reference plugin that declared everything would exercise no skip path.
    assert capabilities_of(plugin.create_scenario_adapter(None)) == frozenset()
    assert CAPABILITIES == frozenset({"macros", "sync", "dialog_policy", "mock_routes"})
```

- [ ] **Step 4: Run the test to verify it fails, then passes**

Run: `uv run --active pytest tests/plugins/test_reference_plugin.py -v`
Expected first: FAIL — `ModuleNotFoundError: No module named 'octowright.server.plugin_state'`
(if you write the test before Step 2's module). With every file above in place:
7 passed.

- [ ] **Step 5: Verify the tools module registers under the loader**

Run: `uv run --active pytest tests/plugins -v`
Expected: all pass. If `tests.plugins.reference.tools` fails to import because
`tests` is not a package on `sys.path`, confirm `tests/__init__.py` exists (it
does) and that you are running pytest from the repo root.

- [ ] **Step 6: Commit**

```bash
git add tests/plugins/reference src/octowright/server/plugin_state.py tests/plugins/test_reference_plugin.py
git commit -m "test(plugins): backend-only reference session kind

The in-repo consumer that keeps the contract from rotting. Deliberately
partial — it declares no scenario capabilities, so the skip paths are
exercised rather than only the happy path — and it covers every
SessionPool method so adding one without coverage fails CI."
```

---

## Task 10: Wire the loader into the server and report it in status

**Files:**
- Modify: `src/octowright/server/_state.py` (descriptor phase before `_allowed_tools`)
- Modify: `src/octowright/server/_optional_tools.py` (activation)
- Modify: `src/octowright/server/meta.py` (status block)
- Test: `tests/plugins/test_server_wiring.py`

**Interfaces:**
- Consumes: `discover`, `enabled_names` (Task 5); `resolve_descriptors`, `activate` (Task 7); `register_plugin_profile` (Task 8); `PluginRegistry`, `plugin_state` (Tasks 6, 9).
- Produces:
  - `octowright.server._state.plugin_registry: PluginRegistry`
  - `octowright_status()["plugins"]: list[dict]`

- [ ] **Step 1: Write the failing test**

Create `tests/plugins/test_server_wiring.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state


def test_status_reports_the_plugins_block():
    # octowright_status is sync — see tests/test_leader_mode.py, which calls it
    # without await.
    from octowright.server.meta import octowright_status

    payload = octowright_status()
    assert "plugins" in payload
    assert isinstance(payload["plugins"], list)


def test_plugin_state_registry_is_replaceable():
    original = plugin_state.registry()
    replacement = PluginRegistry()
    try:
        plugin_state.set_registry(replacement)
        assert plugin_state.registry() is replacement
    finally:
        plugin_state.set_registry(original)


def test_state_exposes_the_process_registry():
    from octowright.server import _state

    assert isinstance(_state.plugin_registry, PluginRegistry)


def test_no_plugins_enabled_by_default():
    from octowright.server import _state

    # A core install enables nothing, so the tool surface is unchanged.
    assert _state.plugin_registry.kinds() == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --active pytest tests/plugins/test_server_wiring.py -v`
Expected: FAIL — `AttributeError: module 'octowright.server._state' has no attribute 'plugin_registry'` and a `KeyError: 'plugins'`.

- [ ] **Step 3: Resolve descriptors before the profile filter in `_state.py`**

In `src/octowright/server/_state.py`, insert immediately **before** the line
`_allowed_tools = active_filter()`:

```python
# Plugin descriptors resolve BEFORE the profile filter is computed, because a
# plugin's capability profile must be registered before any @mcp.tool
# decorator fires — decoration is an import-time side effect and the filter is
# read at decoration time. Discovery is metadata-only; only an explicitly
# enabled plugin's descriptor is imported here.
plugin_registry = PluginRegistry()
_enabled_plugins = plugin_discovery.enabled_names()
try:
    _discovered_plugins = plugin_discovery.discover()
except Exception:  # noqa: BLE001 - e.g. two distributions claiming one name
    # discover() refuses duplicate entry-point names outright. That must not be
    # fatal here: the daemon owns live browsers, and a bad third-party package
    # cannot be allowed to stop it from starting.
    log.warning("octowright.plugins.discovery_failed", exc_info=True)
    _discovered_plugins = []
_resolved_plugins = plugin_loader.resolve_descriptors(
    registry=plugin_registry,
    discovered=_discovered_plugins,
    enabled=_enabled_plugins,
)
for _item in _resolved_plugins:
    if _item.descriptor.profile_name:
        with contextlib.suppress(ValueError):
            register_plugin_profile(_item.descriptor.profile_name, _item.descriptor.tool_names)
plugin_state.set_registry(plugin_registry)
```

Add the imports at the top of `_state.py`:

```python
import contextlib

from octowright.plugins import discovery as plugin_discovery
from octowright.plugins import loader as plugin_loader
from octowright.plugins.registry import PluginRegistry
from octowright.server import plugin_state
from octowright.server.profiles import register_plugin_profile
```

> A `ValueError` here means the plugin's profile name collides with a core
> profile. It is suppressed rather than fatal because the plugin is still
> loadable — it just registers under no profile — and `resolve_descriptors`
> has already recorded the plugin's state. Log it via
> `log.warning("octowright.plugins.profile_collision", name=...)` inside the
> `except` if you prefer an explicit block to `suppress`.

- [ ] **Step 4: Activate plugins in `_optional_tools.py`**

Append to `src/octowright/server/_optional_tools.py`:

```python
def _activate_plugins() -> None:
    """Import each enabled plugin's tool module and build its pool.

    Runs here rather than in ``_state`` so tool registration happens after the
    profile filter exists, and so a plugin's tool module can import ``mcp``
    from ``_state`` without a circular import.
    """
    from octowright.plugins import loader as plugin_loader
    from octowright.plugins.session_launch import PluginContext
    from octowright.server._state import plugin_registry, _resolved_plugins, mcp
    from octowright import defaults

    def _ctx(kind: str) -> PluginContext:
        return PluginContext(
            kind=kind,
            recordings_dir=defaults.RECORDINGS_DIR,
            id_in_use=plugin_registry.id_in_use,
        )

    plugin_loader.activate(
        registry=plugin_registry,
        resolved=_resolved_plugins,
        ctx_factory=_ctx,
        tool_manager=mcp._tool_manager,
    )


_activate_plugins()
```

Place the call at the end of the module, after the existing terminal-tools
block, so terminal registration is untouched.

- [ ] **Step 5: Add the status block**

In `src/octowright/server/meta.py`, add to the `octowright_status()` return
dict, immediately after the `"profile": profile_block,` entry:

```python
        # Every session-kind plugin core knows about, whatever its state.
        # Disabled rows carry metadata only — reporting `kind` for a disabled
        # plugin would require importing it, executing exactly the code
        # explicit enable exists to gate.
        "plugins": _plugin_state.registry().status_rows(),
```

Add the import near meta.py's other `octowright.server` imports:

```python
from octowright.server import plugin_state as _plugin_state
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --active pytest tests/plugins/test_server_wiring.py -v`
Expected: 4 passed.

- [ ] **Step 7: Verify the whole suite and the lint gate**

Run: `uv run --active pytest -m "not live_browser and not memory_isolated" -q`
Expected: no new failures; `tests/terminal/` still green.

Run: `make lint`
Expected: every gate passes. If vulture flags the not-yet-consumed contract
members (`FrontendAsset`, `RENDERER_API_VERSION`, the capability Protocols),
add them to `.ci/vulture-baseline.json` — the baseline is a ratchet, and these
are consumed in build steps 3 and 4.

- [ ] **Step 8: Commit**

```bash
git add src/octowright/server/_state.py src/octowright/server/_optional_tools.py \
        src/octowright/server/meta.py tests/plugins/test_server_wiring.py
git commit -m "feat(server): wire plugin discovery, activation, and status

Descriptors resolve and plugin profiles register before _allowed_tools is
computed, because @mcp.tool decoration reads the filter at import time.
Activation runs from _optional_tools so a plugin tool module can import mcp
without a cycle. A core install enables nothing, so the tool surface is
unchanged."
```

---

## Done criteria

- `uv run --active pytest -m "not live_browser and not memory_isolated"` green.
- `make lint` green.
- `tests/terminal/` unchanged and green — terminal still runs on its own path.
- `octowright_status()["plugins"]` is `[]` on a default install.
- With `OCTOWRIGHT_PLUGINS` naming a plugin that is not installed, status shows `state: "missing"`.
- No push, no PR, no CHANGELOG edit, no release.

## Not in this step

Deferred to later build steps, per the spec's §12:

- `ctx.artifact` reserve/commit and the `artifact_registered` row (step 2).
- Registry-driven session list, detail, and close; `session_start` consumed by discovery; shutdown teardown (step 2).
- `ScenarioAdapter` implementations, `BrowserScenarioAdapter`, `options:` replacing `connector_type` (step 3).
- `/api/plugins`, asset serving, `mountStream`, fallback renderer (step 4).
- Deleting terminal from core and standing up `octowright-terminal` (step 5).
