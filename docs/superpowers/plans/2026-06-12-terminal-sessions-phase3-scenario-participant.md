# Terminal Sessions — Phase 3: Scenario Participant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a scenario declare a **terminal** participant (PTY or SSH) alongside browsers — launched, tracked (status/participants/tail), and closed by the scenario lifecycle — with persona SSH defaults (explicit participant args win).

**Architecture:** A terminal participant has `kind: "terminal"` and a `connector_type` of `pty`/`ssh`. `ScenarioPool.start()` partitions participants into a browser roster (`browser_pool.spawn_roster`) and per-terminal `terminal_pool.launch(...)`, then merges the launched dicts back into original participant order. A unified `_resolve_session(p, browser_pool, terminal_pool)` routes by `p["kind"]`. Browser-only operations (fixtures, Playwright startup/teardown macros, `wait_for_sync`, `run_macro`) **skip** terminal participants — Phase 3 does **not** add terminal macro/replay or terminal sync (that's later). The connector-config builders move to a **pure** `octowright/terminal/connector_config.py` (no uterm import) so core `scenarios.py` can build terminal configs without pulling uterm.

**Tech stack:** Python 3.11+, existing scenario/persona/terminal subsystems. Builds on Phase 1 + Phase 2.

**Depends on:** Phase 2 (committed). **Pre-merge gate still open:** GPLv3 §7 linking exception in `provide-uterm`; the `[terminal]` extra is now declared.

**Conventions:** identical to Phase 1/2 — SPDX header w/ blank line after the `#` block; `from __future__ import annotations`; run via `uv run --active --no-sync`; per-task `pytest --no-cov`; lowercase-start conventional-commit subjects; never mention AI in commits; mark fake test secrets with `# pragma: allowlist secret`. The terminal extra must be installed for terminal-path tests (`uv sync --all-groups --extra terminal`); the `tests/terminal/` suite is gated on availability but the **scenario** tests live in `tests/` and run on core too — so scenario tests that exercise terminal launch must use a **stub terminal pool**, not the real one, to stay core-safe (mirror the existing `_StubPool` pattern in `tests/test_scenarios_unit.py`).

---

## Scope (what Phase 3 does and does NOT do)

**Does:** declare terminal participants in YAML/Python; validate them; resolve PTY/SSH launch config (persona `app.ssh` defaults, explicit-args-win, no password in YAML — key-based/known_hosts only); partition-launch in `ScenarioPool.start`; track in status/participants/tail; close in `stop`; route `remap` by kind; thread `terminal_pool` through the scenario MCP tools.

**Does NOT (guarded/deferred):** terminal participants are **skipped** by fixtures, Playwright startup/teardown macros, `wait_for_sync`, and `run_macro` (these are browser-only). A terminal participant declaring `startup_macros` is a **validation error**. SSH **password** auth from a scenario is out of scope (secret hygiene — scenarios are persisted); persona SSH defaults cover host/port/user/key_path/known_hosts/insecure_no_host_check only.

---

## Task 1: pure connector-config module + SSH default port

**Files:**
- Create: `src/octowright/terminal/connector_config.py`
- Modify: `src/octowright/defaults.py` (add `SSH_DEFAULT_PORT`)
- Modify: `src/octowright/server/terminal/lifecycle.py` (delegate to the pure builder)
- Test: `tests/terminal/test_connector_config.py`

Rationale: `scenarios.py` is core (no uterm). The SSH/PTY config dicts are pure data — extracting the builders into a uterm-free module lets both the (optional) tool layer and core scenarios build them. `octowright.terminal.connector_config` imports only `octowright.defaults` + stdlib; importing it triggers `octowright/terminal/__init__` → `availability` (import-light), so it's safe on a core install.

- [ ] **Step 1: Add `SSH_DEFAULT_PORT` to `defaults.py`** (near the other network defaults; env-overridable, no hardcoded port elsewhere):

```python
# Default SSH port for terminal SSH connectors (scenario participants / terminal_launch).
SSH_DEFAULT_PORT: int = int(os.environ.get("OCTOWRIGHT_SSH_PORT", "22"))
```

- [ ] **Step 2: Write the failing test** `tests/terminal/test_connector_config.py`:

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from octowright.terminal.connector_config import pty_connector_config, ssh_connector_config


def test_pty_config_defaults_and_overrides() -> None:
    assert pty_connector_config(command=None, cols=None, rows=None) == {
        "command": "/bin/bash",
        "cols": 80,
        "rows": 24,
    }
    assert pty_connector_config(command="/bin/zsh", cols=132, rows=50) == {
        "command": "/bin/zsh",
        "cols": 132,
        "rows": 50,
    }


def test_ssh_config_emits_only_connector_keys() -> None:
    cfg = ssh_connector_config(
        host="h",
        port=2222,
        user="me",
        key_path="/k",
        password=None,
        known_hosts="/kh",
        insecure_no_host_check=False,
    )
    assert cfg == {"port": 2222, "host": "h", "username": "me", "client_key_path": "/k", "known_hosts": "/kh"}
    assert "cols" not in cfg and "rows" not in cfg and "command" not in cfg
```

- [ ] **Step 3: Create `connector_config.py`** (pure; lift the SSH mapping from `lifecycle._ssh_connector_config`, add the PTY builder):

```python
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Pure builders for uterm connector_config dicts (no uterm import).

Lives in the terminal package but depends only on octowright.defaults + stdlib,
so core callers (e.g. scenarios.py) can build terminal launch configs without
pulling the optional uterm dependency.
"""

from __future__ import annotations

from typing import Any

from octowright.defaults import SSH_DEFAULT_PORT

__all__ = ["SSH_DEFAULT_PORT", "pty_connector_config", "ssh_connector_config"]


def pty_connector_config(*, command: str | None, cols: int | None, rows: int | None) -> dict[str, Any]:
    return {"command": command or "/bin/bash", "cols": cols or 80, "rows": rows or 24}


def ssh_connector_config(
    *,
    host: str | None,
    port: int,
    user: str | None,
    key_path: str | None,
    password: str | None,
    known_hosts: str | None,
    insecure_no_host_check: bool,
) -> dict[str, Any]:
    """Map SSH args to the uterm SshSessionConnector's allow-listed keys only.

    The connector rejects unknown keys and fixes its own remote PTY size, so no
    cols/rows/command. Omitted args are dropped so the connector falls back to
    its own defaults rather than seeing None.
    """
    cfg: dict[str, Any] = {"port": port}
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

- [ ] **Step 4: Refactor `server/terminal/lifecycle.py`** to delegate (DRY) — replace `_DEFAULT_SSH_PORT` and the body of `_ssh_connector_config` with imports from the pure module:

```python
from octowright.terminal.connector_config import SSH_DEFAULT_PORT as _DEFAULT_SSH_PORT
from octowright.terminal.connector_config import ssh_connector_config as _ssh_connector_config
```
…and drop the now-duplicated local `_ssh_connector_config` def + the `from provide.uterm.defaults import TerminalDefaults` import. Keep `terminal_launch`'s PTY branch using `pty_connector_config` for symmetry. Re-run `tests/terminal/test_ssh_args.py` — it imports `lifecycle._ssh_connector_config`, which now resolves to the re-exported pure builder, so it still passes.

- [ ] **Step 5: Run + quality + commit** `feat(terminal): extract pure connector-config builders`.

---

## Task 2: `Participant` terminal fields + validation + YAML loader

**Files:**
- Modify: `src/octowright/scenarios.py` (`Participant`, `_validate_scenario`, `_load_yaml_participant`, validators)
- Modify: `src/octowright/defaults.py` (`SUPPORTED_TERMINAL_KINDS`)
- Modify: `src/octowright/mcp_types.py` (`ScenarioParticipant` TypedDict — add optional `connector_type`)
- Test: `tests/test_scenarios_unit.py`

- [ ] **Step 1: Add `SUPPORTED_TERMINAL_KINDS`** to `defaults.py` (do NOT touch `SUPPORTED_KINDS`):

```python
# Connector types for terminal scenario participants (kind == "terminal").
SUPPORTED_TERMINAL_KINDS = ("pty", "ssh")
```

- [ ] **Step 2: Write failing tests** in `tests/test_scenarios_unit.py` (mirror existing `_validate_scenario` test style):

```python
def test_terminal_participant_accepted() -> None:
    from octowright.scenarios import Participant, Scenario, _validate_scenario

    s = Scenario(
        name="t",
        participants=[
            Participant(persona="alice", kind="terminal", role="operator", connector_type="pty"),
        ],
    )
    _validate_scenario(s)  # must not raise
    assert s.participants[0].connector_type == "pty"


def test_terminal_participant_defaults_connector_type_to_pty() -> None:
    from octowright.scenarios import _load_yaml_participant

    p = _load_yaml_participant({"persona": "a", "kind": "terminal", "role": "operator"}, index=0, scenario_name="t")
    assert p.connector_type == "pty"


def test_terminal_bad_connector_type_rejected() -> None:
    from octowright.scenarios import Participant, Scenario, _validate_scenario
    import pytest

    s = Scenario(name="t", participants=[Participant(persona="a", kind="terminal", role="x", connector_type="telnet")])
    with pytest.raises(ValueError, match="connector_type"):
        _validate_scenario(s)


def test_terminal_with_startup_macros_rejected() -> None:
    from octowright.scenarios import Participant, Scenario, _validate_scenario
    import pytest

    s = Scenario(
        name="t",
        participants=[
            Participant(persona="a", kind="terminal", role="x", connector_type="pty", startup_macros=["m"]),
        ],
    )
    with pytest.raises(ValueError, match="startup_macros"):
        _validate_scenario(s)


def test_browser_kind_still_validated() -> None:
    from octowright.scenarios import Participant, Scenario, _validate_scenario
    import pytest

    s = Scenario(name="t", participants=[Participant(persona="a", kind="opera", role="player")])
    with pytest.raises(ValueError, match="unsupported kind"):
        _validate_scenario(s)
```

- [ ] **Step 3: Extend `Participant`** (`scenarios.py:37`) with terminal fields (all optional → browser participants unaffected):

```python
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
    # Terminal participants (kind == "terminal"); connector_type defaults to "pty".
    connector_type: str | None = None
    command: str | None = None
    cols: int | None = None
    rows: int | None = None
    host: str | None = None
    port: int | None = None
    user: str | None = None
    key_path: str | None = None
    known_hosts: str | None = None
    insecure_no_host_check: bool | None = None
```

- [ ] **Step 4: Terminal-aware `_validate_scenario`** — replace the `if p.kind not in SUPPORTED_KINDS` block:

```python
from octowright.defaults import SUPPORTED_TERMINAL_KINDS

for p in s.participants:
    if p.kind == "terminal":
        ct = p.connector_type or "pty"
        if ct not in SUPPORTED_TERMINAL_KINDS:
            raise ValueError(
                f"scenario {s.name!r}: terminal participant has unsupported connector_type {p.connector_type!r}"
            )
        if p.startup_macros:
            raise ValueError(
                f"scenario {s.name!r}: terminal participant {p.persona!r} cannot declare startup_macros "
                "(Playwright macros don't apply to terminals)"
            )
    elif p.kind not in SUPPORTED_KINDS:
        raise ValueError(f"scenario {s.name!r}: participant has unsupported kind {p.kind!r}")
    # ... role check + duplicate (persona, kind) check unchanged ...
```

- [ ] **Step 5: YAML loader** — in `_load_yaml_participant`, default `connector_type` to `"pty"` when `kind == "terminal"`, validate the new optional ints (`port`, `cols`, `rows`) and bool (`insecure_no_host_check`), and pass the terminal fields to `Participant(...)`:

```python
_validate_optional_ints(
    raw, ("viewport_w", "viewport_h", "port", "cols", "rows"), index=index, scenario_name=scenario_name
)
_validate_optional_bools(
    raw, ("stabilize", "record_video", "trace", "insecure_no_host_check"), index=index, scenario_name=scenario_name
)
kind = raw["kind"]
connector_type = raw.get("connector_type") or ("pty" if kind == "terminal" else None)
return Participant(
    persona=raw["persona"],
    kind=kind,
    role=raw.get("role", "player"),
    url=raw.get("url"),
    startup_macros=startup_macros,
    viewport_w=raw.get("viewport_w"),
    viewport_h=raw.get("viewport_h"),
    stabilize=raw.get("stabilize"),
    record_video=raw.get("record_video"),
    trace=raw.get("trace"),
    connector_type=connector_type,
    command=raw.get("command"),
    cols=raw.get("cols"),
    rows=raw.get("rows"),
    host=raw.get("host"),
    port=raw.get("port"),
    user=raw.get("user"),
    key_path=raw.get("key_path"),
    known_hosts=raw.get("known_hosts"),
    insecure_no_host_check=raw.get("insecure_no_host_check"),
)
```

- [ ] **Step 6: Update `mcp_types.ScenarioParticipant`** TypedDict — add `connector_type: NotRequired[str | None]` so the launched terminal participant dicts type-check.

- [ ] **Step 7: Run + quality + commit** `feat(scenarios): accept terminal participants in the model + validation`.

---

## Task 3: `resolve_terminal_launch` (Participant + persona SSH defaults → launch kwargs)

**Files:**
- Modify: `src/octowright/scenarios.py` (add `resolve_terminal_launch`)
- Test: `tests/test_scenarios_unit.py`

`terminal_pool.launch(kind=<connector_type>, connector_config=..., label=..., profile=..., protected=...)` — note its `kind` param is the connector type (`pty`/`ssh`), and the session's `kind` is always `"terminal"`. Persona SSH defaults live in `persona.app["ssh"]` (the freeform `app` dict, already allowed — no Persona model change). Explicit participant fields win over persona defaults.

- [ ] **Step 1: Write failing tests** (no real pool; pure function):

```python
def test_resolve_terminal_launch_pty() -> None:
    from octowright.scenarios import Participant, resolve_terminal_launch

    kw = resolve_terminal_launch(
        Participant(persona="a", kind="terminal", role="op", connector_type="pty", command="/bin/sh")
    )
    assert kw["kind"] == "pty"
    assert kw["connector_config"] == {"command": "/bin/sh", "cols": 80, "rows": 24}
    assert kw["profile"] == "a" and kw["protected"] is False


def test_resolve_terminal_launch_ssh_explicit_wins(monkeypatch, tmp_path) -> None:
    # persona app.ssh provides defaults; participant host/user override.
    import octowright.scenarios as sc
    from octowright.scenarios import Participant, resolve_terminal_launch

    class _P:  # stand-in persona
        app = {"ssh": {"host": "default-host", "user": "deploy", "key_path": "/k", "known_hosts": "/kh"}}

    monkeypatch.setattr(sc, "_load_persona_or_none", lambda name: _P())
    kw = resolve_terminal_launch(
        Participant(persona="a", kind="terminal", role="op", connector_type="ssh", host="explicit-host")
    )
    cfg = kw["connector_config"]
    assert kw["kind"] == "ssh"
    assert cfg["host"] == "explicit-host"  # participant wins
    assert cfg["username"] == "deploy"  # persona default
    assert cfg["client_key_path"] == "/k" and cfg["known_hosts"] == "/kh"
```

- [ ] **Step 2: Implement** in `scenarios.py` (top-level import of the pure builders is core-safe):

```python
from octowright.terminal.connector_config import (
    SSH_DEFAULT_PORT,
    pty_connector_config,
    ssh_connector_config,
)


def _load_persona_or_none(name: str) -> Any:
    from octowright import personas as _p

    try:
        return _p.load_persona(name)
    except FileNotFoundError:
        return None


def resolve_terminal_launch(p: Participant) -> dict[str, Any]:
    """Participant (kind == 'terminal') → terminal_pool.launch(**kwargs).

    SSH fields resolve participant-override → persona app['ssh'] default → omit.
    No password from YAML (scenarios are persisted): key-based / known_hosts only.
    """
    connector_type = p.connector_type or "pty"
    if connector_type == "ssh":
        persona = _load_persona_or_none(p.persona)
        ssh = (getattr(persona, "app", {}) or {}).get("ssh", {}) or {}

        def pick(attr: str, key: str) -> Any:
            v = getattr(p, attr)
            return v if v is not None else ssh.get(key)

        port = p.port if p.port is not None else int(ssh.get("port", SSH_DEFAULT_PORT))
        insecure = (
            bool(p.insecure_no_host_check)
            if p.insecure_no_host_check is not None
            else bool(ssh.get("insecure_no_host_check", False))
        )
        cfg = ssh_connector_config(
            host=pick("host", "host"),
            port=port,
            user=pick("user", "user"),
            key_path=pick("key_path", "key_path"),
            password=None,
            known_hosts=pick("known_hosts", "known_hosts"),
            insecure_no_host_check=insecure,
        )
    else:
        cfg = pty_connector_config(command=p.command, cols=p.cols, rows=p.rows)
    return {"kind": connector_type, "connector_config": cfg, "label": None, "profile": p.persona, "protected": False}
```

- [ ] **Step 3: Run + quality + commit** `feat(scenarios): resolve terminal-participant launch config with persona ssh defaults`.

---

## Task 4: `ScenarioPool.start` browser/terminal fan-out partition

**Files:**
- Modify: `src/octowright/scenarios_pool.py` (`start`, `_rollback_start`, add `_resolve_session`)
- Test: `tests/test_scenarios_unit.py` / `tests/test_scenarios_pool.py` (stub terminal pool)

- [ ] **Step 1: Write failing test** — a `_StubTerminalPool` (launch returns a terminal-shaped dict, get/close work) + a mixed scenario; assert `start` launches both and the merged `live.participants` preserve spec order with the terminal entry carrying `kind == "terminal"`. Also assert: a terminal participant with `terminal_pool=None` raises a clear error.

- [ ] **Step 2: Implement the partition** in `start` (signature gains `terminal_pool: Any | None = None`). Partition by `p.kind == "terminal"`, launch browsers via roster + terminals via `terminal_pool.launch(**resolve_terminal_launch(p))`, then reassemble in original order. Raise `RuntimeError` if any terminal participant exists but `terminal_pool is None`. On any failure, close all launched in BOTH pools before raising. Thread `terminal_pool` into `_apply_fixtures`/`_run_startup_macros`/`_rollback_start`.

```python
    terminals = [(i, p) for i, p in enumerate(spec.participants) if p.kind == "terminal"]
    if terminals and terminal_pool is None:
        raise RuntimeError(
            f"scenario {effective_name!r} has terminal participant(s) but the octowright[terminal] "
            "extra is not installed (terminal_pool is unavailable)"
        )
    # launch browsers via roster, terminals via terminal_pool.launch, merge by index → `participants`
    # (full code in the implementer's hands; keep the existing error-rollback shape, closing both pools)
```

- [ ] **Step 3: Add `_resolve_session`** (module-level helper) used by later tasks:

```python
def _resolve_session(p: dict[str, Any], browser_pool: Any, terminal_pool: Any | None) -> Any:
    if p.get("kind") == "terminal":
        if terminal_pool is None:
            raise RuntimeError("terminal participant present but terminal_pool is unavailable")
        return terminal_pool.get(p["instance_id"])
    return browser_pool.get(p["instance_id"])
```

- [ ] **Step 4: Run + quality + commit** `feat(scenarios): launch terminal participants alongside browsers`.

---

## Task 5: guard browser-only ops + route close/remap for terminals

**Files:**
- Modify: `src/octowright/scenarios_pool.py` (`_apply_fixtures`, `_run_startup_macros`, `stop`, `run_macro`, `wait_for_sync`, `remap_participant`)
- Test: `tests/test_scenarios_pool.py`

- [ ] **Step 1: Write failing tests** — mixed scenario: `_apply_fixtures`/`_run_startup_macros` must NOT call `browser_pool.get` for the terminal participant; `stop` must close the terminal via `terminal_pool.close` and skip its teardown macro; `wait_for_sync`/`run_macro` skip terminals; `remap_participant` for a terminal validates against `terminal_pool`.

- [ ] **Step 2: Implement guards.** In `_apply_fixtures` and `_run_startup_macros`, `continue`/skip when `p["kind"] == "terminal"`. In `stop`, thread `terminal_pool`, skip terminal participants in the teardown-macro loop, and route the close through `_resolve_pool` (browser vs terminal). In `wait_for_sync` and `run_macro`, filter to browser participants only (terminals are not eligible; document in the docstring). In `remap_participant`, add `terminal_pool` param and look up the replacement session from the pool matching the participant's kind.

- [ ] **Step 3: Run + quality + commit** `feat(scenarios): guard browser-only ops and route close/remap for terminals`.

---

## Task 6: thread `terminal_pool` through the scenario MCP tools

**Files:**
- Modify: `src/octowright/server/scenarios.py` (import `terminal_pool`; pass it to start/spawn_template/stop/run_macro/wait_for_sync/remap; fix `scenario_run_as_test`'s direct `pool.get`)
- Test: `tests/test_scenarios.py` or a new `tests/test_scenarios_terminal.py` (stub pools via monkeypatch on `server._state`)

- [ ] **Step 1: Write failing test** — call `scenario_start` (monkeypatched stub pools incl. a stub `terminal_pool`) on a mixed scenario; assert the live scenario has both participants and `scenario_status` reports the terminal.

- [ ] **Step 2: Implement** — `from octowright.server._state import ..., terminal_pool` and pass `terminal_pool=terminal_pool` to each `scenario_pool.*` call; in `scenario_run_as_test`, route per-participant session resolution through `_resolve_session`.

- [ ] **Step 3: Run + quality + commit** `feat(scenarios): wire terminal_pool through the scenario MCP tools`.

---

## Task 7: example scenario + docs

**Files:**
- Create: `examples/scenarios/browser-plus-terminal.yaml`
- Modify: `AGENTS.md` (+ `CLAUDE.md` copy): note terminal participants under Scenario concept + the Terminal Sessions section; persona `app.ssh` defaults block.
- Test: `tests/test_scenarios.py` (load the example, assert it parses with one browser + one terminal participant)

- [ ] **Step 1:** Add the example YAML (mixed browser `player` + terminal `operator`, PTY).
- [ ] **Step 2:** Doc updates (run the AGENTS→CLAUDE sync: `cp AGENTS.md CLAUDE.md`).
- [ ] **Step 3: Commit** `docs(scenarios): document + example terminal scenario participants`.

---

## Final review

Dispatch a code-quality reviewer over the Phase 3 diff. Run full `make test` (with `--extra terminal` installed) — EXIT 0 + coverage. Confirm a **core install** (uterm absent) still: imports `octowright.scenarios` cleanly, validates/loads a browser-only scenario, and raises the clear "extra not installed" error (not an ImportError) when a terminal participant is started without `terminal_pool`.
