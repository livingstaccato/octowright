# Personas and Scenarios

Status: draft
Date: 2026-04-24
Scope: single spec, single implementation plan.

## Context

Two pressures converged on the profile subsystem:

1. Today's profile layout is **engine-first** (`~/.config/undef/profiles/<kind>/<name>/`).
   A profile is a `(kind, name)` pair and its value is limited to whatever Playwright
   writes into the user-data-dir. Human identity is scattered: "dante's webkit state"
   and "dante's chromium state" are unrelated directories with no place to put
   non-browser data about dante.
2. There is no way to bring up a coordinated group of browsers — e.g., seven
   Discord players plus one monitoring window plus one main-site spectator — as a
   reproducible unit. `spawn_roster` launches a flat list of specs; after it
   returns there is no handle on the group, no lifecycle, no shared setup or
   teardown, no filtering by role.

This spec addresses both: flip the profile layout to **persona-first**, introduce
**scenarios** as a declarative grouping primitive with its own lifecycle, and
resolve two open issues already tagged for cleanup (`session.py` at 536 lines and
popup-page listener inheritance).

## Goals

- A persona is a first-class identity with metadata, credential references, and
  arbitrary sidecar files, independent of which browser engine it's currently
  using.
- Scenarios bring up N coordinated browser instances with per-participant roles,
  startup macros, and shared fixtures; they stay alive after `start` so humans
  and Claude can drive individual participants interactively.
- Scenarios are authored in either YAML (declarative) or Python (dynamic /
  programmatic) from day one.
- Scenarios optionally emit pass/fail + JUnit XML when asked to run as tests,
  but default to "bring up the world and hand me the keys."
- The existing tool surface (`browser_*`, `macro_*`, `golden_*`, etc.) continues
  to work unchanged on every participant inside a scenario.
- Existing profiles on disk migrate cleanly to the new layout with no manual
  intervention required.

## Non-goals

- Replacing macros. Scenarios orchestrate; macros remain the per-browser action
  primitive.
- Deep security around credentials. Credentials are stored **by reference only**
  (env var or shell command) — octowright never writes secrets to disk.
- Cross-machine portability of persona dirs. Like today's profiles, personas are
  local-machine state.
- Auto-selection of personas for a scenario. A scenario spec names the personas
  it wants; personas must exist when the scenario starts or start fails.

## Persona-first profile layout

### On-disk shape

```
~/.config/undef/profiles/
├── dante/
│   ├── profile.yaml          # persona metadata (octowright-owned)
│   ├── avatar.png            # arbitrary persona files (octowright-ignored)
│   ├── notes.md
│   ├── webkit/               # Playwright user-data-dir for dante on WebKit
│   ├── chromium/             # Playwright user-data-dir for dante on Chromium
│   └── firefox/
└── tim/
    ├── profile.yaml
    └── webkit/
```

The persona root holds metadata and any free-form files. Each engine
subdirectory is a pure Playwright `user_data_dir` — octowright writes nothing
there. `profile.yaml` is the only octowright-owned file inside a persona dir.

### `profile.yaml`

```yaml
name: dante                          # must match the slug used as dir name
display_name: Dante Alighieri        # human-readable; used in window title + reports
default_url: https://discord.com/app
default_macros: [discord-login]      # auto-run after launch
credentials:
  email_env: DANTE_EMAIL             # resolved via os.environ at use time
  password_cmd: "op read op://Personal/dante/password"  # resolved via shell capture
app:                                  # free-form; ignored by octowright
  discord_user_id: "1234"
  role: player
```

Required fields: `name`. Everything else is optional. Unknown keys under `app`
are preserved verbatim.

### Credential resolution

Credentials are **reference-only**. At macro-run time, when a macro or a
scenario needs a credential field, octowright calls
`resolve_credential(persona, field)`:

- `<field>_env` → reads `os.environ[value]`. Missing env var raises a clear
  error (`"persona dante needs DANTE_EMAIL but env is unset"`).
- `<field>_cmd` → runs the shell command via `subprocess.run(shell=True,
  capture_output=True, check=True)`, strips trailing whitespace, returns stdout.
  Non-zero exit raises a clear error with stderr excerpt.
- If both are set for the same field: `*_cmd` wins, with a warning.
- If neither is set and the consumer needs it: raise `MissingCredential` naming
  the persona and the field.

Resolved credentials are returned as plain strings, never written to disk by
octowright, never echoed in logs (masked as `<redacted>` in telemetry events).

### Terminology

- **Persona** — a named identity (`dante`, `tim`). Owns metadata + files at
  `<PROFILES_DIR>/<persona>/`.
