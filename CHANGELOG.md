# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.9] - 2026-08-06

### Added
- **A persona's `default_url` is the browser context's `base_url`.** A macro is
  the behaviour; the persona is the *where* — but browser contexts were the one
  place that did not honour that split, so a macro had to bake an origin into
  every navigate, and proving the same flow against a second deployment meant a
  second copy of the same behaviour that then drifted from the first.
  `default_url` now becomes Playwright's `base_url`, so
  `browser_navigate("/orders")` resolves per persona and the same macro replays
  against a local stack, a staging tier or production by launching it as a
  different persona. Deliberately silent when there is nothing to say: a profile
  name need not be a saved persona and a persona need not declare a
  `default_url` — both pass no `base_url` at all rather than `None`, so absolute
  URLs and every existing macro keep working untouched.
- **Explicit `LaunchOptions.base_url`.** A caller driving octowright as a library
  has no persona to speak for it — a suite replaying macros against a dev stack,
  a batch run pinned to one tier. Explicit wins over the persona's `default_url`,
  because a caller naming an origin is more specific than a default.

### Fixed
- **`browser_navigate` accepts a relative path.** `_reject_unsafe_url` demanded a
  scheme, so `/orders` never reached Playwright to be resolved at all. One
  leading slash is same-origin by construction — no scheme to deny, no new host
  to check — and is now allowed; **two** slashes is protocol-relative
  (`//evil.test/x` is a different host) and still goes through the absolute
  checks. That relaxation is only sound if the inherited origin is itself
  trusted, so `base_url` is now validated through the same guard every
  navigation uses — otherwise an unchecked `base_url` would be a way to reach a
  host the SSRF policy refuses by writing `/` in a macro.
- **Replay no longer reports failures for the passive rows the recorder emits.**
  The strip-list had drifted from the recorder with nothing checking it: sockets
  are recorded as `websocket_{direction}` (Playwright's `framesent` /
  `framereceived`) but the list still named `websocket_inbound` /
  `websocket_outbound`, an older vocabulary with no emitter left. Unclassified
  kinds count as errors in `dispatch_simple`, so one captured macro library
  carried 608 frames and reported **608 bogus failures on every replay**.
  `websocket_error`, `dialog_handled`, `download_saved` and
  `download_save_error` had the same gap — outcomes of something the harness
  did, not instructions to redo it. The dead `inbound`/`outbound` names are
  retained so recordings made under the older vocabulary still replay clean, and
  the two hand-maintained copies of this vocabulary (in `runtime` and
  `recording_import`, which disagreed with the recorder *and* with each other)
  are replaced by a derived `RECORDER_NOISE`. A new test pins the missing
  invariant: every event the recorder emits must be replayable, skipped or
  stripped, so any NEW unclassified event fails.
- **`switch_frame` and `get_text_by` are replayable.** Both were recorded and
  classified nowhere, so `dispatch_simple` counted each as an error rather than
  performing it — a macro that entered an iframe never re-entered it, and one
  that read a value never asserted on it. Each records its observation alongside
  its inputs, so each needed a drop entry: `switch_frame` records the frame it
  *landed* on (index, url, name) and replay must re-resolve that from the live
  page, since an index recorded yesterday means nothing today; `get_text_by`
  records the text it read, and dropping that matters more than usual because
  the method takes `**finders`, so a stray `result` would not raise as an
  unexpected kwarg — it would reach the locator builder as though it were a
  finder. `get_text_by` also needed allowlisting in `strip_non_aria_noise`,
  which treats the semantic locator keys as noise for every kind except
  `click`/`fill`/`click_by`/`fill_by`; those keys *are* this action's finders, so
  replaying one called `session.get_text_by()` with no finder at all. The
  allowlist enumerated three semantic actions and missed the fourth; it is now a
  named set.

### Security
- **`cryptography` floor raised to 50.0.0** (PYSEC-2026-3552). Windows ARM64
  keeps its existing 46.0.3 pin — upstream ships no `win_arm64` wheels past that
  — and the pip-audit gate runs on Linux, so the exception does not weaken it.

## [0.13.8] - 2026-07-20

