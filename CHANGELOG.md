# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-06-06

### Added
- **`/new-tab` landing page** — the default destination for `browser_launch`
  with no `url`. A self-contained page served by the local HTTP server: Otto
  logo, lowercase `octowright` wordmark, a live status strip (version · short
  commit · uptime · live browser count · dashboard link), and a background tint
  that shifts with the time of day. Replaces the old `octowright.com` default,
  which caused cert/network failures offline.
- **Cmd+T new-tab override on all three engines** — pressing Cmd+T (or Ctrl+T)
  now lands on `/new-tab`. Chromium uses a generated unpacked background
  service-worker extension (no settings-override prompt); Firefox uses a context
  page-event redirector; WebKit uses an injected keydown handler (opens a window,
  since its shell has no tab bar).
- **Context-aware default browser label** — a no-label/no-profile launch derives
  a human-readable label and persistent profile from (in priority)
  `OCTOWRIGHT_DEFAULT_LABEL`, `.octowright/config.yaml`, the git repo name, or
  the username — instead of a random instance ID. A persona matching the project
  slug is auto-adopted.
- **`.octowright/config.yaml` project config** — `label` / `persona` / `profile`
  keys, read from the nearest parent directory. Scaffolded by `octowright init`.
- **Configurable, richer badge** — `OCTOWRIGHT_BADGE_OPACITY` (default 0.35, more
  translucent); all 8 badge positions including the four center anchors; an
  Alt-click info popup showing session id/label/url with dashboard and recording
  links.
- **`browser_each`** — a single fan-out tool (navigate / resize / evaluate /
  wait_for / screenshot across N browsers) replacing the five `browser_*_each`
  tools.
- **`nav_warning`** — a failed initial navigation leaves the browser alive and
  reports the error in the launch result instead of destroying the instance.
- New env vars: `OCTOWRIGHT_DEFAULT_URL`, `OCTOWRIGHT_DEFAULT_LABEL`,
  `OCTOWRIGHT_BADGE_OPACITY`.

### Changed
- **Default HTTP port 8765 → 6286** ("OCTO"). Override with
  `OCTOWRIGHT_HTTP_PORT`.
- **`browser_click` / `browser_fill` take ARIA locator params directly**
  (`role`, `label`, `text`, `test_id`) — folded in from the removed
  `browser_click_by` / `browser_fill_by`.
- No-URL launches resolve the actual bound HTTP port at launch time, so they
  work even when the port auto-bumped.

### Fixed
- **`octowright restart` binds immediately** instead of waiting out the old
  socket's TIME_WAIT — the daemon and the restart pre-flight both set
  `SO_REUSEADDR` / `SO_REUSEPORT` (restart dropped from ~12s to ~2s).
- **A failed initial navigation no longer tears down the browser** (see
  `nav_warning`).
- **Programmatic `window.open` popups are no longer hijacked** by the new-tab
  redirect — only opener-less (user-opened) tabs are redirected.
- New-tab status-strip browser-count off-by-one.

### Removed
- **`browser_click_by`, `browser_fill_by`** — merged into `browser_click` /
  `browser_fill`.
- **`browser_navigate_each`, `browser_resize_each`, `browser_evaluate_each`,
  `browser_wait_for_each`, `browser_screenshot_each`** — merged into
  `browser_each`.

[0.7.0]: https://github.com/livingstaccato/octowright/compare/v0.6.4...v0.7.0

## [0.6.4] - 2026-06-04

### Added
- **`browser_set_protected`** MCP tool — set or clear the protected flag on a
  live browser session after launch, without closing and relaunching.
- **`pool.protected_browsers`** count in `octowright_status()["pool"]` — LLMs
  can now see how many sessions are off-limits at a glance.
- **`browser_spawn_roster`** description updated to mention `protected` field
  in each spec dict; also added to the `core` capability profile.

[0.6.4]: https://github.com/livingstaccato/octowright/compare/v0.6.3...v0.6.4

## [0.6.3] - 2026-06-04

### Added
- **Protected browser flag** — `browser_launch(protected=True)` marks a session
  as user-owned. `browser_close` and `browser_close_all` refuse to close
  protected browsers unless `force=True` is passed, preventing other agents
  from accidentally closing a window the user is actively watching.
  `OCTOWRIGHT_PROTECT_BROWSERS=1` makes every browser protected by default.
- `protected` field surfaced in `browser_list`, `GET /api/sessions` live array,
  and POST `/api/sessions` launch response — LLMs and the dashboard can now
  see which sessions are off-limits.
- Dashboard session table shows a 🔒 badge and a subtle tint on protected rows.
- Skill docs updated: `SKILL.md` and `reference/launch-and-personas.md` now
  direct agents to pass `protected=True` on every user-facing launch.

