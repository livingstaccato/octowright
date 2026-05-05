# Scenarios

Scenarios orchestrate multiple participants (persona + engine + role) as a single run unit.

## Core Tools

- `scenario_list`
- `scenario_plan`
- `scenario_start`
- `scenario_status`
- `scenario_participants`
- `scenario_run_macro`
- `scenario_run_as_test`
- `scenario_tail`
- `scenario_stop`

## Files

Default location:

- `~/.config/octowright/scenarios/`

Supported forms:

- YAML specs
- Python specs exposing `build()`

## Lifecycle

1. `scenario_plan` to inspect resolved launch kwargs without launching.
2. `scenario_start` to launch participants.
3. `scenario_run_macro` optionally broadcast by role.
4. `scenario_run_as_test` for verification-mode pass/fail + JUnit.
5. `scenario_stop` for teardown.

## CLI

```bash
uv run octowright scenario list
uv run octowright scenario start <name> --watch
uv run octowright scenario start <name> --test --out dist/scenario.xml
```