### Fixed
- **Leader-side protection against a follower reconnect/session storm.** A
  follower that churns StreamableHTTP sessions — each forwarded RPC opening a
  fresh session on the leader instead of reusing one — could pile per-session
  server tasks and transports onto the shared daemon until it was at multiple GB
  RSS and real tool calls were starved (observed live at **18 GB over 2 days**),
  making octowright appear to crash across every connected client. Every prior
  storm defense was *follower*-side, so it only helped once every client
  upgraded; the leader had no protection of its own. Two leader-side guards, on
  by default and deployable with a single daemon restart, independent of
  follower version:
  - **Per-source new-session rate limit** (`http/mcp_flap_guard`): a
    session-creating request (`POST /mcp` with no `Mcp-Session-Id`) beyond
    `OCTOWRIGHT_MCP_NEW_SESSION_MAX` per `OCTOWRIGHT_MCP_NEW_SESSION_WINDOW_SECONDS`
    (defaults 10 / 10 s) is rejected with `429 + Retry-After`, keyed by the
    `X-Octowright-Follower` header a current follower now sends. Old followers
    share one `anonymous` bucket — exactly the storm, collectively throttled.
    Legit clients create ~1 session and reuse it, so they never approach it.
  - **Session-table cap** (housekeeping job 4): when the live session table
    exceeds `OCTOWRIGHT_MCP_MAX_SESSIONS` (default 256), the most-idle sessions
    (abandoned before recently-active) are evicted back to the cap — a memory
    bound no follower can defeat. New metrics
    `octowright_mcp_new_session_throttled_total` and
    `octowright_mcp_session_evicted_total`.

## [0.13.7] - 2026-07-18

### Fixed
- **A daemon restart no longer disconnects every MCP client at once.** Each
  MCP client bridges to one shared leader daemon; when the leader goes
  unresponsive, a follower retries for `BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS`
  and then exits (closing its client's stdio) so `serve.py` can respawn a
  leader. The default was **15s** — shorter than a real leader outage
  (`octowright restart` alone takes 20-30s+; a split-brain/port fight can last
  minutes), so on every restart all followers blew past the window at the same
  instant and octowright broke across every client simultaneously. Raised the
  default to **180s** (`proxy_runtime.py`): followers now wait out a normal
  restart and reconnect to the new leader transparently. A truly-gone leader
  takes up to 180s before a follower respawns one — an acceptable trade against
  a session-killing false-exit on every restart. Still tunable via
  `OCTOWRIGHT_BRIDGE_LEADER_RECOVERY_WINDOW_SECONDS`. Follower-side code: it
  rolls out passively as clients reconnect, not via a daemon restart.

### Added
- **Per-pool recordings root for concurrent-pools embedders.**
  `BrowserPool(recordings_dir=...)` routes one pool's per-launch artefacts (the
  JSONL log, video dir, HAR, and downloads) to a distinct root instead of the
  process-global `RECORDINGS_DIR`, so several `BrowserPool`s in one process no
  longer collide on a single recordings tree. The pool threads its root through
  the new `launch_helpers.build_recording_kwargs` combiner; downloads anchor on
  `session.log_path.parent`. Defaults to the global root (resolved at call time)
  — fully back-compatible for the one-pool daemon. Write-side only by design:
  the dashboard, closed-session discovery and `octowright cleanup` still read
  the process-global root, so a custom root suits an embedder that consumes the
  launch-returned paths directly (`video_dir`, `log_path`). Surfaced as the
  read-only `BrowserPool.recordings_dir` property.

## [0.13.6] - 2026-07-17

### Fixed
- **Split-brain daemons: closed in both directions.** A leader dying could leave
  two daemons alive on different ports (canonical + a bumped one), and
  `octowright restart` couldn't recover from it.
  - *No longer forms.* When a leader died, every follower ran the respawn path;
    the election flock serialized the spawn decision, but `wait_for_daemon` ran
    outside the lock — so the first follower released the flock before its
    spawned daemon bound the port, and a second follower then spawned a
    competitor that port-walked to a bumped port. The election lock is now held
    until the spawned daemon is confirmed up, so the next follower adopts the new
    leader and defers instead of forking a rival. A lock-acquire timeout defers
    quietly rather than spawning or raising.
  - *Restart recovers from an existing split-brain.* `octowright restart` scoped
    its process sweep to the lockfile leader's `--http-port` and then spawned on
    the canonical port; a rival leader holding the canonical port (whose command
    line may carry no `--http-port` at all) was never killed, so the bind failed
    and the daemon stayed down. Restart now reclaims the spawn port by the actual
    listening socket — command-verified as `octowright serve` — so the rival is
    cleared and a fresh daemon binds. Non-octowright port holders and bare MCP
    followers are never targeted.

