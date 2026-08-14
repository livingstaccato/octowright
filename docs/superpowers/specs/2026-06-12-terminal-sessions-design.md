# Terminal Sessions in Octowright (Shape A) — Design Spec

**Date:** 2026-06-12
**Status:** Approved design, pending implementation plan
**Topic:** Add terminal sessions (local shell / PTY / SSH) as a first-class Octowright session kind, reusing `provide-uterm`'s in-process session engine behind a single seam.

---

## 1. Summary

Octowright drives parallel Playwright **browsers**. `provide-uterm` (a sibling provide.io project) drives **terminals** — shells, PTYs, SSH — via in-process **connectors** (`SessionConnector` + `build_connector`), with screen emulation and prompt/flow detection.

This feature makes a terminal session a first-class Octowright session kind. An agent can drive a shell the same way it drives a browser, a scenario can launch a terminal participant alongside browser participants for **recorded full-stack flows** (web UI + backend shell on one timeline), and the dashboard shows the live terminal screen.

The integration is **in-process** ("Shape A"): Octowright depends on uterm's libraries and hosts terminal sessions inside its own daemon, recording to the same JSONL tree, rather than running a separate `uterm server` and bridging to it.

### Decisions locked during brainstorming

| # | Decision | Choice |
|---|----------|--------|
| 1 | Primary surface | **Both** — standalone `terminal_*` MCP tools **and** scenario participant, equal priority |
| 2 | Recording model | **Single Octowright format** — record directly into Octowright's `Recorder` (`{ts, action, …}`). *Mechanism corrected 2026-06-12 (see §0): we drive uterm's `SessionConnector` directly and translate its worker-protocol messages into `Recorder` actions. `HostedSessionRuntime` / `RecordingStore` / `SessionLogger` are **not** used — the runtime is a hub-WebSocket bridge, not a headless driver. The chosen outcome (single format, reuse uterm's connectors) is preserved; the seam just moves down one layer.* |
| 3 | v1 connectors | **PTY (local shell) + SSH** (the uterm `shell` connector is a toy reference mock — test-only, not a product surface; telnet / websocket deferred) |
| 4 | SSH credentials | **Explicit args are source of truth; persona supplies defaults** via additive optional persona fields |
| 5 | Dashboard | **List + tail + action timeline + live xterm.js screen view** in the session debugger |
| 6 | Packaging | **uterm is completely optional (plugin model)** — shipped as an `octowright[terminal]` extra, *not* a hard dependency. Core Octowright never imports uterm; the feature lights up only when the extra is installed. Tool registration is kept externally-hookable so a future out-of-tree entry-point plugin can register the same way. *(Decided 2026-06-12 — see §3.2.)* |

### 1.1 Mechanism correction (2026-06-12, post-source-verification)

The original draft assumed `HostedSessionRuntime` was a headless in-process driver we could reuse wholesale, recording through a custom `RecordingStore`. Reading the source disproved this:

- `HostedSessionRuntime.start()` → `_run()` connects **out** to a TermHub worker WebSocket (`/ws/worker/{id}/term`) and bridges all connector I/O across it. With no hub it sits in a reconnect-backoff loop. It exposes no public "send input / read output" surface. **Not usable headless.**
- The real in-process primitive is one layer down: the **`SessionConnector`**, built via `build_connector(session_id, display_name, type, config)` (from `provide.uterm.server.connectors`). It is fully driveable in-process: `start()`, `handle_input(data)→[msg]`, `poll_messages()→[msg]`, `get_snapshot()→{screen,cursor,…}`, `stop()` — no hub, no WebSocket, no FastAPI. `HostedSessionRuntime._bridge_session` is just a ~40-line pump over this interface; we re-implement the pump (minus the WS) and record directly to Octowright's `Recorder`.
- The uterm `shell` connector is an **in-memory toy** (`/help`/`/nick`/`/say` mock), not a real shell. The real local shell is the **PTY** connector (`provide-uterm-platform`).

**Net effect:** decisions #1–#5 stand; the implementation is simpler (no `HostedSessionRuntime`, `SessionLogger`, `RecordingStore`, `SessionDefinition`, or `RecordingConfig`); and §3/§4 below are rewritten to the connector-direct design.

---

## 2. Goals & scope

### In scope (v1)

- `pty` (local shell) and `ssh` terminal connectors. (The uterm `shell` connector is a toy mock — used only in tests/fixtures, never exposed as a product `kind`.)
- A `terminal_*` MCP tool group and a `terminals` capability profile.
- A terminal **scenario participant** (mixed browser + terminal scenarios).
- A translator that records uterm connector worker-protocol messages directly as Octowright JSONL actions (no uterm `RecordingStore` involved).
- Session-debugger **terminal view**: live (via `/tail`) and replay, rendered with xterm.js, plus the existing action timeline.
- Input redaction for terminal sends, consistent with `OCTOWRIGHT_REDACT_INPUTS`.
- Telemetry spans/metrics consistent with existing OTel conventions.

### Out of scope (later phases)

- `telnet` / `websocket` connectors.
- uterm's hijack-lease / collaborative presence (DeckMux) for human takeover.
- Exporting a terminal session to a standalone script (`export.py` is browser/Playwright-shaped).
- Golden accessibility-style snapshots for terminals.
- Persona-driven SSH as the *only* path (we do explicit-args-first; persona is defaults-only).

---

## 3. Architecture — dependency quarantine

All `provide-uterm` surface is confined to **one seam**: a new `octowright/terminal/` package plus a `server/terminal/` tool module. Nothing in `scenarios.py`, `http/`, `server/browser/`, or the existing frontend imports uterm. They interact only with:

- Octowright's **generic session shape** — `instance_id`, `kind`, `label`, `profile`, `log_path`, `recorder`, `protected`, `async close()`.
- Octowright-format **JSONL recordings** (`{ts, action, …}`).

If uterm changes, bumps a major version, or is swapped out, the blast radius is this one package.

```
src/octowright/terminal/
  __init__.py
  session.py      TerminalSession    — parallel dataclass to BrowserSession (minimal shape)
  pool.py         TerminalPool       — mirrors BrowserPool registry surface
  engine.py       TerminalEngine     — builds + drives a uterm SessionConnector (own poll loop); no HostedSessionRuntime
  translate.py    message→action     — pure fn: connector worker-protocol message → Octowright Recorder action
  redact.py       terminal input-redaction policy (reuses OCTOWRIGHT_REDACT_INPUTS semantics; owns password-prompt masking)

src/octowright/server/terminal/
  __init__.py
  lifecycle.py    @mcp.tool terminal_* functions
```

`src/octowright/server/_state.py` gains `terminal_pool = TerminalPool()` alongside `pool` and `scenario_pool`.

### Dependency set

| Package | Purpose | Notes |
|---------|---------|-------|
| `provide-uterm-server` (+ `asyncssh`) | `SessionConnector` base, `build_connector`/`register_connector`/`registered_types`, `SshSessionConnector` | `connectors/` import no fastapi — confirmed. We use **only** `provide.uterm.server.connectors`, not the runtime/bridge/routes. SSH needs `asyncssh` (declared directly, or via the `provide-uterm[ssh]`/`provide-uterm-server[gateway]` extra). |
| `provide-uterm-platform` | `PTYConnector` (`kind="pty"`) | local PTY (fork + master fd). Self-registers via `_register()` on import; engine imports the module to trigger registration. |
| `@xterm/xterm` (frontend) | terminal renderer in the dashboard | same OSS lib uterm uses; we do **not** import uterm's frontend SPA. xterm.js does its own ANSI emulation from the **raw output bytes** we record, so Octowright needs **no** pyte/`[emulator]` dependency. |

All uterm packages already depend on `provide-telemetry`, which Octowright also uses, so OTel spans nest cleanly.

### 3.2 Optional by design (plugin model)

uterm is a **completely optional** dependency. Once uterm is published to PyPI it is declared under `[project.optional-dependencies]` as the `terminal` extra — never the hard `dependencies` list:

```toml
[project.optional-dependencies]
terminal = ["provide-uterm-server>=0.4.0", "provide-uterm-platform>=0.4.0"]  # + asyncssh when SSH lands
```

> **Until uterm is published, this declaration is omitted entirely.** Listing not-yet-published packages anywhere in `pyproject.toml` makes `uv` try to resolve them from PyPI → 404 → every `uv run` and pre-commit hook fails (confirmed in execution). During development the packages are **editable-installed** from the sibling monorepo and detected at runtime via `is_available()`; the committed `pyproject.toml` stays clean. Adding the extra is a publish-time follow-up.

A browser-only install pulls **no** uterm, `asyncssh`, PTY/PAM, or AGPL code. The feature lights up iff the extra is installed — the same "present-or-absent" model as a capability profile:

- `octowright/terminal/__init__.py` stays import-light and exposes `is_available()` (tries `import provide.uterm.server.connectors`; no submodule imports, so it is safe to call when the extra is absent). The heavy submodules (`engine`, `pool`) are imported only when available.
- `server/_state.terminal_pool` is `TerminalPool | None` — instantiated only when `is_available()`.
- Terminal MCP tools register via a `register_terminal_tools(mcp)` hook the server calls **only when `is_available()`**; on a core install the tools simply don't appear (exactly like a profile-filtered tool). Keeping registration in an explicit hook — rather than import-time `@mcp.tool` side effects — is what makes a future **out-of-tree entry-point plugin** (`octowright-terminal` as its own distribution) able to register the identical way. v1 ships the in-tree extra; the entry-point discovery mechanism is a later, non-breaking addition.

This also sharpens the **licensing** boundary (§11): core never links uterm, so the AGPL code is pulled only by a user's explicit `[terminal]` opt-in. The reverse integration ("vice versa" — uterm shipping an optional `[browser]` extra that drives Octowright browsers) is symmetric and feasible with the same quarantine pattern, but lives in the uterm repo and is out of scope here.

---

## 4. Component design

### 4.1 `TerminalSession` (`terminal/session.py`)

A **parallel dataclass to `BrowserSession`**, not a subclass — `BrowserSession` (`session/core.py:51`) is welded to Playwright `page`/`context`/`browser` and mixes in browser-specific behavior. `TerminalSession` carries only the minimal shape the rest of Octowright depends on:

```python
@dataclass
class TerminalSession:
    instance_id: str
    kind: str  # always "terminal"
    label: str | None
    profile: str | None
    url: None  # always None; present so dashboard summaries are uniform
    recorder: Recorder
    log_path: Path
    protected: bool
    engine: TerminalEngine  # builds + drives a uterm SessionConnector

    async def close(self, *, force: bool = False) -> None: ...
    async def send_input(self, text: str) -> None: ...
    async def snapshot(self) -> dict[str, Any]: ...  # screen text + cursor
    async def read(self, *, since: int | None = None) -> dict[str, Any]: ...
    async def wait_for(
        self, *, prompt: str | None = None, text: str | None = None, timeout: float
    ) -> dict[str, Any]: ...
```

`protected`/`force` semantics are identical to browsers: close-capable tools refuse a protected session unless `force=True`; internal rollback/teardown uses `force=True`.

### 4.2 `TerminalPool` (`terminal/pool.py`)

Mirrors `BrowserPool`'s registry surface (`browser_pool/pool.py:46`) so the dashboard and scenario code treat it uniformly:

```python
class TerminalPool:
    _sessions: dict[str, TerminalSession]
    _sessions_lock: asyncio.Lock

    async def launch(self, **kwargs) -> dict[str, Any]: ...
    def get(self, instance_id: str) -> TerminalSession: ...
    def maybe_get(self, instance_id: str) -> TerminalSession | None: ...
    def iter_sessions(self) -> Iterable[TerminalSession]: ...
    def list_sessions(self) -> list[dict[str, Any]]: ...  # same keys BrowserPool returns
    async def spawn_roster(self, specs: list[dict]) -> dict[str, Any]: ...
    async def close(self, instance_id: str, *, force: bool = False) -> None: ...
    async def close_all(self, *, force: bool = False) -> None: ...
```

`list_sessions()` returns the same dict keys `BrowserPool.list_sessions()` does (`instance_id`, `kind`, `label`, `profile`, `url`, `log_path`, `har_path=None`, `protected`) so `/api/sessions` needs no terminal-specific shape.

### 4.3 `TerminalEngine` (`terminal/engine.py`)

Owns and drives one uterm `SessionConnector` directly — **no `HostedSessionRuntime`**:

- On construction, imports the needed connector modules (so `register_connector` runs) and calls `build_connector(instance_id, label, connector_type, connector_config)`.
- `start()`: `await connector.start()`, then launches a background **poll task** modeled on `HostedSessionRuntime._bridge_session` minus the WebSocket: loop `msgs = await connector.poll_messages()`, record each via `translate.py`; when `poll_messages()` returns empty, `await asyncio.sleep(0.05)` (the same anti-hot-spin backoff the runtime uses for pty/shell connectors).
- `send_input(text)`: apply redaction policy, record `terminal_input`, then `msgs = await connector.handle_input(text)`, record each returned message.
- Maintains a **latest-snapshot cache** updated from every `{type:"snapshot"}` message (and on demand via `await connector.get_snapshot()`), backing `snapshot()` and `wait_for()`.
- `wait_for(prompt|text, timeout)`: poll the latest snapshot's `screen` against a regex/substring until match or timeout.
- `stop()`: cancel the poll task, `await connector.stop()`.

All recording flows through the injected Octowright `Recorder` via `translate.py`; the engine never touches a uterm `RecordingStore`.

### 4.4 Recording translation (`terminal/translate.py`)

A pure function `record_message(recorder, msg)` maps one connector **worker-protocol message** (`dict` with a `type` key) to an Octowright `Recorder.record(action, **fields)` call. The `Recorder` is opened by the session at:

```python
new_log_path(defaults.RECORDINGS_DIR, instance_id, label, kind="terminal")
```

The engine emits three synthetic boundary actions itself: `terminal_start` (launch), `terminal_input` (each send), `terminal_stop` (close).

Worker-protocol message `type` → action:

| message `type` | Octowright action | fields carried in `**fields` |
|----------------|-------------------|------------------------------|
| `snapshot` | `terminal_output` | `screen` (text), `cursor`, `cols`, `rows`, `screen_hash`, `prompt_detected` |
| `term` (raw output) | `terminal_output` | `bytes_b64` (raw terminal bytes, for xterm.js) |
| `hello` | `terminal_event` | raw payload |
| `error` | `terminal_error` | `message` |
| *(any unmapped `type`)* | `terminal_event` | `uterm_type` (original `type`) + raw payload |

Synthetic engine-emitted actions: `terminal_start` (`connector_type`, `cols`, `rows`) · `terminal_input` (`keys`, or `"***"` + `byte_count` when masked) · `terminal_stop` (`reason`).

**The exact `snapshot`/`term` field set the PTY connector emits is pinned by Task 1's characterization test** — the connector's message shape lives in the unread half of `pty/connector.py`, so we confirm it empirically rather than guess. Raw output bytes (whichever field the connector uses) feed the xterm.js view and any replay.

**Drift safety:** the unmapped-`type` pass-through guarantees no message is ever silently dropped; a **contract test** pins the known mapping so a uterm upgrade that changes the message vocabulary surfaces in CI rather than as silent data loss.

### 4.5 Redaction (`terminal/redact.py`)

We do not use uterm's `SessionLogger`, so Octowright owns send redaction — re-implementing `HostedSessionRuntime`'s `_log_snapshot`/`_log_send` masking in ~5 lines:

- The engine tracks an `at_password_prompt` flag, set when the latest snapshot's `screen` matches uterm's prompt regex `(?i)(?:password|passphrase)[^\n]*:\s*$` (copied verbatim from `runtime.py:260`).
- On `send_input`, if `at_password_prompt` (or policy dictates), record `terminal_input` with `keys:"***"` + `byte_count` instead of the literal text — while the connector still receives the real bytes.
- Honor `OCTOWRIGHT_REDACT_INPUTS`: `off` records literal sends, default masks at detected password prompts and `password=`-sourced launches, `all` masks every send. Exact parity with the browser semantics is a planning detail.

---

## 5. MCP tool surface (`server/terminal/lifecycle.py`)

Same `@mcp.tool` pattern as `server/browser/lifecycle.py:42`, importing `mcp` and `terminal_pool` from `server/_state`. Each mutating tool publishes a dashboard invalidation (`publish_dashboard_invalidation_nowait("sessions")`) the way browser tools do.

| Tool | Signature (abridged) | Returns |
|------|----------------------|---------|
| `terminal_launch` | `kind="pty"\|"ssh", command=…, host=…, port=22, user=…, key_path=…, password=…, known_hosts=…, insecure_no_host_check=False, cols=80, rows=24, label=…, profile=…, persona=…, protected=False` | `{instance_id, kind, log_path, …}` |
| `terminal_send_input` | `instance_id, text` | `{ok, event_count}` (redaction applied) |
| `terminal_snapshot` | `instance_id` | `{screen, cursor, cols, rows}` |
| `terminal_read` | `instance_id, since=None` | `{output, cursor}` |
| `terminal_wait_for` | `instance_id, prompt=None, text=None, timeout=…` | `{matched, screen}` |
| `terminal_close` | `instance_id, force=False` | `{closed}` (refuses protected without force) |
| `terminal_list` | — | `[ {instance_id, kind, label, …} ]` |

**Profiles** (`server/profiles.py`): add a new `terminals` profile listing the `terminal_*` tools; also add the same names to the `scenarios` profile so scenario-driven terminals are usable under that profile. The always-on meta tools (`octowright_status`, etc.) already report the active profile.

---

## 6. Scenario participant integration

### Model changes (`scenarios.py`)

`Participant` (`scenarios.py:36`) gains terminal-relevant **optional** fields — browser participants are unaffected:

```python
command: str | None = None
host: str | None = None
user: str | None = None
key_path: str | None = None
cols: int | None = None
rows: int | None = None
```

`SUPPORTED_KINDS` (`defaults.py:214`) extends with `"terminal"`, so the `scenarios.py:64` validation accepts a terminal participant. `_validate_scenario` continues to log unknown *roles* without blocking.

### Launch fan-out (`scenarios_pool.py`)

The one real branch. `ScenarioPool.start()` currently does:

```python
result = await browser_pool.spawn_roster([resolve_launch_kwargs(p) for p in spec.participants])  # :178
```

Change to **partition by kind**:

```python
browser_specs = [resolve_launch_kwargs(p) for p in spec.participants if p.kind != "terminal"]
terminal_specs = [resolve_terminal_kwargs(p) for p in spec.participants if p.kind == "terminal"]
browser_result = await browser_pool.spawn_roster(browser_specs)
terminal_result = await terminal_pool.spawn_roster(terminal_specs)
# merge launched entries back, preserving participant order, into LiveScenario.participants
```

Merged participant entries keep the same shape (`{instance_id, persona, role, …}`), so downstream code is unchanged except session lookup.

### Session lookup (`scenarios_pool.py` run_macro / status / lookups)

Where the scenario pool currently calls `browser_pool.get(instance_id)`, resolve from whichever pool owns the session:

```python
session = browser_pool.maybe_get(id) or terminal_pool.maybe_get(id)
```

### Macros on terminals

A terminal participant accepts a constrained action vocabulary: send-input, wait-for-prompt/text, snapshot. A browser-only macro action (click, fill, navigate, …) targeted at a terminal role is an explicit error — never a silent no-op (consistent with the silent-swallow policy: user-action paths must surface failures).

---

## 7. Persona / SSH credentials

Per decision #4 — **explicit args are source of truth; persona supplies defaults.**

- Persona model gains an optional additive block: `ssh = {host, port, user, key_path, known_hosts}`. **No plaintext password is ever persisted** to a persona file.
- Resolution order for an SSH launch field (`host`, `port`, `user`, auth, `known_hosts`): explicit tool/participant arg → referenced persona's `ssh.*` → error if a required field is still missing.
- Standalone `terminal_launch` works with **no persona** (all explicit).
- A password is accepted only as a live argument (`terminal_launch(password=…)`), used for the session, and never written to disk in cleartext (redaction §4.5 covers the recording side).
- **Host-key safety:** the SSH connector refuses to connect without `known_hosts` unless `insecure_no_host_check=true`. `terminal_launch` surfaces both; the connector's `ValueError` is reported as a clear tool error (not a stack trace), nudging callers toward providing `known_hosts`.

This keeps the "same identity drives browser + terminal" story available without breaking surgery on the persona/credential schema.

---

## 8. Dashboard / frontend

### Backend (`http/routes/sessions.py`)

`list_sessions` (`:41`) merges both pools:

```python
live = [_live_summary(s) for s in state.pool.iter_sessions()] + [
    _live_summary(s) for s in state.terminal_pool.iter_sessions()
]
```

Because `TerminalSession` exposes the same summary keys, `_live_summary` and the session-detail route work unchanged. Terminal recordings are discovered as closed sessions the same way browser recordings are (same `RECORDINGS_DIR`, `kind="terminal"` in the filename).

### Frontend (`packages/octowright-frontend`)

- Add **`@xterm/xterm`** as a direct dependency (the OSS lib; **not** uterm's frontend SPA — preserves the quarantine).
- The session debugger renders a **terminal view** when `kind === "terminal"`:
  - **Live:** subscribe to the existing `/tail` WebSocket, filter `terminal_output` records, base64-decode `bytes_b64`, `term.write()` into an xterm instance.
  - **Replay:** read the recorded `terminal_output` byte stream in order and feed a fresh xterm instance.
  - The generic **action timeline** still renders `terminal_input` / `terminal_output` / `terminal_*` rows beside the screen.
- `src/types.ts` gains the terminal action shapes mirroring the recorder fields.

This is the largest single chunk of v1 work and the main schedule risk.

---

## 9. Error handling, lifecycle, containment

- **Disk-write containment:** terminal recording paths are anchored under `defaults.RECORDINGS_DIR` via `new_log_path` (same contract as browsers). `key_path` is read-only and never copied into the recordings tree.
- **External-close eviction:** connector EOF / SSH drop / child exit → the session self-evicts from `TerminalPool`, records `terminal_stop`, and publishes a dashboard invalidation — mirroring browser external-close eviction (`browser_pool/listeners.py`).
- **Daemon shutdown / `octowright restart`:** terminal sessions close during pool teardown with `force=True` (internal-rollback pattern).
- **Telemetry:** new spans `octowright.terminal.launch` / `.send_input` / `.close`, and a metric family `octowright_terminal_launched_total` / `_closed_total` / `_launch_failed_total{kind,error}`, consistent with the existing browser instruments.

---

## 10. Open items to verify at planning time (not design blockers)

Resolved during source verification (2026-06-12): connector registration happens via `register_connector` on connector-module import (PTY exposes `_register()`; the engine imports the module to trigger it); there is **no** `SessionDefinition`/`RecordingConfig` — `build_connector` takes `(session_id, display_name, type, config)`; I/O is driven directly off the `SessionConnector` (`handle_input`/`poll_messages`/`get_snapshot`/`stop`). Remaining:

1. **PTY connector output-message shape** — the exact `type`/fields the PTY connector emits from `poll_messages()` / `handle_input()` / `get_snapshot()` (raw `term` frames vs. `snapshot` frames, and where the raw output bytes live). **Pinned empirically by Task 1's characterization test**; `translate.py` is written against the pinned shape.
2. **`wait_for` substrate** — v1 uses a simple regex/substring match against the latest snapshot's `screen`. The richer uterm `DetectionEngine`/`FlowEngine` is deferred (no rules-file dependency in v1).
3. **PTY `ECHO` disabled** — the connector clears the PTY `ECHO` bit (uterm's frontend renders its own echo). The Octowright xterm.js view must echo `terminal_input` itself, or accept no input echo — decide during the dashboard task. Agent-facing `snapshot()`/`wait_for` are unaffected (they read program output).

---

## 11. Prerequisites

- **Licensing (gating):** `provide-uterm` is AGPL-3.0-or-later; Octowright is Apache-2.0. Before Octowright takes the dependency, add a **GPLv3 §7 "Additional Permission" (linking exception)** to provide-uterm permitting combination with other provide.io llc software. (Owner controls both projects; this is a policy action, not a technical blocker.) Scope it to the combination for v1; document where it lives in uterm's `LICENSES/`.

---

## 12. Testing strategy

- **Characterization:** drive a real PTY connector via `build_connector` against `/bin/echo`/`/bin/sh` and assert the emitted worker-protocol message shape — pins the input contract for `translate.py`.
- **Unit:** the `translate.py` message→action mapping (table-driven, incl. the unmapped-`type` pass-through and the drift contract test); redaction policy (password-prompt detection + `OCTOWRIGHT_REDACT_INPUTS` modes); persona/explicit resolution order.
- **Connector-level:** PTY against a real local `/bin/sh` (no network, current user, no PAM); SSH against a loopback sshd fixture or mocked `asyncssh` (CI-friendly, no external host); assert the `known_hosts`-missing refusal.
- **Tool-level:** `terminal_launch → send_input → wait_for → snapshot → close` happy path; protected-refusal and `force=True`.
- **Scenario:** a mixed scenario (1 browser + 1 terminal participant) starts, both appear in status, a macro runs against each role; terminal-targeted browser action raises.
- **Frontend:** vitest for the terminal view (recorded bytes → screen contents); reuse the existing frontend test harness.
- **Quality gates:** the new code passes the existing `make lint` stack (ruff/format/mypy/ty/bandit/codespell/SPDX/LOC/vulture/xenon/secrets) and `make test`.

---

## 13. Build order (for the implementation plan)

1. Licensing prerequisite (uterm linking exception).
2. **Characterization spike** — drive a real PTY connector via `build_connector` and pin its message shape (feeds `translate.py`).
3. `translate.py` + `redact.py` + `TerminalEngine` + `TerminalSession`/`TerminalPool` — the in-process primitive, driveable from a unit test.
4. `terminal_*` MCP tools + `terminals`/`scenarios` profile entries.
5. Scenario participant model + fan-out partition + session lookup.
6. Dashboard backend merge + frontend xterm.js view (largest chunk).
7. Telemetry spans/metrics.
8. Tests + docs (CLAUDE.md "Five Concepts" → add terminal session; env-var table; profile table).
