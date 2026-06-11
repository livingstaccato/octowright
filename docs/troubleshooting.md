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

2. Confirm binaries are present for your Playwright version:

   ```bash
   uv run playwright install --list
   ```

3. If a single engine is broken, reinstall just that one:

   ```bash
   uv run playwright install webkit
   # or firefox / chromium
   ```

See [getting-started.md](getting-started.md#1-install) for the canonical
engine install workflow and note on CLI-only engine management.

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
   lsof -nP -iTCP:6286 -sTCP:LISTEN
   ```

If a process other than Octowright owns the port, either kill it or set
`OCTOWRIGHT_HTTP_PORT` to a free port and restart `octowright serve`.

## "Tool not found" — capability profile filtering

**Symptoms**

- The LLM reports a tool it expected (e.g. `macro_save`, `scenario_start`)
  is not available.
- `octowright selftest` shows a smaller-than-usual tool count.

**Diagnosis**

1. Run `octowright selftest`. The first lines show `active profile:` and the
   total tool count. If the active profile is anything other than `all`, the
   filter is in play.

2. From within an MCP session, call the `octowright_status` tool — its
   `profile` block reports `active`, `filter_active`, `tool_count`, and the
   list of `available_profiles`.

3. To restore the full surface, unset `OCTOWRIGHT_PROFILE` (or set it to
   `all`) and restart the daemon, OR add the missing profile to the spec
   (e.g. `--profile=core,macros` to add macros back).

The profile mapping lives at `src/octowright/server/profiles.py`. See the
[Capability profiles](getting-started.md#slimming-the-llm-tool-surface)
section in the getting-started guide for worked examples.

## MCP transport closed or timed out

**Symptoms**

- An MCP client reports `Transport closed`.
- A browser launch or status call times out even though the dashboard may still
  answer.

**Diagnosis**

1. Check daemon health directly:

   ```bash
   curl http://127.0.0.1:6286/api/health
   ```

2. If health is good, retry one Octowright MCP call. The follower bridge keeps
   the local stdio process alive and reconnects to the current leader. Since
   v0.9.0 an interrupted in-flight `tools/call` is **safely auto-resumed** on the
   new session (the leader dedups it via an injected idempotency key, so a
   side-effectful call runs at most once); calls that can't be resumed still fail
   with an explicit JSON-RPC bridge error.

3. If the same client handle still fails, run the fresh-client smoke proof:

   ```bash
   uv run --active python scripts/bridge_reconnect_smoke.py
   ```

   If this succeeds, the daemon and a new MCP client are healthy; the remaining
   failure is isolated to the already-attached client handle.

4. Run `octowright restart` only if daemon health fails or you intentionally
   want to discard the current daemon and orphan browser state.

**Tuning (v0.9.0)**

These env vars adjust the bridge's timeout and resume behaviour (all have safe
defaults — you rarely need to touch them):

- `OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS` (default 20) — flat in-flight
  deadline for a forwarded request. Long tools override it via per-tool floors:
  `OCTOWRIGHT_BRIDGE_BROWSER_LAUNCH_TIMEOUT_SECONDS` (~105),
  `OCTOWRIGHT_BRIDGE_MACRO_RUN_TIMEOUT_SECONDS` (120),
  `OCTOWRIGHT_BRIDGE_MACRO_SEQUENCE_TIMEOUT_SECONDS` (180). A tool that streams
  MCP progress (e.g. `macro_run`) re-arms its deadline while progress flows.
- `OCTOWRIGHT_BRIDGE_RESUME_MAX_ATTEMPTS` (default 3) — how many times an
  in-flight request is re-sent across reconnects before it's failed.
- `OCTOWRIGHT_IDEMPOTENCY` (default on; `0` to disable) — the leader-side dedup
  that makes resume safe. Disabling it restores the pre-v0.9.0 "fail the call,
  let the agent retry" behaviour. Related cache bounds:
  `OCTOWRIGHT_IDEMPOTENCY_TTL_SECONDS` (180),
  `OCTOWRIGHT_IDEMPOTENCY_MAX_ENTRIES` (256),
  `OCTOWRIGHT_IDEMPOTENCY_MAX_RESULT_BYTES` (1 MiB),
  `OCTOWRIGHT_IDEMPOTENCY_INPROGRESS_WAIT_SECONDS` (95).