## [0.13.5] - 2026-07-17

### Fixed
- **The orphan-browser reaper was killing Chromium's crash handlers.**
  `chrome_crashpad_handler` ships inside the browser bundle (under
  `ms-playwright/chromium-*/`), so it matched the reaper's browser-path filter,
  and it is deliberately spawned detached — the kernel reparents it to `ppid 1`,
  the exact signal `_is_orphaned_browser` reads as "the driver died, reap it." So
  every housekeeping cycle SIGKILLed both crash handlers of every live browser on
  the box (observed as a repeating `reaped_orphan_browsers count=2` with fresh
  pids each minute). Killing them freed nothing — a handler owns no window and the
  pool never drives it — and silently disabled Chromium crash reporting for
  perfectly healthy sessions, hiding the renderer crashes the handler exists to
  capture. Crash-reporter helpers (`crashpad_handler`, `crashreporter`) are now
  excluded from the reaper before the orphan rule runs; a genuinely reparented
  browser sitting beside a handler is still reaped.

### Security
- **Dependency refresh.** All locked dependencies bumped to their latest
  compatible versions, clearing three `mcp` advisories (CVE-2026-52870,
  CVE-2026-52869, CVE-2026-59950; `mcp` 1.27.1 → 1.28.1). No API or behavior
  changes in octowright — lockfile-only, verified against the full test suite.

## [0.13.4] - 2026-07-09

### Fixed
- **Recorded `mock_route`/`unmock_route` were dead on replay.** The recorder
  writes the field as `pattern` (matching the macro linter's required-field
  name), but the session methods' parameter is `url_pattern`. The replay
  rename table had no entry for either action, so any macro or recording
  replay touching route mocking raised `TypeError: unexpected keyword
  argument 'pattern'`.

## [0.13.3] - 2026-07-09

Addresses the root cause behind recurring "Octowright disconnected" reports: the
leader process itself leaking memory over multi-day uptime (observed as high as
18.8GB RSS under heavy concurrent-follower load), which stalls the daemon under
memory pressure and looks like a random disconnect.

### Added
- **Dead-follower MCP session reaper.** A new housekeeping job reaps leader-side
  StreamableHTTP sessions by PID liveness, independent of and complementary to
  `OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS` (which stays off by default). A follower
  whose OS process is confirmed dead has its session terminated immediately —
  unlike idle-time reaping, this can never false-positive on a live client
  that's merely quiet (reading output, thinking, a long CI run), so it runs
  unconditionally and needs no opt-in.
- **Bridge-state tmp-file sweep.** A process killed mid atomic-write (crash,
  SIGKILL, an ungraceful restart) can orphan a `bridge-state.json.*.tmp`
  sibling forever; a new housekeeping job age-gates and removes these
  automatically (364 such files, some weeks old, were found and hand-cleaned
  during this investigation).
- `octowright_status()["bridge"]["follower_sessions_reaped"]` surfaces the
  pid-liveness reaper's running total, and `octowright_follower_session_reaped_total`
  is exported as a metric.

## [0.13.2] - 2026-07-05

A release-tooling fix, no package code changes.

### Fixed
- **PyPI trusted-publisher OIDC exchange.** The release workflow was a
  single job publishing straight to PyPI, but the `invalid-publisher`
  exchange failure meant nothing on pypi.org matched its OIDC claims. Split
  into a `build` job producing the wheel/sdist as an artifact, plus two
  conditional publish jobs: `publish-testpypi` (environment `testpypi`,
  test.pypi.org) for pre-release GitHub Releases, and `publish-pypi`
  (environment `pypi`, pypi.org) for full releases — matching the trusted
  publishers now configured on both.

## [0.13.1] - 2026-07-04

A hotfix for 0.12.1's idle-session reaper.

### Fixed
- **`OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS` is now OFF by default**, not
  `300`s. Nothing pings the leader to reset a session's idle deadline
  between real tool calls, so an ordinary interactive pause — reading
  output, deciding what to say, watching a slow build/CI run — looked
  identical to an abandoned session to this timer, and live sessions were
  getting silently killed every few minutes. Mirrors `OCTOWRIGHT_IDLE_GRACE`'s
  existing philosophy: killing a live client session by default is worse
  than a slow leak. Set a positive value to opt in on a shared/CI host that
  wants bounded memory over long-lived idle sessions.

