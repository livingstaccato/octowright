# Troubleshooting

## Engine Launch Failures

Symptoms:
- browser launch tools fail immediately
- Playwright binary/runtime errors

Checks:
1. `uv run playwright install webkit firefox chromium`
2. `browser_engine_status`
3. `browser_engine_reinstall` (specific engine)

## Scenario/Macro Failures

Symptoms:
- selectors not found
- flow drift after site update

Checks:
1. `browser_snapshot` and inspect aria-tree state.
2. re-run with `macro_lint` against saved macro.
3. update/re-record macro rather than patching brittle selectors.

## Persona Credential Resolution Problems

Symptoms:
- startup macros fail at login fields
- missing secret/env references

Checks:
1. `persona_credentials_check` before launch.
2. verify referenced env vars and command-based credential providers.

## Golden Verification Mismatches

Symptoms:
- `golden_assert` failures
- `golden_verify_loop` diffs

Checks:
1. confirm expected page state and timing.
2. in local/dev only, optionally bootstrap missing baseline via `golden_verify_loop(save_if_missing=true)`.
3. in CI, keep verify-only policy (`save_if_missing` is refused under `CI=true`).

## Dashboard/HTTP Issues

Symptoms:
- no UI on expected port
- SSE/WebSocket streams appear stale

Checks:
1. call `octowright_dashboard_url`.
2. confirm port/host env vars (`OCTOWRIGHT_HTTP_HOST`, `OCTOWRIGHT_HTTP_PORT`).
3. verify no conflicting process is already bound on the selected port.