- **Engine profile** — a `(persona, kind)` pair; resolves to
  `<PROFILES_DIR>/<persona>/<kind>/`. That directory is the Playwright
  `user_data_dir`.

### Migration

On import of `personas.py`, a one-shot helper detects legacy layout
(`profiles/<kind>/<name>/` directories with no sibling `<name>/profile.yaml` at
the persona root) and moves contents into the new shape. Idempotent. Emits a
telemetry event per migrated persona.

Also exposed as `octowright migrate-profiles` CLI subcommand and as a
`migrate_profiles` MCP tool for explicit invocation. Running after migration is
already complete is a no-op.

No dual-layout support — one shot, then the old layout stops existing.

## Scenarios

### Definition

A scenario is a named, live, stateful group of browser instances. `scenario_start`
brings up every participant in parallel, applies shared fixtures, runs
per-participant startup macros, and returns. **Browsers stay open.** The scenario
remains tracked by octowright until `scenario_stop` (or process exit).

Inside a live scenario, every existing tool works per-participant by
`instance_id`. A participant is a normal `BrowserSession`; being in a scenario
adds group semantics but never restricts single-instance control.

### Storage

- `OCTOWRIGHT_SCENARIOS_DIR` env var; default `~/.config/undef/scenarios/`.
- One scenario per file: either `<name>.yaml` or `<name>.py`.
- If both exist for the same name, `.py` wins with a warning.
- Filename stem is the scenario name. Directory nesting not supported in v1.

### YAML form

```yaml
name: discord-raid
description: 7 players + 1 monitor + 1 main-site spectator
participants:
  - persona: dante
    kind: webkit
    role: player
  - persona: tim
    kind: chromium
    role: player
  - persona: player-3
    kind: webkit
    role: player
  - persona: player-4
    kind: firefox
    role: player
  - persona: player-5
    kind: webkit
    role: player
  - persona: player-6
    kind: chromium
    role: player
  - persona: player-7
    kind: firefox
    role: player
  - persona: ops
    kind: firefox
    role: monitor
    url: https://warp.undef.games/monitor   # explicit override of persona default
    startup_macros: []                       # explicit empty overrides persona default
  - persona: observer
    kind: webkit
    role: main-site
    url: https://example.com/
fixtures:
  mock_routes:
    - pattern: "**/api/time"
      status: 200
      body: '{"now": "2026-04-24T00:00:00Z"}'
  dialog_policy: dismiss
teardown:
  macro: cleanup-session        # runs per-participant on scenario_stop
verify:                         # optional; used only by scenario_run_as_test
  player:  assert-in-server
  monitor: assert-monitor-healthy
```

### Python form

```python
# scenarios/discord-raid.py
from octowright.scenarios import Scenario, Participant

def build() -> Scenario:
    return Scenario(
        name="discord-raid",
        participants=[
            Participant(persona="dante", kind="webkit", role="player"),
            *[
                Participant(persona=f"player-{i}", kind="webkit", role="player")
                for i in range(3, 8)
            ],
            Participant(persona="ops", kind="firefox", role="monitor",
                        url="https://warp.undef.games/monitor", startup_macros=[]),
            Participant(persona="observer", kind="webkit", role="main-site",
                        url="https://example.com/"),
        ],
        fixtures={
            "mock_routes": [
                {"pattern": "**/api/time", "status": 200,
                 "body": '{"now": "2026-04-24T00:00:00Z"}'},
            ],
            "dialog_policy": "dismiss",
        },
        teardown_macro="cleanup-session",
        verify={"player": "assert-in-server", "monitor": "assert-monitor-healthy"},
    )
```

The loader imports the module and calls `build()`, which returns a `Scenario`
dataclass. Any exception during import/build surfaces immediately.

### Resolution order for per-participant fields

For `url`, `startup_macros`, `viewport_w/h`, `stabilize`, `record_video`, `trace`:

    participant override  →  persona default (profile.yaml)  →  scenario default  →  global default

First non-None wins.

### Roles

Free-form strings. Conventions (`player`, `monitor`, `admin`, `main-site`) are
documentation; octowright does not validate. Used for:

- Filtering: `scenario_participants(id, role="player")`.
- Routing: `scenario_run_macro(id, macro, role="player")` runs only against
  participants with that role. If no participant matches, the call succeeds with
  an empty result (not an error).
- Per-role verify macros (see test mode below).

`participant.role` (set in the scenario spec) is distinct from `profile.yaml`'s
`app.role` (free-form persona metadata, octowright-opaque). Scenario operations
key off participant.role only.