## [0.13.0] - 2026-07-04

Headed browsers now protect themselves by default.

### Added
- **Headed browsers protect-by-default.** A resolved-headed, non-ephemeral
  browser now launches with `protected=True` — an agent's reflex
  `browser_close` right after a screenshot is refused unless `force=True` is
  passed, so a window the user was told to watch can't be destroyed out from
  under them. Headless/CI/agent-internal browsers are completely untouched.
  Reuses the existing `protected` mechanism; no new API surface.
  Precedence: explicit `protected` arg → `OCTOWRIGHT_PROTECT_BROWSERS=1` (all)
  → `OCTOWRIGHT_PROTECT_HEADED` (headed, default **on**) → unprotected. New
  env var `OCTOWRIGHT_PROTECT_HEADED` (`=0` opts out). The refusal message is
  tailored by `protected_reason` to explain why and how to proceed. Resolves
  at the pool's single launch chokepoint, so direct `browser_launch`,
  `browser_spawn_roster`, and scenario launches are all covered, and the
  protection (and its reason) survives `relaunch_fluid`/`browser_handoff`.

### Fixed
- `relaunch_fluid`/`handoff_browser` now force-close the source browser during
  a relaunch/handoff — previously they didn't pass `force=True`, which was
  harmless while `protected` was rare but would have broken relaunch/handoff
  for any ordinary headed browser now that headed browsers protect by
  default. A relaunch is a transparent same-browser replacement, not a
  destructive close, matching the existing "internal rollback/teardown"
  `force=True` exception.

## [0.12.1] - 2026-07-02

A leader-memory hotfix. A reconnect storm could leave the daemon leader holding
gigabytes of RAM with zero live browsers; idle MCP sessions are now reaped.

### Fixed
- **Unbounded leader memory leak from abandoned MCP sessions.** The StreamableHTTP
  session manager defaults `session_idle_timeout=None` — it never reaps sessions,
  so every session's per-session server task + transport lingered in the manager's
  task group even after the client vanished (~54KB/session, unbounded — observed a
  leader at 2.4GB RSS with zero live browsers after ~17h of reconnect churn).
  `http/app.py` now sets the timeout on the manager after `streamable_http_app()`
  builds it; the manager resets the deadline on each request, so an active session
  is never reaped — only a truly idle/abandoned one, whose task then exits and
  frees its memory. Tunable via `OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS` (default 300;
  `0`/`off` restores the leaky default). Reproduced (RSS 97→151MB over 1000
  abandoned sessions, linear) and validated (RSS flat at ~110MB with the reaper on).

## [0.12.0] - 2026-07-02

A follower-bridge stability release. The bridge that connects an MCP client
(Codex, Claude Code, …) to the shared daemon leader no longer surfaces false
"Octowright disconnected" errors on slow tool calls, delivers proactive
notifications in the default daemon deployment, and cannot be driven into a
reconnect storm or a split-brain second daemon.

### Added
- **Tool-call progress heartbeat.** The leader emits periodic MCP progress for
  every in-flight tool call (`server/_heartbeat.py`), reviving the follower's
  deadline re-arm so a slow-but-alive call keeps its bridge deadline alive as
  long as the leader event loop is alive. A genuinely wedged/dead leader still
  times out fast. Tunable via `OCTOWRIGHT_HEARTBEAT_INTERVAL_SECONDS` (default 8)
  and `OCTOWRIGHT_HEARTBEAT_MAX_SECONDS` (ceiling, default 600).
- **Proactive notifications in daemon mode.** The detached-daemon leader now
  streams `session_event_bus` over a new `GET /api/mcp-events` SSE endpoint, and
  the follower re-injects each frame into the local stdio client — so
  `browser_crashed` / `browser_recovered` / `driver_died` / `session_closed`
  reach stdio clients even though the HTTP-MCP transport carries no
  server-initiated notifications. A direct HTTP-MCP client (no follower) still
  gets none (SDK limitation), so `octowright_status()` remains the authoritative
  pull check.