[0.6.3]: https://github.com/livingstaccato/octowright/compare/v0.6.2...v0.6.3

## [0.6.2] - 2026-05-29

### Fixed
- **Follower bridge recovery when the singleton leader is killed or disappears**
  now unwinds the local stdio follower so `octowright serve` can spawn a fresh
  daemon instead of leaving the client stuck behind a dead HTTP-MCP stream.
- **Bridge request timeouts recycle the remote HTTP-MCP session** so later MCP
  calls reconnect cleanly to the current lockfile leader.
- **`octowright skill install octowright` defaults to the Claude skill +
  Claude plugin target** instead of installing the MCP server target by
  mistake.

[0.6.2]: https://github.com/livingstaccato/octowright/compare/v0.6.1...v0.6.2

## [0.6.1] - 2026-05-27

### Fixed
- **`octowright` with no subcommand now shows help** instead of silently
  starting the daemon. Users on a new machine who run `octowright` without
  arguments see the command listing and usage, not a running MCP server.
- **Python 3.13 `RuntimeError: Attempted to exit cancel scope in a different task`**
  — the follower bridge's `proxy_supervisor` was manually calling
  `streamablehttp_client.__aenter__()` / `.__aexit__()`. Python 3.13 changed
  async generator finalization to run cleanup in a separate asyncio task;
  anyio cancel scopes cannot span task boundaries, producing a noisy traceback
  on every follower teardown. Fixed by switching to
  `async with streamablehttp_client(...)` with a `CancelScope` + `deadline = math.inf`
  trick that preserves the connect-only timeout without constraining the read
  loop.

[0.6.1]: https://github.com/livingstaccato/octowright/compare/v0.6.0...v0.6.1

## [0.6.0] - 2026-05-27

### Added

#### Macro artifact workbench — phases 2 and 3
- **Verification layer** (`macro_artifact_verify`): evaluates critical-point
  checks against a completed artifact run; check types include `result_status`,
  `evidence_exists`, `screenshot_exists`, `assertion_passed`, and `log_contains`.
- `macro_artifact_critical_points_get` / `macro_artifact_critical_points_set`
  MCP tools for reading and overwriting a run's critical-point definitions.
- `macro_artifact_status` MCP tool — summary view of a run including verification
  state, evidence count, and per-check results.
- `verification.json` written into the run bundle alongside `summary.md` when
  verification is run; `## Verification and Critical Points` rendered in
  `summary.md`.
- Telemetry: `octowright.macro.artifact.run` span + `octowright_macro_artifact_run_total`
  counter; `octowright.macro.artifact.verify` span + counter.

#### Export engine (phase 2)
- Expanded JSONL-to-script export fidelity: action parity for all recorded
  browser actions, deduplication, and idiomatic output formatting.
- Full export test suite (`tests/test_export.py`).

#### Agent skill — restructured
- Renamed skill from `using-octowright` → `octowright` (`/octowright:octowright`
  slash command).
- Root `SKILL.md` trimmed to overview + rule summaries + quick reference; deep
  content moved to dedicated reference files.
- New reference files: `launch-and-personas.md`, `macros-and-advisor.md`,
  `debugging.md`, `transport-recovery.md`.
- New command files: `commands/record.md`, `commands/replay.md`,
  `commands/scenario.md`.

### Fixed
- CI smoke script updated to use new `octowright` skill name (was `using-octowright`).

[0.6.0]: https://github.com/livingstaccato/octowright/compare/v0.5.0...v0.6.0

## [0.5.0] - 2026-05-26

465 commits since `v0.3.0`. Highlights below; see `git log v0.3.0..v0.5.0` for
the full record.

### Added

#### Macro artifacts subsystem
- Safe artifact path store anchored under `RECORDINGS_DIR` (path containment,
  symlink resolution before prefix check).
- Artifact planning (`macro_artifact_plan`) and bounded digest tools.
- Artifact run execution with evidence and run report generation.
- Export macros as import-safe standalone CLI scripts.
- Shared report redaction: bearer tokens, cookies (all variants), private key
  fields, common auth key variants, colon-style log previews, summary URLs
  (IPv6-safe; rejects protocol-relative and userinfo-like URLs).

#### OpenTelemetry integration
- Tracing and metrics via `provide.telemetry` — noop by default; enabled with
  `PROVIDE_TRACE_ENABLED=true` / `PROVIDE_METRICS_ENABLED=true`.
- W3C `traceparent` propagation from follower→leader: `httpx` request hook
  injects the header; leader-side ASGI middleware extracts it so tool-handler
  spans chain under the follower's `bridge.forward_rpc` span.
- `octowright.mcp.request` span in the leader-side extraction middleware (ends
  on `http.response.start` to avoid buffering long-lived SSE streams).
- WS-cache batched flush — single flush per drain instead of flush-per-frame.

