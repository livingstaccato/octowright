# Troubleshooting

Common failure modes and the fastest path to a fix. Each section lists symptoms,
then a numbered diagnosis sequence.

## Engine launch failures

**Symptoms**

- `browser_launch` fails immediately, before any page loads.
- Stderr mentions a Playwright binary, runtime, or download error.

**Diagnosis**

1. Reinstall the engine binaries:

   ```bash
   uv run playwright install webkit firefox chromium
   ```

2. Call `browser_engine_status` to confirm the install succeeded and matches
   the Playwright version Octowright is using.

3. If a single engine is broken, run `browser_engine_reinstall` against just
   that one rather than blowing away all three.

## Scenario / macro failures

**Symptoms**

- Selectors that worked yesterday no longer find their target.
- Macros stop replaying mid-flow.
- Drift after a target site updates.

**Diagnosis**

1. Run `browser_snapshot` on the live page and inspect the accessibility tree.
   Hand-translate the macro's selectors against what the snapshot actually
   contains.

2. Run `macro_lint <name>` on the saved macro — the linter catches missing
   fields, unknown actions, and empty branches that often appear during a
   bad edit.

3. **Re-record rather than hand-patching brittle selectors.** Sites that ship
   randomly-rotated CSS classes (Discord, Slack, Linear) make hand-patching a
   losing battle.

## Persona credential resolution problems

**Symptoms**

- Startup macros fail at the login fields.
- An `*_env` reference resolves to an empty string.
- An `*_cmd` reference exits non-zero.

**Diagnosis**

1. Run `persona_credentials_check name=<persona>` *before* launching anything.
   The report names every reference and its resolution status without leaking
   the resolved value.

2. For `*_env`: verify the variable is exported in the shell that spawned
   `octowright serve` (not just your interactive shell — the daemon may have
   inherited a stale environment).

3. For `*_cmd`: run the command directly in the daemon's shell to confirm it
   exits 0 and prints the secret to stdout.

## Golden verification mismatches

**Symptoms**

- `golden_assert` raises on a page that *looks* correct visually.
- `golden_verify_loop` diffs change after every run.

**Diagnosis**

1. Confirm the page state and timing match what was captured. Many false
   positives come from running the verify before the page has settled.

2. **Local/dev only**: if a baseline is genuinely missing, bootstrap it with
   `golden_verify_loop(save_if_missing=true)`.

3. **In CI**: keep the verify-only policy. `save_if_missing` is **refused
   under `CI=true`** by design — silently minting baselines defeats regression
   coverage.

## Dashboard / HTTP issues

**Symptoms**

- The dashboard URL doesn't resolve.
- Live event streams (SSE / WebSocket) appear stale.

**Diagnosis**

1. Call `octowright_dashboard_url` from your MCP client to get the current
   bind address — Octowright may have walked up to a higher port if the
   default was busy.

2. Confirm the bind address matches your env: `OCTOWRIGHT_HTTP_HOST` and
   `OCTOWRIGHT_HTTP_PORT`.

3. Verify no other process is bound on the selected port:

   ```bash
   lsof -nP -iTCP:8765 -sTCP:LISTEN
   ```

If a process other than Octowright owns the port, either kill it or set
`OCTOWRIGHT_HTTP_PORT` to a free port and restart `octowright serve`.