- **`OCTOWRIGHT_BRIDGE_MIN_SESSION_SECONDS`** (default 2.0) — flap threshold for
  the reconnect backoff below.

### Fixed
- **False "disconnected" on slow tool calls.** A tool call that outran the flat
  bridge deadline surfaced as a transport disconnect even while the leader was
  working; the heartbeat above keeps it alive.
- **Reconnect transport storm.** A session the leader ended almost immediately
  reconnected with no backoff, busy-looping the leader into a
  `Created new transport` / `Terminating session` storm (observed ~300+/sec
  across followers). Both reconnect paths — a clean instant end and a
  connect-then-abort (`ClientDisconnect` / reset, whose `attempt` counter reset
  on each connect) — now throttle by an increasing flap backoff.
- **Split-brain second daemon.** A follower's respawn (and now the initial
  election) could bind a bumped port (6286 → 6287) and become a second leader
  beside a healthy one when the lockfile probe false-negatived during a storm.
  Both paths now probe the canonical port directly and adopt the existing leader
  instead of forking a competitor.

## [0.11.0] - 2026-06-29

A token-efficiency release for agentic browsing: Octowright keeps the broad
browser/macro/scenario surface, but adds cheaper discovery paths, bounded
summaries, and profile-aware follow-up guidance so agents can orient before
spending tokens on full snapshots or raw dumps.

### Added
- **HTTP-first web discovery tools.** New `web_page_outline`,
  `web_find_links`, and `web_site_links` tools fetch and parse public HTTP(S)
  pages without launching a browser, returning compact headings/link candidates
  and profile-aware next actions.
- **Compact browser discovery tools.** New `browser_links`,
  `browser_find_link`, `browser_fields`, `browser_find_field`,
  `browser_page_outline`, and `browser_observe` expose bounded DOM, form,
  navigation, console, network, and download summaries for live browser
  sessions.
- **Capture summaries and line/search follow-ups.** Stored captures now support
  summary-oriented payloads for markdown, snapshots, evaluate results, network
  logs, console logs, and recordings, with follow-up actions for targeted
  retrieval.
- **Profile-aware next actions.** Truncated or summarized responses now annotate
  follow-up tool suggestions that are hidden by the active
  `OCTOWRIGHT_PROFILE`, so agents know whether to call another compact tool or
  restart with a wider profile.
- **Dashboard live-preview fallback.** The frontend live preview falls back to
  screenshot polling when screencast streaming closes, keeping the debug view
  useful on engines or sessions where streaming is unavailable.

### Changed
- **Capability profiles were rebalanced for low-token browsing.** `core` now
  includes compact browser and HTTP discovery tools; `advanced` includes
  summaries for console, network, downloads, captures, and fan-out workflows.
  A core install now exposes 125 MCP tools by default (132 with the optional
  terminal extra), while `--profile=core` trims the visible surface to 31 tools.
- **Heavy read tools are more conservative by default.** Evaluate, text reads,
  snapshots, recording tails, console/network/download reads, and multi-browser
  fan-out now prefer bounded results with explicit follow-up actions for full
  payloads or targeted retrieval.
- **Large server modules were split by responsibility** so discovery, assertions,
  recording tailing, console summaries, capture summaries, and link scoring stay
  under the repo's LOC and complexity gates.

### Fixed
- **Profile-filtered test isolation.** Tests that assert exact follow-up action
  payloads now clear leaked `OCTOWRIGHT_PROFILE` state, preventing order-
  dependent failures in full-suite runs.
- **XML sitemap parsing uses `defusedxml`** in HTTP-first discovery.

## [0.10.1] - 2026-06-27

A bridge-resilience patch plus a terminal-connector tidy.

### Fixed
- **The follower bridge survives an MCP-client compaction freeze.** When a client
  (Codex/Claude compaction) SIGSTOPs the follower process, `time.monotonic()`
  keeps advancing while every task is frozen — so on resume the deadline watchdog
  saw in-flight requests already past due and failed them, and the reconnect that
  followed returned `400` because the bridge replayed the cached `initialize` but
  not the `notifications/initialized` after it (leaving the fresh leader session
  half-initialized). The watchdog now detects the suspension (a wall-clock gap far
  exceeding its sleep interval) and shifts in-flight deadlines forward by the
  frozen span instead of failing them, and reconnect replays the **full**
  handshake. New `OCTOWRIGHT_BRIDGE_SUSPEND_THRESHOLD_SECONDS` knob (default 5s)
  and `octowright_bridge_suspension_total` counter.