### Lifecycle

1. **`scenario_start(name)`** — load spec, resolve persona defaults, launch all
   participants via `spawn_roster` in parallel, apply shared fixtures to each
   participant, run `startup_macros` per-participant. Returns
   `{scenario_id, name, participants: [{instance_id, persona, kind, role, url, ...}]}`.
   `scenario_id` is a short opaque string (12-hex like instance_id) used by every
   other scenario_* tool. Non-blocking; browsers remain open.
2. **`scenario_status(id)`** — live view of active scenarios and their
   participants.
3. **`scenario_list()`** — on-disk scenario specs (not live state).
4. **`scenario_run_macro(id, macro, role=None, args={})`** — replay a macro
   against every participant (optionally role-filtered). Parallel via
   `asyncio.gather(..., return_exceptions=True)`; per-participant pass/fail
   collected and returned.
5. **`scenario_stop(id)`** — run teardown macro per-participant (if any), close
   every participant's browser, remove scenario from live tracking. Returns a
   summary including any teardown failures.
6. **Process exit** — any scenarios still active are teardown'd best-effort.

### Optional test mode

`scenario_run_as_test(id)` (MCP tool) and `octowright scenario start <name> --test
[--out <xml>]` (CLI) block until every participant passes its role's `verify`
macro or a timeout fires. Pass/fail per-participant. If `--out` supplied, emits
JUnit XML (same schema as `run_test_suite`). If the scenario declares no
`verify` mapping, the tool errors rather than passing silently.

## Tool + CLI surface

### New MCP tools (13)

| Tool | Purpose |
|---|---|
| `persona_list` | Enumerate personas with engines, metadata, last-used |
| `persona_get(name)` | Full `profile.yaml` dict; credentials masked |
| `persona_create(name, display_name?, default_url?, ...)` | Scaffold new persona dir + stub `profile.yaml` |
| `persona_delete(name)` | Wipe persona (all engines + metadata). Refuses while any engine profile is in live use. |
| `migrate_profiles()` | One-shot legacy → persona-first migration. Idempotent. |
| `scenario_list` | On-disk scenario specs |
| `scenario_start(name)` | Launch the scenario; return participant table |
| `scenario_status(id?)` | Live active scenarios; detailed view if `id` supplied |
| `scenario_stop(id)` | Teardown + close |
| `scenario_run_macro(id, macro, role?, args?)` | Broadcast a macro across participants |
| `scenario_run_as_test(id, out_path?)` | Block until verify macros pass; pass/fail + optional JUnit |
| `scenario_participants(id, role?)` | List participants, optionally role-filtered |

Existing `profile_list` / `profile_delete` stay but rescoped to engine-profile
operations (they become thin wrappers that delegate to the engine-profile layer
of `personas.py`). `browser_launch(profile=<name>)` keeps its signature — the
name now resolves to a persona and the engine subdir is used as
`user_data_dir`.

### New CLI subcommands

```
octowright persona list
octowright persona show <name>
octowright persona create <name> [--display <str>] [--url <str>]
octowright persona delete <name>
octowright scenario list
octowright scenario start <name> [--attach] [--test [--out <file>]]
octowright scenario stop <id>
octowright migrate-profiles
```

`scenario start` is non-blocking by default (prints participant table, returns;
browsers stay up). `--attach` blocks the CLI until Ctrl-C, then runs teardown
and exits. `--test` enables test mode with optional JUnit XML output.

## Module map

| Module | Status | Owns |
|---|---|---|
| `src/octowright/personas.py` | new | `Persona` dataclass, `load_persona`, `list_personas`, `delete_persona`, `resolve_credential`, migration helper |
| `src/octowright/profiles.py` | refactor | Engine-profile path resolution only — `engine_profile_dir(persona, kind)`, `list_engine_profiles(persona=None)`. Persona-level CRUD moves to `personas.py` |
| `src/octowright/scenarios.py` | new | `Scenario` / `Participant` dataclasses, YAML + Python loaders, `ScenarioPool` (live tracking), `scenario_start/stop/status/run_macro/run_as_test` |
| `src/octowright/session_frames.py` | new | iframe helpers (`switch_frame`, `reset_frame`, `list_frames`) extracted from `session.py` |
| `src/octowright/session_downloads.py` | new | download helpers extracted from `session.py` |
| `src/octowright/session_locators.py` | new | `_locator`, `click_by`, `fill_by`, `get_text_by` extracted from `session.py` |
| `src/octowright/session.py` | split | Core `BrowserSession` state + base methods. Delegates to the three helper modules. Target ≤300 lines. |
| `src/octowright/pool.py` | small change | Extract `_wire_listeners(session, page)` and call from both initial-page path and `session._register_popup`. Fixes popup-frame listener inheritance. No other changes. |
| `src/octowright/server.py` | append | New scenario_* and persona_* tools appended at EOF. |
| `src/octowright/cli.py` | append | New `persona`, `scenario`, `migrate-profiles` subcommand groups |
| `src/octowright/runner.py` | unchanged | Continues serving `run_test_suite` on macros. `scenario_run_as_test` shares JUnit writer. |
| `src/octowright/defaults.py` | small change | Add `SCENARIOS_DIR`, `OCTOWRIGHT_SCENARIOS_DIR` env var |

