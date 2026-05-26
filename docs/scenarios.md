# Scenarios

A **scenario** is a pre-declared group of personas launched together as a single
unit. Spin up *N* players + a monitoring window + a main-site spectator with one
call; each instance is a regular `BrowserSession` you can drive per-participant
(via `instance_id`) using all the normal `browser_*` tools.

Scenarios are the right abstraction whenever you need multiple coordinated
browsers: load tests, multi-account validation, watching a feature from multiple
viewing angles at once, or end-to-end verification flows.

The scenario tools (`scenario_list`, `scenario_plan`, `scenario_start`,
`scenario_status`, `scenario_stop`, `scenario_participants`, `scenario_run_macro`,
`scenario_tail`, `scenario_wait_for_sync`, `scenario_run_as_test`,
`scenario_remap_participants`, `scenario_spawn_template`) belong to the
`scenarios` capability profile. By default every tool registers; under
`--profile=core` they are not visible — combine with `--profile=core,scenarios`
to keep them. See [getting-started.md](getting-started.md#slimming-the-llm-tool-surface).

## Where scenarios live

Default location: POSIX uses the XDG config dir
`${XDG_CONFIG_HOME:-~/.config}/octowright/scenarios/`; Windows uses
`%APPDATA%\octowright\scenarios\`. Override with `OCTOWRIGHT_SCENARIOS_DIR`.

Two supported formats:

- **YAML** — `<name>.yaml` for static rosters.
- **Python** — `<name>.py` exposing `def build() -> Scenario` for dynamic rosters
  (e.g. participant counts driven by env vars).

## Scenario spec

```yaml
name: discord-raid
description: 7 players + 1 monitor + 1 main-site spectator

participants:
  - persona: dante
    kind: webkit
    role: player
  - persona: ops
    kind: firefox
    role: monitor
    url: https://octowright.com/monitor

fixtures:
  mock_routes:
    - pattern: "**/api/time"
      body: '{"now":"2026-04-24T00:00:00Z"}'
  dialog_policy: dismiss

teardown:
  macro: cleanup-session

verify:
  player: assert-in-server
  monitor: assert-monitor-healthy
```

Field reference:

| Field | Purpose |
|---|---|
| `name` | Scenario slug (filename stem). |
| `description` | Free-form prose shown in `scenario_list`. |
| `participants[]` | Persona + engine + role; optional per-participant `url` override. |
| `participants[].role` | One of `player`, `monitor`, `spectator`. Used for `role=`-filtered macro broadcast. |
| `fixtures.mock_routes` | Network stubs applied to every participant before startup macros. |
| `fixtures.dialog_policy` | Default `accept`/`dismiss`/`manual` for `confirm()`/`alert()`/`prompt()`. |
| `teardown.macro` | Macro to run on each participant during `scenario_stop`. |
| `verify.<role>` | Macro to run on participants of that role during `scenario_run_as_test`. |

## Lifecycle

Five phases, each its own MCP tool:

1. **`scenario_plan`** — dry run. Resolves every persona, computes the
   per-participant `launch_kwargs`, returns the plan **without launching anything**.
   Use this to validate before committing real browser windows.
2. **`scenario_start <name>`** — launches all participants in parallel, applies
   fixtures, runs each persona's `default_macros`. **Browsers stay open.**
3. **`scenario_run_macro <id> <macro> [role=...]`** — broadcasts a macro across
   participants (optionally role-filtered). Returns per-participant results.
   Equivalent for individual control: drive one `instance_id` directly with the
   normal `browser_*` tools.
4. **`scenario_run_as_test <id>`** — runs each role's `verify` macro and emits
   pass/fail + JUnit XML. The CLI equivalent is `--test --out <xml>`.
5. **`scenario_stop <id>`** — runs the teardown macro per participant, closes
   every window, returns a summary.

`scenario_status` and `scenario_tail` give you live introspection while the
scenario is running.

## Tools

| Tool | Purpose |
|---|---|
| `scenario_list` | Enumerate every scenario spec on disk. |
| `scenario_plan` | Dry-run: show resolved per-participant `launch_kwargs` without launching. |
| `scenario_start` | Launch all participants in parallel; apply fixtures; run startup macros. |
| `scenario_status` | Snapshot a running scenario's participant state. |
| `scenario_participants` | List a running scenario's participants and their `instance_id`s. |
| `scenario_run_macro` | Broadcast a macro across participants (optionally role-filtered). |
| `scenario_run_as_test` | Run `verify` macros and emit JUnit-compatible pass/fail. |
| `scenario_tail` | Stream participant events to a single combined log. |
| `scenario_stop` | Run teardown, close windows, return a summary. |

## CLI

```bash
uv run octowright scenario list
uv run octowright scenario start <name> --watch
uv run octowright scenario start <name> --test --out dist/scenario.xml
```

The `start` command blocks until Ctrl-C, then runs teardown and exits.
`--watch` streams participant events to stdout in real-time.

## Related

- [personas.md](personas.md) — every participant references a persona by `name`.
- [macros.md](macros.md) — startup, teardown, and verify hooks all reference
  saved macros.
- [troubleshooting.md](troubleshooting.md#scenariomacro-failures) — when
  participants drift from each other.