### Changed
- **Terminal connector vocabulary: canonical `ssh, telnet, pty` ordering** in
  `terminal/connector_config.py` (network connectors before local), matching the
  transport-vocabulary convention. The external contract is unchanged — the
  `terminal_launch` MCP `kind` arg and dispatch are preserved.

## [0.10.0] - 2026-06-26

A feature + hardening release: optional terminal sessions, self-healing
browsers, a default-on security baseline (all configurable), and real Windows
support.

### Added
- **Terminal sessions (optional `octowright[terminal]` extra).** Drive an
  in-process `provide-uterm` connector — local PTY, SSH, or telnet (CP437 + RFC
  854 for BBS art) — recorded to the same JSONL format as browsers. New
  `terminal_*` MCP tools, a `terminals` capability profile, scenario terminal
  participants, and a read-only **xterm.js** session view in the dashboard
  (lazy-loaded). Core never imports uterm; without the extra the tools simply
  don't register.
- **Self-healing + observability for the browser pool.** Renderer crashes
  auto-recover, a dead shared Playwright driver rebuilds itself, and lost
  sessions are captured (`OCTOWRIGHT_DRIVER_RELAUNCH`). Incidents + a computed
  health verdict surface in `octowright_status()`, and the server proactively
  pushes `browser_crashed` / `browser_recovered` / `driver_died` /
  `session_closed` MCP notifications.
- **Resource governors.** A browser cap (default 32) and an opt-in available-
  memory floor (`OCTOWRIGHT_MIN_FREE_MEMORY_MB`), enforced in the pool layer so
  `scenario_start` can't bypass them.
- **Security knobs** (secure defaults, opt-out where noted): `/mcp` capability
  token (`OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN`, on), owner-only recordings
  (`OCTOWRIGHT_RECORDINGS_PRIVATE`, on), SSRF policy (`OCTOWRIGHT_SSRF_POLICY` /
  `OCTOWRIGHT_SSRF_ALLOW`, off), and a per-recording JSONL size ceiling
  (`OCTOWRIGHT_RECORDING_MAX_BYTES`, off).
- **Stability telemetry.** RSS histogram, driver-restart / launch-refused /
  driver-lost / leader-recovery counters, and trace-context propagation across
  the follower→leader bridge.
- **Windows support.** A real Win32 `GetProcessMemoryInfo` RSS reader so the
  memory governor and RSS telemetry work on Windows (no psutil dependency).

### Changed
- **The follower bridge survives a leader restart / hard kill** instead of
  dropping the client: supervised reconnect with idempotent in-flight resume,
  and bridge health in `octowright_status()["bridge"]`.
- **Recordings are written `0600` (parent `0700`) by default**, and credential
  redaction now also covers the selector-less sinks (`press_key` / `evaluate` /
  `select_option`) under `OCTOWRIGHT_REDACT_INPUTS=all`.
- **The idle watchdog stays off by default** and `--keep-alive` propagates to
  the detached daemon (carried over and reaffirmed from 0.9.1).

### Fixed
- **A crashed renderer is recovered by replacing the page**, not a `reload()`
  that silently failed on a dead renderer.
- **Disk-write containment**: browser downloads reduce the remote
  Content-Disposition filename to a safe basename; golden + capture files are
  written atomically (temp-sibling + rename) to defeat a symlink TOCTOU.
- **The navigate telemetry span strips `user:pass@` userinfo and the query
  string** so credentials/tokens don't reach traces.
- **`octowright restart` verifies the lockfile pid is an `octowright serve`
  process** before signalling it, so a stale/recycled pid isn't friendly-fired;
  the daemon sweep is scoped to the managed port.
- **Cross-platform fixes** for Windows (path separators, process discovery, and
  POSIX-only test assumptions).

### Security
- Closes the unauthenticated loopback `/mcp` RCE for processes that can't read
  the `0600` lockfile (cross-user / sandbox), adds opt-in SSRF blocking
  (literal private / loopback / cloud-metadata hosts, incl. macro replay), and
  keeps recorded credentials owner-only — see the "Disk-write containment",
  "Recording-file privacy", and "Bridge capability token" sections in AGENTS.md.