One potential new runtime dependency: `pyyaml>=6`. Implementation must check
whether it's already installed transitively (`provide-telemetry` may pull it);
if not, add it to `[project.dependencies]` in the same commit as `scenarios.py`.
No other new deps.

## Open fixes resolved as part of this work

1. **`session.py` at 536 lines** — resolved by extracting iframe / download /
   locator helpers into sibling modules. `BrowserSession` delegates; public
   methods and signatures unchanged.
2. **Popup-frame listener inheritance** — resolved by extracting
   `_wire_listeners(session, page)` in `pool.py` and calling it both for the
   initial page and from `_register_popup`. Dialog + download listeners now
   attach to every page the session owns.

## Test plan

### Unit tests (pytest, no real browser)

- `tests/test_personas.py` — YAML load and dump round-trip, credential
  resolution for env / cmd / missing / both-set, slug rules, migration helper
  (uses `tmp_path`).
- `tests/test_scenarios.py` — YAML + Python loaders, default-resolution order,
  role filtering, teardown wiring, verify-mapping round-trip, error paths for
  missing persona / duplicate participants / unknown fields.
- Existing `tests/test_iframes.py`, `tests/test_downloads.py`,
  `tests/test_locators.py` — must keep passing unmodified after the
  `session.py` split; verifies the split is a pure refactor.

### Integration tests (live headless browsers)

- `tests/test_scenarios_live.py` — start a 2-participant scenario against
  `about:blank`, inject a trivial HTML form, apply a shared `mock_route`
  fixture, call `scenario_run_macro` to broadcast a navigation, `scenario_stop`
  runs teardown cleanly. Asserts: every participant closed, teardown macro ran
  per-participant, recordings have per-participant `scenario_id` metadata.
- `tests/test_popup_listeners.py` — launch one browser, open a popup via
  `window.open`, trigger a `confirm()` in the popup. Asserts the session's
  dialog handler fires on the popup page (exercises the popup-frame fix).

### Migration test

- `tests/test_migration.py` — create a tmp profiles dir with legacy layout
  (`profiles/webkit/alice/` with a stub file), call `migrate_profiles()`,
  assert new layout (`profiles/alice/webkit/` + `profiles/alice/profile.yaml`
  with `name: alice`). Re-run migration; asserts idempotent.

## Acceptance criteria

1. All 236 pre-existing tests pass unchanged.
2. New unit + integration + migration tests pass.
3. `session.py` is ≤ 300 lines after the split.
4. `persona_list` and `scenario_list` return usable data on a freshly-created
   setup.
5. Given 7 personas with `profile.yaml` (each declaring a `default_macros: [login]`
   hook, env-var credentials) + one `discord-raid.yaml` scenario:
   `octowright scenario start discord-raid` brings up 9 headed windows, each
   with the right title prefix, each running its persona's `default_macros`.
   `octowright scenario stop <id>` closes every window cleanly. Browsers stayed
   open between start and stop for manual interaction.
6. A popup opened from inside a scenario participant fires the dialog listener
   (proves the popup-page fix).
7. `scenario_run_as_test discord-raid --out report.xml` produces a valid JUnit
   XML file with one testcase per participant.

## Known risks / deferrals

- **Scenarios and `--attach` interactions with Ctrl-C** — need to install a
  signal handler that runs teardown before the CLI exits. Straightforward but
  non-trivial; called out explicitly in the implementation plan.
- **Python scenarios and import side effects** — arbitrary Python executes at
  scenario-load time. Document this. No sandboxing planned.
- **Teardown failures** — if teardown raises, the scenario still tears down as
  completely as possible; failures are collected and returned in the stop
  summary rather than aborting the cleanup.
- **Cross-session scenario persistence** — a scenario started in one process
  does not survive that process dying. If the user wants persistence, they need
  the CLI `--attach` form or a supervisor. Out of scope for v1.