#### Antigravity (agy) plugin integration
- First-class `agy` plugin support: ships `mcp_config` in the skill pack,
  tracks all plugin manifests, version-guards the plugin list.
- `octowright skill` installs and inspects the skill for both Claude Code and
  Antigravity clients.

#### MCP push notifications
- `notifications/octowright/session_closed` pushed on every session eviction
  (external close and `pool.close`).

#### MCP follower bridge reliability
- Supervised bridge that keeps the local stdio follower alive while the remote
  HTTP-MCP leader session is disposable: in-flight request deadlines, explicit
  JSON-RPC bridge errors on stream loss, automatic reconnect to the current
  lockfile leader URL on the next call.
- Bridge diagnostics surfaced through `octowright_status().bridge` and persisted
  via `OCTOWRIGHT_BRIDGE_STATE`.
- `scripts/bridge_reconnect_smoke.py` to distinguish a broken client handle
  from a broken daemon when transport errors recur.

#### Scenarios + personas subsystem
- `Scenario` / `Participant` dataclasses with YAML and Python loaders.
- `ScenarioPool` with `start` / `stop` / `run_macro` and fixture application.
- Scenario templates, `scenario_plan` dry-run resolver, `scenario_wait_for_sync`
  orchestration, cursor-based tail for live event streaming.
- `Persona` dataclass with YAML loader and credential resolver
  (`*_env` / `*_cmd` references), `persona_credentials_check` pre-flight tool.
- Idempotent migration from the legacy persona layout.
- `octowright scenario` and `octowright persona` CLI subcommand groups.

#### Browser pool capabilities
- Tools: `browser_brief`, `browser_quick_launch`, `browser_suggest_for_url`,
  `browser_read_markdown`, `browser_open_url`, `browser_tail_recording`,
  `browser_*_each` fan-out variants, hover, select_option, drag,
  navigate_back, resize, network_requests, network mocking, dialog policies,
  file uploads, iframe switching, role-based selectors.
- `wait_for_function` JS-expression predicates for `browser_wait_for`.
- `response_mode='brief'` on `browser_click` and `browser_navigate` to keep
  LLM responses compact.
- `session=True` tmpdir profile mode and `profile_cleanup` MCP tool.
- Visual differentiation: persona-stable corner badge with 4-corner positioning,
  per-persona/engine emoji prefix, chromium window tiling, macro status pill
  (with slowmo and run-history modal), viewport status pill.
- Capture cache with semantic capture surfaces (`capture_create`,
  `capture_search`, `capture_list`, `capture_get`, `capture_cleanup`).
- Golden snapshot tooling, `golden_verify_loop`, video recording + frame
  extraction, composite-label pills, Playwright tracing.

#### HTTP dashboard + frontend SPA
- TypeScript debugger SPA in `packages/octowright-frontend/` (npm workspace,
  tsc + biome + vitest), bundled into the wheel and sdist.
- Session-detail page with embedded video, action timeline, console messages,
  downloads, screenshots panels.
- Write endpoints: launch / close / navigate sessions, scenario start / stop /
  run_macro, recording delete, persona YAML editor, disk-size summaries.
- Live screenshot polling and externally-closed browser eviction.
- Dashboard event stream over SSE; WebSocket `/tail` with bounded heartbeat.

#### Capability profiles + Advisor
- `OCTOWRIGHT_PROFILE` env var (and `octowright serve --profile=...`) filters
  the LLM-visible MCP tool surface at `@mcp.tool` decoration time.
- Named profiles: `core`, `advanced`, `macros`, `scenarios`, `personas`.
- Always-on meta + Advisor tools regardless of filter
  (`octowright_status`, `octowright_storage_report`, `octowright_dashboard_url`,
  `octowright_check_takeover`, three `octowright_advisor_*` tools).
- Octowright Advisor — local deterministic guidance: bounded tool-usage
  summaries, repeated-workflow observations, `macro_candidate` suggestions,
  preference persistence.

#### Macros
- `macro_lint` static analysis, `macro_repair_preview`, aria-first replay,
  semantic replay with intent-based recovery prompts.
- Conditional action types: `if_selector`, `try`, `try_each`.
- Parameterized macros with assertions, chaining, and auto-capture on failure.
- Action summarization for compact macro descriptions.

#### CLI + distribution
- `octowright init` first-run scaffolding wizard.
- `octowright restart` to recover wedged daemons cleanly (Windows-compatible).
- `octowright cleanup` to prune stale recordings / screenshots / videos / traces.
- `octowright takeover` to detect and disable competing Playwright MCP plugins.
- `octowright test` JSONL-driven test suite runner.
- `octowright skill` to install / inspect the octowright agent skill,
  with skill-pack distribution baked into the wheel.