- **Dependency CVE remediation**: cryptography → 49.0.0 (win-arm64 stays on the
  newest installable 46.0.3), starlette → 1.3.1, python-multipart → 0.0.32,
  msgpack → 1.2.1, pydantic-settings → 2.14.2.

## [0.9.1] - 2026-06-12

Reliability fixes for the follower/daemon bridge: the daemon no longer
disconnects clients mid-session, and follower processes no longer leak.

### Changed
- **Idle watchdog disabled by default.** The daemon holds live browser state, and
  its idle auto-exit closed the follower's stdio mid-session — breaking the MCP
  connection and dropping open browsers with no transparent wake (the user had to
  reconnect by hand). The daemon now stays up until an explicit `octowright
  restart`. Opt back into auto-exit for CI / shared / resource-constrained hosts
  with `OCTOWRIGHT_IDLE_GRACE=<seconds>` or `--idle-grace`; `off` / `never` /
  `none` / `disabled` / `0` / a non-positive value also disable it.

### Fixed
- **`--keep-alive` now reaches the daemon.** `spawn_daemon` forwards `--keep-alive`
  to the detached daemon; previously the flag was silently dropped, so it never
  reached the process that actually owns the watchdog.
- **Detached daemon stays alive after stdio EOF.** The watchdog task was also what
  held a discoverable daemon up past its `/dev/null` stdin EOF; with auto-quit off
  by default the daemon exited the instant it spawned (`daemon_spawn_failed`,
  falling back to fragile inline mode). The post-EOF "keep serving" phase now keys
  on being discoverable and waits on the HTTP sidecar, watchdog or not.
- **Followers no longer outlive their client.** When the MCP client closes stdin,
  the follower arms a daemon-thread hard-exit backstop, so a wedged remote teardown
  can't leave an orphaned `octowright serve` process reconnecting forever. Grace is
  `OCTOWRIGHT_FOLLOWER_EXIT_BACKSTOP_SECONDS` (default 5s).

## [0.9.0] - 2026-06-11

### Added
- **Browser crash detection** — Playwright's `page.on('crash')` (renderer
  "Aw, Snap" / `Target.crashed`) is now wired. The session is marked crashed, a
  proactive `notifications/octowright/browser_crashed` notification fires
  (carrying `instance_id`, `kind`, `scope`, `log_path`, and an actionable
  `hint`), eviction reports `reason="crashed"`, and a later tool call on a
  crashed instance says "crashed (its process died) — relaunch" instead of an
  opaque failure. A pure hard-kill that fires no crash event still reads as a
  normal external close (Playwright Python can't read the child exit signal).
- **Idempotent bridge resume** — the follower injects a stable
  `octowrightIdempotencyKey` into each `tools/call` and re-sends it verbatim
  after a reconnect; a new leader-side dedup cache (TTL + size bounded,
  success-only, with dead-session takeover and a bounded await) makes the
  re-sent call a no-op instead of double-executing a side-effectful tool. The
  bridge now auto-resumes in-flight requests (bounded by
  `OCTOWRIGHT_BRIDGE_RESUME_MAX_ATTEMPTS`, default 3) rather than failing them.
  Disable with `OCTOWRIGHT_IDEMPOTENCY=0` to restore the prior fail-safe.
- **Per-tool bridge timeouts + macro progress** — long tools no longer hit the
  flat 20s in-flight deadline. A per-tool floor (`BRIDGE_TOOL_TIMEOUTS`:
  browser_launch ~105s, macro_run 120s, macro_run_sequence 180s; env-overridable)
  replaces the flat default, and `macro_run` / `macro_run_sequence` stream MCP
  progress per step, which re-arms the deadline while progress flows. A mid-macro
  failure now reports `executed` + `executed_actions` so a half-applied replay
  shows exactly what landed.
- **`browser_snapshot` heavy-DOM degrade** — a snapshot that would exceed
  `OCTOWRIGHT_SNAPSHOT_TIMEOUT_SECONDS` (12s, kept below the bridge timeout)
  returns a typed `{snapshot_timed_out, hint}` pointing at
  `browser_read_markdown` / a scoped selector instead of hanging until the
  transport gives up.

### Changed
- **Telemetry now flows through `provide.telemetry` ≥ 0.4.8** — Octowright
  dropped its local `span()` / metrics helpers for the library's governed
  equivalents (consent / sampling / backpressure), which also fixes the per-call
  OpenTelemetry cross-context detach error that previously spammed the daemon
  log on every tool call. HTTP request metrics moved to the library's
  `TelemetryMiddleware` (RED metrics + request-id / W3C trace propagation +
  cardinality-safe routes), exported over OTLP. `OCTOWRIGHT_HTTP_METRICS` now
  gates metric recording only; context propagation stays on.
- **Saved macros no longer bake in recorder noise** — passive recorder events
  (`user_navigation`, console, websocket, markdown-cache, etc.) are stripped at
  `macro_save`, so replays carry only the intentional steps.

### Removed
- **Bespoke `/api/metrics` Prometheus scrape endpoint** — HTTP metrics now flow
  through `provide.telemetry` → OTLP, uniform with the rest of Octowright's
  telemetry. Point an OTLP collector at the process instead of scraping.
  **Breaking** for anyone scraping the old endpoint.

### Fixed
- **Bridge-state / followers registry bounded** — `bridge-state.json` no longer
  accumulates stale per-PID follower snapshots (dead PIDs are pruned), and
  `octowright_status` caps the exposed followers/events so the payload can't blow
  past the MCP client's tool-result limit (a 242 KB status once made the call
  unusable).
- **Macro progress-pill locator collision** — the in-page status pill now renders
  in a closed shadow root, so its echoed action text (e.g. "… | click_by
  text=Place order") no longer doubles a `get_by_text(...)` match and breaks the
  macro's own replay.
- **"Died vs never existed" messaging** — `pool.get` on an instance that was
  evicted (crashed or closed externally) now reports that it ended unexpectedly
  and to relaunch, distinct from a never-launched id.

## [0.8.0] - 2026-06-09

### Added
- **`macro_repair_apply`** — applies a stored-heuristic repair to one macro
  action: rewrites a brittle selector-based `click` / `fill` into its semantic
  `click_by` / `fill_by` form (from the `role` / `label` / `text` / `test_id`
  captured at record time), drops the stale CSS selector, and saves the macro in
  place. Completes the repair loop (`macro_repair_preview` → `macro_repair_apply`
  → `macro_run`); raises before any write on an out-of-range index or an action
  with no stored semantic locator.
- **Post-upgrade "what's new" notice** — the first run after a version change
  records curated highlights, surfaced in `octowright_status()` (`upgrade` block)
  and echoed once as a startup banner (a human terminal in inline mode, the
  daemon log otherwise). New `octowright.upgrade` module; `OCTOWRIGHT_UPGRADE_STATE`
  overrides the last-seen-version marker path.
- **Dead-server reconnect guidance** — when the octowright MCP transport is
  disconnected (tools missing, or `Transport closed` that doesn't recover after
  one retry), the agent is steered to reconnect octowright in its client — asking
  which client and using its native reconnect rather than guessing — instead of
  substituting a shell-opened browser it can't drive. Carried in the agent skill,
  the MCP server instructions, and the follower-bridge error text.

### Changed
- **`browser_snapshot` and `browser_brief` respect the active frame** — after
  `browser_switch_frame` they descend into the switched iframe (matching the
  action tools) instead of always reading the top-level page.

### Fixed
- **Frame-blind read tools** — `capture_create` (snapshot/text content + url),
  `browser_capture_and_close` (url + aria), `browser_read_markdown` (url), and
  `golden_save` (url) now follow the active frame instead of mixing frame
  content with top-page metadata.
- **Stale documentation corrected** — `8765` → `6286` dashboard-port references in
  the getting-started and troubleshooting docs (the port moved in 0.7.0), and the
  MCP tool-surface counts across the docs and architecture diagrams now reflect the
  real 111-tool surface.

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

[0.11.0]: https://github.com/livingstaccato/octowright/compare/v0.10.1...main
[0.10.1]: https://github.com/livingstaccato/octowright/compare/v0.10.0...v0.10.1
[0.9.1]: https://github.com/livingstaccato/octowright/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/livingstaccato/octowright/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/livingstaccato/octowright/compare/v0.7.0...v0.8.0
[0.5.0]: https://github.com/livingstaccato/octowright/compare/v0.3.0...v0.5.0
[0.3.0]: https://github.com/livingstaccato/octowright/releases/tag/v0.3.0