- `octowright selftest` to list MCP tools without a client.
- `--log-level` flag and watchdog / shutdown visibility on `octowright serve`.

#### Singleton leader/follower
- Lockfile primitive for leader/follower election.
- Daemonized leader that survives parent SIGKILL.
- HTTP staleness probe with follower auto-promote.
- Idle watchdog: auto-quit when the pool sits empty after use
  (`OCTOWRIGHT_IDLE_GRACE`, default 300s; `--keep-alive` to disable).

### Changed

- Scenario YAML loader validates participant shape; unknown roles warn instead
  of raise to support custom role vocabularies without blocking.
- Scenario role vocabulary extended with domain-specific roles:
  `main-site`, `recorder`, `replayer`, `form`, `counter`, `arithmetic`.
- Coverage gate enforced in CI at 83% floor.
- License switched to Apache-2.0 with SPDX headers across all source files.
- All env-var-driven defaults consolidated into `src/octowright/defaults.py`.
- HTTP API contract documented in `docs/architecture/MCP-SHARED-CONTRACT.md`.
- Recording cleanup is silent-swallow only in shutdown / per-line malformed
  input paths; user-action paths log warnings instead.
- `browser_snapshot` default selector changed to `body`.
- Demo bundle layout reorganised: `demo/bundles/` is source of truth (tracked),
  `demo/tutorial-export/` is derived and gitignored (`make export-demos`).
- HAR rotation helper promoted to public `next_har_path`; returns the original
  path when free instead of always producing a `.N` sibling, and the rotation
  loop is bounded.

### Fixed

- Bridge connect-timeout scope, double-response race, replay-id collision.
- WebSocket `/tail` disconnect race + `shutdown_pool` lock.
- `warm_close` re-threading; `browser_screenshot` atomic write.
- Secrets redaction in JSONL export: locator fills and sequence failure args.
- Macro lint schema aligned with IO recording semantics.
- Cross-origin guard now blocks GET requests to side-effect routes
  (`/api/sessions/{id}/screenshot/now`, `/api/sessions/{id}/markdown`) via a
  per-route `side_effect_get` marker; cross-origin policy applied to mounted
  ASGI apps (FastMCP transport).
- Dashboard origin controls hardened; persona endpoints validate slugs and
  verify containment.
- `browser process` cleanup ensured for persistent contexts on session close.
- Recorded `open_url` actions replay without their captured `page_index`
  (which is replay-specific state).
- Composite-label pills use real RGBA alpha instead of magenta chroma-key.
- WebSocket `/tail` heartbeat cadence + clamping of negative `?since` cursors.
- UTF-8 file IO across recorder / macro storage / persona YAML.
- CI: build the frontend SPA before the wheel build in the test matrix; ship
  the SPA in both wheel and sdist (`check_wheel_assets` validates both).
- Tests no longer hardcode `/Users/tim` paths; POSIX-only daemonize test
  skipped on Windows.
- Sdist member normalisation in `check_wheel_assets` no longer silently masks
  malformed entries.

### Security

- **`OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS`** — shell `*_cmd` credential resolution
  is now default-deny; set to `1` to opt in. Previously allowed by default.
- **`OCTOWRIGHT_ALLOW_PY_SCENARIOS`** — `.py` scenario loading is now
  default-deny; set to `1` to opt in. Previously allowed by default.
- **`OCTOWRIGHT_REDACT_INPUTS`** — record-time scrubbing of typed/filled values
  in JSONL: `off` records literals, `passwords` (default) redacts
  `<input type="password">` and SPA custom-password inputs, `all` redacts every
  typed/filled value.
- Lockfile `chmod 0o600` + parent directory `chmod 0o700`.
- Screenshot path-traversal TOCTOU window closed.
- SPA mount guarded by bind-host check.
- Atomic writes for macro JSON and persona YAML (crash-safe, no partial-write
  window).
- Path containment for replay artifacts and reports: all paths resolved and
  checked against `RECORDINGS_DIR` before write; symlinks resolved before the
  prefix check.
- `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD` env var to allow non-loopback access to
  sensitive dashboard / MCP endpoints; loopback bind by default. Documented
  the RCE-equivalent surface implication when combined with a non-loopback
  bind host without an auth gateway.
- `guard_sensitive_http` ASGI wrapper for mounted apps (FastMCP transport)
  that captures the bind host at wrap time so policy is correct under
  Starlette `Mount` nesting.
- Trust-boundary warnings added to the agent-facing docs.

## [0.3.0] - 2026-05-07

Initial PyPI / TestPyPI publication. See `git log v0.3.0` for the commit
history that led to the first published release.

[0.5.0]: https://github.com/livingstaccato/octowright/compare/v0.3.0...v0.5.0
[0.3.0]: https://github.com/livingstaccato/octowright/releases/tag/v0.3.0
