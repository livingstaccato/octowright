# HANDOFF — External Security Review Remediation

**Branch:** `fix/review-batch-a-correctness`
**Source:** external LLM security review of octowright @ `e788f43` (main).
**Status date:** 2026-08-11

Every finding below was **code-verified** by reading the actual source (not taken
on the reviewer's word). Verdicts: TRUE = defect confirmed; PARTIAL = real but
narrower/nuanced than stated; FALSE = reviewer wrong; POLICY = deliberate
documented choice, needs an owner decision not a bugfix.

---

## Verification summary (all 22)

### High
| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 1 | Dashboard `/api`/SSE/WS/media have no capability-token auth (loopback-Host only). Token guards `/mcp` alone. | **TRUE** | `http/app.py:195` guard on `/mcp` only; `http/exposure.py:103-108,191-210` Host/Origin only; no `X-Octowright-Token` check in any `/api` route |
| 2 | SSRF policy default OFF | **POLICY** | Documented deliberate back-compat in CLAUDE.md; DNS-rebind gap also documented. Flipping default = behavior change. |
| 3 | Session registered before URL-validate/nav; cleanup skipped on post-register failure/cancel → leaked browser | **TRUE** | `browser_pool/launch_pipeline.py:360` register, `:386` `_reject_unsafe_url`, `:388` goto, `:131 if not registered` skips cleanup |
| 4 | Last-page close evicts from registry but skips full `_close_impl` → orphan context/process/lock | **TRUE** | `browser_pool/listeners.py:99,139-140`; `pool.py:413` dict pop; full close `session/core_ops_mixin.py:429-498` |
| 5 | Persona delete races pre-register profile mkdir/open; no shared lock | **TRUE** | `server/personas.py:32` check-then-rmtree; `browser_pool/launch_helpers.py:253,263` mkdir/open before register (`launch_pipeline.py:360`); `engine_profiles.py:47-52` |
| 6 | Crash recovery double-wires page (explicit `_wire_listeners` + context `page` event); no dedup | **TRUE** | `browser_pool/crash_recovery.py:225-226`; `launch_pipeline.py:331` `context.on("page", ...)`; `listeners.py:33-61` no idempotency guard |
| 7 | `[terminal]` extra deps path-pinned; wheel strips → `pip install ...[terminal]` 404s externally | **TRUE** | `pyproject.toml:80-84` deps; `:73-76` path sources; `:77-79` comment admits "aren't on PyPI yet" | 
| 8 | CLI `scenario start` builds no `terminal_pool` → raises for terminal participants | **TRUE** | `cli/scenario.py:55,58`; `scenarios_pool.py:186-190` raises when `terminal_pool is None` |

### Medium
| # | Finding | Verdict | Evidence |
|---|---------|---------|----------|
| 9 | Idempotency reuses key without proving old owner dead; key not namespaced by follower/method/args | **TRUE** | `server/_idempotency.py:152` `owner != owner` only |
| 10 | Bridge-state unlocked read-modify-write loses concurrent follower reg | **TRUE** | `bridge_state.py:171-180` read→modify→atomic-replace, no lock |
| 11 | Terminal poll task unsupervised; `send_input` returns `ok:true` after EOF | **TRUE** | `terminal/engine.py:115` create_task no done-cb; `:138-148` silent return; `server/terminal/lifecycle.py:96-99` always ok |
| 12 | Scenario `stop` pops before teardown, no shield (unlike `_rollback_start`) | **PARTIAL** | `scenarios_pool.py:339` pop before teardown `:343-359`; `:317` rollback uses shielded CancelScope |
| 13 | Shutdown closes browsers but never closes `terminal_pool`; skips Playwright/tmp cleanup | **PARTIAL** | `cli/serve.py:451` reaper closes browsers; terminal_pool never closed |
| 14 | 512 discovery-index cap + negative-cache skips rescan → past-cap recording unaddressable | **TRUE** | `defaults.py:410` cap 512; `http/discovery.py:244-245` popitem; `:274-278` negative-cache skip |
| 15 | FS ancestor symlink-swap race in containment | **FALSE** | `_paths.py:39-45` resolves fully; `:107-122` temp-sibling in resolved parent + `os.replace`. Safe. |
| 16 | No request-body ceiling; WS frames flush by count/time not bytes; JSONL limit off by default | **TRUE (partial)** | `http/routes/_common.py:24` unbounded `request.body()`; `session/core_io_mixin.py:119-124` no byte ceiling; `recorder.py:31-32` off by default |
| 17 | Demo `bundle.id` → path + `rmtree`, no slug validation | **TRUE** | `scripts/demos/_shared.py:94-95`; `tools/octowright_demos/catalog.py:208` id unchecked |
| 18 | Closed-terminal drops `connector_type`; xterm hardcoded 80×25; frontend types omit `telnet` | **TRUE (nuanced)** | `http/discovery.py:93-105` connector_type omitted; `terminal-view.ts:72-73` 80×25; `types.ts:62` no telnet |
| 19 | `dashboard-state.ts` `.catch(()=>empty)` masks backend 500 as "no sessions" | **TRUE** | `dashboard-state.ts:23-30,45-46` |
| 20 | Replay-classification invariant test scans `session/` only; misses 6+ emitters | **TRUE** | `tests/test_replay_passive_covers_recorder.py:49` scans SRC/"session"; emitters in crash_recovery/listeners/conditional/terminal + `recording_truncated` |
| 21 | Frontend supply chain | **PARTIAL** | reviewer premise wrong — NO lockfile exists (not "committed"); `npm install` not `ci` (`ci.yml:112`, `release.yml:30,231`) TRUE; no audit step TRUE; specific CVE versions **unverifiable** w/o lockfile |
| 22 | `personas.py:388` leaks 200 chars helper stderr into MCP error | **TRUE** | fixed |

---

## Batch A — bounded correctness fixes (IN PROGRESS)

One commit per finding on this branch. TDD each. `make ci` before hand-off.

- [x] **#22** creds helper stderr no longer surfaced to caller (exit code only; length to debug log). `personas.py`
- [x] **#17** demo `bundle.id` slug/containment validation before path join + rmtree. `scripts/demos/_shared.py` `_safe_child`
- [x] **#8** CLI scenario builds + passes `terminal_pool` (`_make_terminal_pool`, threaded to start/stop + closed). `cli/scenario.py`
- [x] **#13** shutdown closes `terminal_pool` (`_close_terminal_pool_on_shutdown`, best-effort force). `cli/serve.py`. NOTE: browsers were already closed by the reaper (reviewer's browser claim was moot); the residual `pool.shutdown()` driver-stop + tmp-dir cleanup on daemon exit is DEFERRED to Batch B (needs `shutdown_pool` idempotency check).
- [x] **#19** frontend state layer keeps last-known slice data on fetch failure + exposes `DashboardState.errors: Set<DashboardScope>` (was `.catch(()=>empty)` wiping panels on a 500). `dashboard-state.ts`. NOTE: a visible per-panel degraded badge (consuming `.errors` in `dashboard-panels.ts`) is a small follow-up; the silent-disappearance bug itself is fixed.
- [x] **#20** widened invariant scan to browser emitter roots (session+browser_pool+conditional; terminal excluded as separate replay domain); classified surfaced markers `page_crash`/`page_recovered`/`try_each_succeeded`/`try_each_branch_failed`/`try_suppressed`/`recording_truncated` as `_REPLAY_PASSIVE`; accept `CONDITIONAL_ACTIONS` bucket for `if_selector`. `macros/runtime.py`
- [x] **#11** poll task now has a done-callback (`_on_poll_done` → pure `terminal/supervision.poll_done_reason`) recording an `error` stop on unexpected death instead of dying silently; `send_input` raises new `TerminalDisconnectedError` (uterm-free `errors.py`) after EOF and `terminal_send_input` maps it to `{"ok": false}` instead of `ok:true`. Pure decision helper TDD'd on core (`tests/test_terminal_supervision.py`); engine/tool wiring + `test_engine` update run only under the `[terminal]` extra. RESIDUAL (Batch B): the poll-death path records an error stop but does not evict the pool entry (engine holds no pool ref).
- [x] **#6** `_wire_listeners` now idempotent per page (per-session `_wired_pages` WeakSet); crash-recovery pages-list update converges (new page present once, dead removed) whether or not the `page` event ran first. `listeners.py`, `crash_recovery.py`, `session/core.py`
- [x] **#4** last-page close now schedules the full idempotent `pool.close(force=True)` (context/browser/bg-tasks/lock torn down) instead of bookkeeping-only eviction; context.close/disconnect stay bookkeeping (resource already dead). Close task held off `session._bg_tasks` to avoid drain self-deadlock; racing evict → KeyError swallowed. `listeners.py`
- [x] **#3** `target_url` now validated in `_launch_impl` BEFORE any allocation (was post-registration → leaked a registered browser on rejection); removed the redundant post-register check; `CancelledError` after registration routes through new `cancel_cleanup_after_register` (pop + close) instead of the registered-skip no-op. `pool.py`, `launch_pipeline.py`

## Batch B — design/locking (IN PROGRESS)
- [x] **#12** `stop()` teardown wrapped in `anyio.CancelScope(shield=True)` (matches `_rollback_start`) so a cancel mid-teardown still closes every participant. `scenarios_pool.py`
- [x] **#10** `_state_lock` (blocking flock on `.lock` sibling) serializes both read-modify-replace transactions (`record_snapshot`, `remove_followers`); Windows/lock-OSError degrade to pre-fix unlocked. Deterministic barrier test proves both concurrent followers survive. `bridge_state.py`
- [x] **#14** `_build_recording_index` now reports `saturated`; the negative cache is authoritative only for a COMPLETE index, and a saturated miss falls through to a targeted `_scan_disk_for_recording` so a past-cap recording stays addressable. `http/discovery.py`
- [x] **#21** CORRECTION: a root `package-lock.json` was ALREADY tracked (reviewer's "no lockfile" premise was wrong; the workspace lockfile lives at repo root, not the sub-package). Real fixes applied: `npm audit fix` patched the 4 high advisories (vite/nanoid/postcss/undici) -> 0 vulns; CI/release switched from `npm install` to root `npm ci --workspace` + a `npm audit --audit-level=high` gate; stale "no lockfile" comment removed. Verified locally: npm ci + build + 352 vitest + typecheck + lint green.
- [~] **#18** PARTIAL: closed-terminal discovery now carries `connector_type` (`http/discovery.py`, matches the live summary); frontend `types.ts` union now includes `telnet`. DEFERRED (b): xterm 80×25 is a DELIBERATE documented choice (BBS/ANSI art authored for 80 cols; FitAddon would stretch it) — honoring recorded PTY geometry means a PTY-vs-telnet branch, a behavior change best done as its own focused change.
- [x] **#9** DECISION: namespacing + honest unknown-outcome (durable journal rejected — browsers die with the leader, so a journaled result is stale; and it would write tool results to disk). Cache key now `_storage_key` = sha256(follower_key+method+canonical_args) (kills wrong-result reuse); in-progress entries are awaited regardless of owner (dedup a still-running producer instead of double-executing), and a bounded-await timeout raises `IdempotencyOutcomeUnknownError` instead of silently re-executing. `server/_idempotency.py`
- [x] **#16** DECISION: all quotas OFF-by-default opt-in knobs. Added `OCTOWRIGHT_MAX_REQUEST_BODY_BYTES` (streaming 413 cap) + `OCTOWRIGHT_WEBSOCKET_MAX_BYTES` (WS sidecar ceiling + truncation marker); recording JSONL limit already existed. Docs in AGENTS.md.
- [ ] residuals: #13 `pool.shutdown()` driver-stop/tmp cleanup on daemon exit; #11 evict pool entry on poll-death

## Batch C — architecture/release decisions (OWNER CALL)
#1 dashboard auth boundary + token bootstrap · #7 publish vs label-experimental terminal extra ·
#2 SSRF default posture (parked — user deciding later)

## Refuted / not-a-bug
- **#15** filesystem containment — safe as written.

## Env note
Sibling `../provide-uterm` checkout is absent, so `uv run` (default resolve) fails on the
terminal path-sources. Use **`uv run --frozen ...`** to run tests/tools against the existing lock.
This is finding #7 biting local dev.


---

# Prior handoffs (preserved — predate this review remediation)

# HANDOFF — Deferred hardening completed: H4a / H4b / H5b (2026-06-26)

## Problem / request

"Take care of the rest of the hardening/minor." The prior round shipped H1/H2/H3/H5a and
deferred three items (each with a real gotcha): H4a auto-relaunch lost sessions, H4b
memory-pressure governor, H5b macOS `.ips` crash-report correlation. This round implements
all three, TDD, lint-green, new code 100% covered.

## Changes completed (all committed, TDD)

| Item | New/changed | What |
|------|-------------|------|
| **H5b** `.ips` correlation | `browser_pool/crash_reports.py` (NEW) + `server/meta.py` | At **status-read time** (the OS writes the `.ips` ~1-2s after the crash; the incident records ~60ms in), correlate each recent `renderer_crash` incident with a macOS DiagnosticReports `*.ips` by mtime window + browser-process name, parse the `SIGSEGV`/`EXC_BAD_ACCESS` signature, and attach `crash_report` to `octowright_status().crash.recent[*]`. macOS-gated, best-effort, never raises into status. |
| **H4b** memory governor | `sysresources.py` (NEW), `browser_pool/errors.py`, `server/browser/lifecycle.py`, `server/meta.py` | `available_memory_bytes()` reads **available** memory per-platform (Linux `/proc/meminfo` MemAvailable; macOS `vm_stat` free+inactive+speculative+purgeable — NOT the misleading sysconf "free"). `OCTOWRIGHT_MIN_FREE_MEMORY_MB` (OFF by default → no read, no false refusals) gates `browser_launch`/`quick_launch`/`spawn_roster` via `_enforce_memory_floor` → `MemoryPressureError`. Unreadable value never refuses. Surfaced at `status.pool.min_free_memory_mb`/`available_memory_mb`. |
| **H4a** lost-session relaunch | `browser_pool/driver_relaunch.py` (NEW), `browser_pool/incidents.py` (CATEGORY_DRIVER_LOST), `browser_pool/pool.py` (1-line hook), `server/meta.py` | On `pool._reset_driver`, `on_driver_reset` records the restart, captures+evicts the sessions lost with the dead driver, and surfaces them at `status.pool.lost_sessions`. **Configurable** `OCTOWRIGHT_DRIVER_RELAUNCH`: `off` (DEFAULT, surface only) / `new-id` (reopen, fresh id, lost record maps old→new) / `keep-id` (reopen + rebind original id so client handles keep resolving). Loop-guarded via `_auto_relaunched` tag. |

## Reasoning / decisions

- **H4a configurability** — Tim's answer to the design question was "it should be configurable",
  so relaunch is an env-gated mode, default `off` (surface-only, no instance_id churn / surprise
  navigation — matches the "user controls the browser" preference). `new-id` and `keep-id` are
  opt-ins. keep-id re-keys the fresh session's dict back to the original id (best-effort; the
  recording file stays under the fresh id — a documented wart).
- **defaults.py LOC ceiling** — defaults.py was at 549/550. The two new env knobs' values+parsers
  live in their **domain modules** (`sysresources.MIN_FREE_MEMORY_BYTES`,
  `driver_relaunch.DRIVER_RELAUNCH_MODE`), mirroring how `incidents._RING_SIZE` / `health.CRITICAL_*`
  already keep their own `OCTOWRIGHT_*` knobs locally. defaults.py carries a one-line pointer. This
  also dodges a defaults→driver_relaunch→browser_pool/__init__→pool→defaults import cycle.
- **pool.py kept minimal** — `_reset_driver` swaps its inline `incidents.record(...)` for a single
  `driver_relaunch.on_driver_reset(self, reason=reason)` call (net pool.py LOC neutral). All H4a
  logic lives in the new module; the verified P3 self-heal path is unchanged except dead sessions
  are now evicted + surfaced.

## Verification

`make lint` EXIT=0 (ruff/format/mypy/ty/bandit/codespell/SPDX/LOC≤550/agent-docs-sync/vulture/xenon/secrets).
`make test` EXIT=0, total coverage **90.91%** (gate 83%). New modules **100%** covered:
`crash_reports.py`, `sysresources.py`, `driver_relaunch.py`, plus `errors.py`. AGENTS.md/CLAUDE.md
env-var docs added for `OCTOWRIGHT_MIN_FREE_MEMORY_MB` + `OCTOWRIGHT_DRIVER_RELAUNCH` (mirrors in sync).

NOTE: behavior changes need a daemon restart to activate (the running daemon is older code).
H4b/H4a default OFF, so default behavior is unchanged except: status now exposes
`pool.lost_sessions` / `pool.driver_relaunch_mode` / `pool.min_free_memory_mb` /
`pool.available_memory_mb`, and `crash.recent[*]` gains `crash_report` on macOS.

## Checklist for next session

- [ ] **Activate**: `uv run octowright restart` (disconnects every client) when Tim is ready —
      the deferred-hardening behavior is in committed code but not yet in the running daemon.
- [ ] (Optional) To exercise H4b live: `OCTOWRIGHT_MIN_FREE_MEMORY_MB=<mb>` and confirm launches
      refuse with `MemoryPressureError` under that floor; verify the macOS available-memory read is
      sane on Tim's Mac (it should report several GB, not "almost none").
- [ ] (Optional) To exercise H4a live: `OCTOWRIGHT_DRIVER_RELAUNCH=new-id`, kill the driver
      (`await pool._pw.stop()` / the chaos test), confirm lost sessions reopen + `status.pool.lost_sessions`
      maps old→new. keep-id: confirm the original id still resolves after self-heal.
- [ ] Minor (pre-existing, not from this round): stale manifest entries in status (`uwarp-*`, old demos)
      clear with `octowright cleanup`; validate `OCTOWRIGHT_MAX_BROWSERS=32` against peak load.
- All P0–P5 + H1/H2/H3/H5a + H4a/H4b/H5b are now done. No deferred hardening items remain.

---

# HANDOFF — Stability: browser orphan leaks + daemon "goes away" (2026-06-26)

## Problem / request

Tim reported two recurring failures:
1. "Another LLM will just keep opening browsers and they'll never close" — dock fills with
   Chromium windows that never get cleaned up.
2. "Sometimes it just goes away" — the Octowright MCP connection drops for a session.

## Diagnosis (evidence-backed)

Live system: **one** leader daemon (PID 5661, `--daemon-mode`, up 3.5 days, healthy, owns
:6286) with **13 live followers** sharing it (5 Codex, 2 grok, 6 claude). Singleton working —
but it concentrates risk. Three root causes:

1. **Orphan leak.** When a Playwright driver (or its leader) dies, its browser windows reparent
   to init (`ppid==1`) and survive forever — the pool can't close them. Confirmed live: browser
   34591 / dead driver 34589. Log: `Browser.close: I/O operation on closed file` (dead stdio pipe
   → close fails → OS process leaks). Only `octowright restart` reaped them.
2. **No reap on auto-respawn.** `serve._ensure_leader_or_inline` / `_respawn_if_leader_gone`
   spawned a fresh daemon WITHOUT reaping. A 3.5-day leader never respawns → mid-session orphans
   pile up indefinitely.
3. **No browser cap + shared fate.** No max-concurrent limit (looping client = unbounded
   browsers). 13 clients on one leader → any leader death OR one LLM running `octowright restart`
   (it's on Tim's PATH) drops all 13 at once = "goes away", orphaning that generation.

Both symptoms are one event: driver/leader dies → calls throw `I/O operation on closed file` →
client panics → `restart` → all sessions drop + browsers orphan.

## Changes completed

| Fix | Where | What |
|-----|-------|------|
| ① orphan reap core | `process_reaper.py` | `scope="orphaned"` + `_is_orphaned_browser()`/`_orphaned_browser_pids()`. `ppid<=1` (POSIX) or parent-not-in-live-pids (Windows). Never touches a live leader's browsers. |
| ① mid-session backstop | `housekeeping.py` (NEW) | `daemon_housekeeping()` periodic leader task (default 60s, `OCTOWRIGHT_HOUSEKEEPING_SECONDS`): reap orphans + bound daemon log. |
| ② reap on spawn | `cli/serve.py` | `_reap_orphan_browsers_at_boot()` in every `_run_leader` (first spawn / restart / auto-respawn). |
| ③ browser cap | `defaults.py`, `server/browser/lifecycle.py`, `errors.py` | `OCTOWRIGHT_MAX_BROWSERS` (off by default, pool-wide). `_enforce_browser_cap()` gates the 3 user-facing launch tools w/ `BrowserCapExceededError`. Internal relaunch/handoff/scenario NOT capped. |
| ④ log hygiene | `housekeeping.py` + cleanup | Mid-run daemon-log truncation (spawn-time rotation never fires on a long leader). Deleted stale junk logs `~/.config/undef/...` + `~/.config/octowright/octowright-daemon.log`. SSE-chunk dumps + `undef` path were OLD builds — NOT in current source. |

New tests: `test_browser_cap.py`, `test_housekeeping.py`, orphan cases in `test_process_reaper.py`.

## Reasoning

- `ppid==1` orphan signal beats per-session PID capture: Playwright Python exposes no PID for
  persistent contexts (the failing case); reparenting is unambiguous + multi-leader-safe.
- Cap default OFF: pool-wide across 13 clients, a low default would block legit heavy use. Opt-in
  knob, actionable error.
- Did NOT fabricate fixes for SSE-chunk/`undef` — not in current source. Deleted stale artifacts,
  added real log bounding instead.

## Verification

Full pytest PASS (exit 0). ruff ✅ mypy ✅ (166 files) vulture ✅ xenon ✅. `make lint` is RED on a
**pre-existing, unrelated** bandit B101 in `scenarios_pool.py:273` + `server/terminal/lifecycle.py:33`
(both in HEAD before this session; make lint aborts at bandit before mypy).

## Follow-up completed (same session)

- **Bandit B101 asserts fixed (TDD).** Added `TerminalPoolUnavailableError(RuntimeError)` to
  `terminal/errors.py`; replaced the two `assert`s (`scenarios_pool.py` `_launch_terminals`,
  `server/terminal/lifecycle.py` `_pool`) with explicit raises that survive `python -O`. Proved
  under `-O` that the old assert was stripped (None slipped through) and the new raise fires.
- **serve.py LOC gate.** The housekeeping/boot-reap wiring pushed `cli/serve.py` from 549→597 (limit
  550). Moved boot-reap + task-creation into `housekeeping.py` (`reap_orphan_browsers_at_boot`,
  `start_housekeeping_task`) and extracted `_log_first_done` + `_run_leader_phases` into new
  `cli/_leader_runtime.py` (re-exported). serve.py now 500. Updated `test_cli_serve_branches.py`
  patch targets accordingly.
- **Per-client MCP reconnect guidance** (Tim's request). Researched reconnect for 12 clients; encoded
  the matrix into the MCP `instructions` string (`server/_state.py`) and the Transport-recovery
  section of AGENTS.md/CLAUDE.md (kept in sync). Key: Claude Code/Cursor/Cline/Copilot-VSCode/
  Windsurf/Gemini-CLI/Copilot-CLI reconnect in-session; **Codex CLI, OpenCode, Amp have NO
  in-session reconnect** — restart the client (loses the conversation).
- **Verification:** `make lint` EXIT=0, `make test` EXIT=0, 0 failed, coverage 90.56%. New code 100%
  covered (`housekeeping.py`, `_leader_runtime.py`, `terminal/errors.py`). Installed missing `webkit`
  browser — 3 live-test failures were webkit-not-installed (environment), not regressions.

## Stability roadmap implemented (same session, committed)

Investigated "browser randomly crashes": macOS DiagnosticReports show `chrome-headless-shell`
`SIGSEGV` in `CrRendererMain` (renderer crashes, not OOM — FD/mem were fine); zero headed-process
crashes. Octowright detected crashes but never recovered. Roadmap P1–P5, each TDD'd + lint-green +
new code 100% covered, committed separately:

- **P2 (commit 9051777)** — browser cap defaults ON at 32 (`OCTOWRIGHT_MAX_BROWSERS`); surfaced as
  `octowright_status().pool.browser_cap`.
- **P4 (9051777)** — `--disable-dev-shm-usage` for Chromium on Linux (headed + headless); the old
  headless path passed no args, so container/CI `/dev/shm` exhaustion caused renderer crashes.
- **P1 + P5 (68340ed, then fixed in e2e9b26)** — `browser_pool/crash_recovery.py`: a renderer crash
  auto-recovers, bounded by `CRASH_RECOVERY_MAX=3` with a `RESET_SECONDS=60` crash-loop reset,
  keeping the session. `octowright_status().crash` surfaces crashes/recoveries/recovery_failures.
  **NOTE:** the original commit recovered via `page.reload()`, which **live verification proved does
  NOT work** for a `chrome-headless-shell` renderer crash — `reload`/`goto` on the dead page fail
  forever with "Page crashed". `e2e9b26` fixes it: recover by **replacing** the dead page with a
  fresh one in the surviving context (`_replace_crashed_page`), since the browser/context survive a
  renderer crash and only the page object dies.
- **P3 core (6fdb987)** — `browser_pool/driver_health.py` + `pool._reset_driver()`: a dead shared
  Playwright driver no longer bricks the pool — `pool.launch` detects driver-death, rebuilds the
  driver, retries once. `status.pool.driver_restarts` surfaces it.

## Live verification (restarted daemon, drove the real MCP surface)

Restarted the daemon to activate the changes, then `/verify`'d against the real crash:

- **Orphan reap — PASS (live):** `octowright restart` reaped 4 orphans; the new boot-reaper
  (`housekeeping.reap_orphan_browsers_at_boot`) caught 2 more (log: `octowright.boot.reaped_orphan_browsers`).
- **P2/P3/P5 surfacing — PASS:** `octowright_status` shows `pool.browser_cap=32`,
  `pool.driver_restarts=0`, and the `crash` block.
- **P1 recovery — caught a real bug, then PASS after the fix.** Killed a browser's renderer with
  `SIGSEGV` (the exact `.ips` signature). Pre-fix: `recovery_failed: 'Page.reload: Page crashed'`,
  page permanently dead. Fixed (`e2e9b26`), restarted, re-ran the SIGSEGV → log shows
  `page_crashed` → `crash.recovered attempt=1` (59ms); process tree shows the old renderer dead and
  a fresh one under the same browser root; the recording shows `page_recovered` + a successful
  `user_navigation`. The session self-healed and stayed usable under the same instance_id.
- Caveat seen during the run: the follower MCP bridge timed out twice while the leader executed the
  call anyway (a transient bridge hiccup, not a recovery failure) — verified server-side instead.

## Hardening round (after live verification caught the reload bug)

The meta-lesson: the broken recovery shipped green (100% unit coverage) because the
tests mocked `page.reload()`. Hardening to prevent / catch / articulate, all TDD'd + committed:

- **H1 live chaos regression tests (commit 35a8508)** — `test_stability_chaos_live.py`:
  CDP `Page.crash` a real renderer → assert recovery + usable (FAILS on the old reload
  recovery); `Playwright.stop()` the driver → assert self-heal. The guards that would have
  caught e2e9b26. Marked `live_browser`.
- **H2 incident records + H3 health verdict (3360045)** — `browser_pool/incidents.py` (bounded
  ring of crash/driver incidents with url+outcome+ts) and `browser_pool/health.py`
  (`assess() → {ok|degraded|critical, reasons}`). `octowright_status` grew `health`,
  `crash.recent`, `pool.driver_restart_recent`; logs WARN when degraded.
- **H5a recovery artifact (this commit)** — on successful recovery, best-effort screenshot of
  the recovered page next to the recording; path lands in the incident record.

### Deferred H4/H5 (designed, real gotchas found — warrant a focused pass)

- **H4a — auto-relaunch sessions lost to driver death.** Driver self-heal (P3) restores the
  pool but the lost sessions aren't recreated. Gotcha: relaunch changes instance_ids and
  re-runs navigation across every client → needs design sign-off.
- **H4b — memory-pressure launch governor.** Refuse launches under low memory to prevent the
  OOM→renderer-crash cascade. Gotcha: macOS "free" memory (SC_AVPHYS_PAGES) is misleading
  (most RAM is cache/purgeable) → a naive threshold causes FALSE refusals on Tim's platform.
  Needs proper per-platform available-memory reading (Linux `/proc/meminfo` MemAvailable;
  macOS `vm_stat` free+inactive+purgeable+speculative), not a sysconf one-liner.
- **H5b — macOS `.ips` crash-report correlation.** Attach the real `chrome-headless-shell`
  `SIGSEGV` signature to the crash incident. Gotcha: the OS writes the `.ips` a second or two
  AFTER the crash, but the incident is recorded ~60ms in — so correlation must happen at
  status-read time (with a window), not at crash time.

## Checklist for next session

- [ ] **Activate**: the running daemon is OLD code. ALL of this turn's behavior needs a daemon
      restart (disconnects every client) — do it when Tim is ready: `uv run octowright restart`.
- [x] ~~Decide a recommended `OCTOWRIGHT_MAX_BROWSERS`.~~ DONE — defaults to 32, configurable.
- [x] ~~Clear the pre-existing bandit B101 asserts.~~ DONE.
- [x] ~~Surface live browser count + cap in `octowright_status`.~~ DONE (pool.browser_cap, crash, driver_restarts).
- [ ] **P3 follow-up (deferred, needs design sign-off):** after driver self-heal, optionally
      auto-relaunch the sessions that were lost to their last URL+profile. Deferred because it
      changes instance_ids / re-runs navigation across every client — a product decision.
- [ ] Validate 32 is the right cap for Tim's peak multi-client load; raise if launches start refusing.

---

# HANDOFF — Crash/disconnect detection & bridge observability

## Problem / request

While driving many parallel browsers during a token benchmark (2026-06-09), Octowright
"disconnected" from the MCP client twice. The agent experienced this as random flakiness.
Root-cause investigation (logs, not guesses) showed these were **recoverable** bridge/browser
events that Octowright *tolerates but does not observe or report*. Request: integrate this
class of crash/disconnect **detection + surfacing** into Octowright so the agent is told
"browser X crashed" / "bridge reconnected, retrying" instead of seeing an opaque error.

## Investigation findings (evidence)

1. **Transport drop = leader/follower bridge blip, not a server crash.** MCP stderr:
   `Tool 'browser_launch' failed after 1s: MCP error -32000: Connection closed`; daemon log at
   the same instant: `octowright: stdio client disconnected; leader staying alive`. The leader
   daemon survived; the per-session stdio→leader bridge dropped the **in-flight** call, then
   auto-reconnected (next health GET :6286 → 200). The agent only saw `-32000`.
2. **A managed Chrome-for-Testing genuinely crashed** — `EXC_BREAKPOINT / SIGTRAP`, parent
   `node` (`~/Library/Logs/DiagnosticReports/Google Chrome for Testing-2026-06-09-180711.ips`).
   Octowright evicted the session but reported it (to MCP) as a generic `user_close`, so the
   agent never learned a browser had *crashed*.
3. **State leak → status bloat.** `bridge-state.json` accumulated **641 `followers`** (167 KB)
   — stale per-PID snapshots that are never pruned. `octowright_status` then returned
   **242,906 chars / 7,762 lines**, which exceeded the MCP client's tool-result token limit
   (the call was unusable; it had to be dumped to a file).
4. **OpenTelemetry context bug on every call.** `ValueError: Token ... was created in a
   different Context` from `opentelemetry…contextvars.detach`, logged on **every**
   CallToolRequest. Caught/non-fatal, but it dominates the daemon log (most of 9,246 lines) and
   would bury a real fault.

## Reasoning / approach

Octowright already auto-recovers (leader stays alive, sessions get evicted, `events` are
capped). The gap is **observability + two leaks**, and most fixes **reuse existing
scaffolding** rather than new subsystems:
- `bridge_state.summarize_state()` already exists — status just also splats the raw state.
- `SessionClosedEvent` already reserves an `external_disconnect` reason.
- `events` is already capped to 50; `followers` simply wasn't given the same treatment.
Do the cheap, high-impact items (P0) first; they stop the bleeding (242 KB status, unbounded
growth) with near-trivial diffs.

## Work done this session

- Root-caused from: `~/.local/state/octowright/logs/octowright-daemon.log`,
  `~/.local/state/octowright/bridge-state.json`,
  `~/Library/Caches/claude-cli-nodejs/<proj>/mcp-logs-octowright/*.jsonl`, and the macOS `.ips`.
- Located the exact code sites (below). **P0s now implemented + tested (2026-06-10, TDD); full
  suite green.** P1/P2 remain scoped proposals.

## Checklist for next session (prioritized; line numbers approximate)

### P0 — `octowright_status` payload bloat ✅ DONE (2026-06-10)
- [x] `src/octowright/server/meta.py` → `octowright_status()`: replaced the raw `**bridge_snapshot`
      splat with `**bridge_state.bounded_view(bridge_snapshot)` (caps followers to 25 most-recent
      by ts + events to 20, sets `followers_truncated`). `summary` still summarizes the FULL state,
      so `follower_count` reports the true total. New `bounded_view()` lives in `bridge_state.py`.
- [x] Test: `tests/test_status_tool.py::test_status_bridge_block_caps_exposed_followers` — 60
      followers → exposed dict ≤ 25, `summary.follower_count == 60`, `followers_truncated is True`.

### P0 — followers leak ✅ DONE (2026-06-10)
- [x] `src/octowright/bridge_state.py` → `record_snapshot()` now calls `_prune_dead_followers(...,
      keep_pid=follower_pid)` after writing: drops followers whose PID is dead (`os.kill(pid,0)` →
      ProcessLookupError; conservative — ambiguous errors count as alive), always keeping the
      just-written follower. Bounds the registry to live followers.
- [x] Test: `tests/test_bridge_state.py::test_record_snapshot_prunes_dead_followers` — a stale
      dead-PID follower is pruned when a live follower records; the live one is kept.
- Verified on the live file: prune dropped 1 dead of 8 on-disk followers (live ones kept).

### P1 — browser **crash** detection & surfacing — ✅ DONE (2026-06-10)
Re-investigated the "Playwright can't distinguish" claim with evidence. It is true ONLY for the
`disconnected` event in isolation — but Playwright DOES expose `page.on("crash")` (Target.crashed /
"Aw, Snap"), which octowright wired nowhere. `Browser.process` is NOT exposed in Playwright Python,
so the raw exit signal (SIGTRAP) genuinely can't be read — but the crash event is the reliable lever.
- [x] DONE: wired `page.on("crash")` (initial + popups, via the `_on_page_close` pattern →
      `session._on_page_crash`). On a crash it sets `session._crashed`, increments
      `octowright_browser_crashed_total`, logs `octowright.browser.page_crashed`, records a recorder
      `page_crash` marker, and publishes a **proactive** `SessionCrashedEvent(scope="renderer")` →
      new MCP notification `notifications/octowright/browser_crashed` (with an actionable `hint`), so
      the client learns the page died immediately — not only on its next failing tool call.
- [x] DONE: `_evict` now upgrades the reason to `SessionClosedEvent(reason="crashed")` when a crash
      was observed on the session (else honest `user_close`); new `"crashed"` value added to
      `SessionCloseReason`. `pool.get` on a crashed-then-evicted instance says *"crashed (its process
      died) — relaunch"* vs the generic *"ended unexpectedly"* (`_recently_evicted: dict[str,bool]`).
- [x] REPRODUCED for real: `tests/test_pool_crash_live.py` (`live_browser`) launches chromium,
      navigates to `chrome://crash`, and asserts the crash is detected + the proactive event fires.
      Stub-based tests in `test_pool_disconnect.py` cover the evict-reason upgrade + crash message;
      `_build_notification` crash branch in `test_mcp_notifications.py`. Full suite green (90.5% cov).
- [ ] NOT possible via Playwright Python: capturing the child exit signal (SIGTRAP/SIGSEGV) — the
      managed Chrome is a grandchild of the node driver and `Browser.process` is unexposed. A pure
      hard-kill that fires NO `page.on("crash")` stays honestly `user_close` (best achievable).

### P1 — bridge in-flight resilience — ✅ DONE (2026-06-11; plan: `.claude/plans/wiggly-chasing-kazoo.md`)
Both halves landed on the telemetry branch, TDD throughout (27 new tests). Full non-live suite green
(3949 passed, 90.4% cov); ruff + `mypy src/octowright` clean. Kill switch `OCTOWRIGHT_IDEMPOTENCY=0`
restores today's fail-safe wire format + behaviour exactly.

**#1 — leader-side idempotency + safe bridge resume** (was the "real gap", now closed):
- [x] The follower injects a stable `octowrightIdempotencyKey` (`owk-<uuid4>`) into each tools/call's
      `_meta` and stores the injected frame on the `InFlightRequest` (`proxy_supervisor._inject_meta`,
      new InFlightRequest fields `idempotency_key`/`outgoing`/`resume_count`).
- [x] **Leader dedup cache** — new `server/_idempotency.py` (`_idempotent_dispatch`, composed OUTSIDE
      `_track_advisor_usage` in `_ProfiledFastMCP.tool`). Process-global, lock-guarded, TTL + LRU
      bounded, success-only. Handles the nasty cases: IN_PROGRESS owned by a dead session →
      **takeover**; same-session waiter → **bounded await** backstop; any exception incl.
      `CancelledError` (the reconnect kills the old session's tool coroutine) → **evict→rerun**;
      over-cap results → DONE-marker (dedup holds, resend re-runs). Read `_meta` key via the lowlevel
      `request_ctx` contextvar (`RequestParams.Meta` is `extra='allow'`).
- [x] **Bridge auto-resume** replaces the old blind-fail: on reset, `fail_or_mark_for_resume` KEEPS
      resumable keyed requests in-flight and fails only the rest; on the next connect's success path
      (after `replay_initialize`) `resume_in_flight` re-sends them verbatim (same id/key) on the fresh
      session — the leader dedups, so no double-execution. Deadline re-armed on resume; bounded by
      `BRIDGE_RESUME_MAX_ATTEMPTS` (3) then failed with the retry-hint. The old "blind retry is unsafe"
      hazard is gone precisely because the key makes the re-send idempotent.
- Defaults: `IDEMPOTENCY_{ENABLED,TTL_SECONDS,MAX_ENTRIES,MAX_RESULT_BYTES,INPROGRESS_WAIT_SECONDS}`,
      `BRIDGE_RESUME_MAX_ATTEMPTS` — with the TTL>resume-window invariant commented in `defaults.py`.

**#2 — multi-action replay timeout + silent partial execution** (the observed `order_brightmart` case):
- [x] **Per-tool timeout floor** (`BRIDGE_TOOL_TIMEOUTS`, `proxy_supervisor._timeout_for`): browser_launch
      ~105s / macro_run 120s / macro_run_sequence 180s replace the flat 20s for these tools. Also fixes
      the latent `browser_launch` (90s) > 20s-bridge-timeout bug.
- [x] **Progress-driven deadline extension**: `macro_run`/`macro_run_sequence` take a FastMCP `ctx`
      (hidden from the client schema) and `ctx.report_progress` per step; the bridge injects a synthetic
      progressToken, re-arms the in-flight deadline on each progress notification, and swallows the
      synthetic ones (forwards client-supplied ones). A progressing macro never spuriously times out;
      a genuinely hung one still does, at its per-tool floor.
- [x] **Structured partial result**: a mid-macro failure payload now carries `executed` (count) +
      `executed_actions` (credential-redacted descriptors of the steps that landed), so a half-applied
      replay reports its state instead of an opaque `-32000`.
- [x] **Live end-to-end smoke** — `tests/test_bridge_idempotency_live.py` (`live_browser`). Spawns a real
      `octowright serve` (→ leader daemon), connects STRAIGHT to the leader's `/mcp/` with raw JSON-RPC,
      and sends two `browser_launch` frames carrying the SAME `_meta.octowrightIdempotencyKey` (what a
      resumed forward looks like on the wire): asserts the same instance + `browser_list count==1` (dedup
      suppressed the 2nd real launch), then a different key → a 2nd browser (`count==2`). Passed (~8s, one
      real headless chromium deduped). NOTE discovered while writing it: the follower bridge OVERWRITES any
      client idempotency key with its own per-request key (correct — keys are bridge-owned; the same key
      recurs only on resume), so a faithful dedup test must hit the leader directly, not via the follower.

### P1 — `browser_snapshot` times out on heavy DOMs ✅ DONE (2026-06-10)
- [x] `inspect.py:browser_snapshot` wraps `session.snapshot()` in `asyncio.wait_for(timeout=
      SNAPSHOT_TIMEOUT_SECONDS)` (new default 12s, below the 20s bridge timeout). On timeout it
      returns a typed degraded result `{snapshot_timed_out, timeout_s, hint}` pointing at
      browser_read_markdown / browser_brief / a scoped selector, instead of hanging. New fields on
      `BrowserSnapshotResult`. Test: `test_server_browser_inspect_tools.py::test_snapshot_degrades_on_timeout`.

Original diagnosis (kept for context):
- [ ] `src/octowright/server/browser/inspect.py` → `browser_snapshot` (~L61) routes through
      `session.snapshot(selector="body")` → Playwright `locator(...).aria_snapshot()`. The
      tree generation has **no internal timeout or size bound**; on a large DOM (e.g. the
      developer.microsoft.com / M365 dashboard) it runs long enough to hit the MCP **transport
      timeout**, which an agent **cannot distinguish from a disconnect**. The `max_chars` cap
      only trims the result *after* the expensive tree is built. (`browser_capture_and_close`
      ~L217 uses the even heavier `locator("html").aria_snapshot()`.)
- [ ] Wrap `aria_snapshot` in `asyncio.wait_for(...)`; on timeout return a **typed degraded
      result** (`{snapshot_timed_out: true, hint: "heavy DOM — use browser_read_markdown or a
      scoped selector"}`) and/or auto-fall back to `browser_read_markdown` / a body-or-region
      scope, instead of hanging until the transport gives up.
- [ ] Independently reproduced on a SECOND Octowright (v0.3.0, health `:8765`) during an M365
      signup-macro recording — so it generalizes, not just our degraded leader. Today agents
      must rediscover `browser_brief` / `browser_read_markdown` / JS-extract fallbacks by hand.
- [ ] Test: snapshot of a synthetic huge DOM returns a degraded/typed result within the bound,
      not a transport timeout.

### P1 — macro replay: save-hygiene + progress-pill locator collision
- [x] DONE (2026-06-10): `macros/recording_import.py` adds a `RECORDER_NOISE` set (user_navigation,
      console, websocket_*, markdown_cached, *_cache_error, etc.) stripped by `iter_macro_actions`
      alongside `ALWAYS_STRIP`, so `macro_save` no longer bakes passive recorder events into macros.
      Test: `test_macro_recording_import_branches.py::...::test_strips_recorder_noise`.
- [x] DONE (2026-06-10): **Progress-pill overlay no longer collides with text locators.** The pill
      injected a status overlay `<span data-role="label">… | click_by text=Place order</span>` in the
      **light DOM**; its text matched `get_by_text("Place order")` → 2 elements (the real button + the
      pill) → Playwright strict-mode violation, so the macro's own instrumentation broke the macro.
      Fix (`browser_pool/_assets/macro_pill.js`): the pill renders its contents inside a **closed**
      shadow root (`root.attachShadow({mode:"closed"})`), which Playwright's text/role/css locators
      cannot pierce — so the only `Place order` match is the real target. Internal queries
      (label/elapsed) moved to the stored `pillShadow` ref; host styling/click handler/modal stay in
      light DOM. (Closed, not open: OPEN shadow roots ARE pierced by Playwright.)
      Note: the Alt+click history **modal** stays in light DOM — it is user-gesture-only and never
      present during automated replay, so it is not a replay-time collision.
- [x] Test (`tests/test_macro_pill_overlay.py`, `live_browser`): real chromium, inject the pill,
      push a status echoing `click_by text=Place order` next to a real "Place order" button, assert
      `get_by_text("Place order").count() == 1` (was 2 pre-fix) and the pill still renders + the modal
      still works. `tests/test_pill.py` updated to read the closed shadow via an `attachShadow`-capture
      test hook (same behavioral assertions; no production test-hooks).

### P2 — OTel context-detach storm ✅ FIXED UPSTREAM in provide.telemetry (2026-06-10)
Root-caused: NOT octowright's `_tracing.span()` per se, and NOT an mcp-integration requirement
(mcp/FastMCP is uninstrumented; it uses its own clean `request_ctx` contextvar). OTel keeps the
current span in a `contextvars` Token (`start_as_current_span` attaches on enter, detaches on exit).
When a span's lifetime straddles an async-context boundary — an async generator `aclose()`d from
another task, a cancelled/GC'd coroutine — the detach runs in a different `contextvars.Context`
than the attach, `Token.reset()` raises, and `opentelemetry.context.detach` logs a traceback **per
occurrence**. Reproduced and confirmed it is **independent per-teardown (no cascade)**: the ~1410
daemon lines were 1410 separate cross-context teardowns of octowright's **background-task** spans,
logged interleaved with every mcp request type (which is why `ListTools`/`ListPrompts` showed it
too, despite opening no spans).
- [x] **Fixed in `provide.telemetry`** (branch `fix/otel-context-detach-storm`, commit `d4a10314`):
      `setup_tracing()` installs `_SafeContextVarsRuntimeContext`, whose `detach` swallows **only**
      the benign cross-context `ValueError` (idempotent global swap of
      `opentelemetry.context._RUNTIME_CONTEXT`); plus a new async-native `provide.telemetry.span()`.
      TDD, 100% stmt+branch coverage, full suite green (2339), `make lint` green. E2E verified
      through the public `setup_telemetry()`: 50 cross-context teardowns → **0** detach logs.
- [x] **DONE (2026-06-11): bumped `provide-telemetry[otel]>=0.4.8`** — the fix shipped in v0.4.8, so
      the storm disappears with no octowright code change. octowright went further and adopted the
      library's span/metrics/middleware wholesale (see the telemetry-adoption section below). The old
      plan to rewrite `octowright._tracing.span()` is moot.

## provide.telemetry adoption + HTTP metrics migration ✅ DONE (2026-06-11)

Follow-on to the P2 OTel fix: provide.telemetry shipped the cross-context-detach fix **and** new
APIs (`span()`, `TelemetryMiddleware`) in **v0.4.8**, so octowright adopted them wholesale on branch
`chore/adopt-provide-telemetry-tracing`.

### Done
- [x] Bumped `provide-telemetry[otel]>=0.4.8` (commit `8221c75`). Detach storm gone, no octowright
      code change (the runtime-context guard ships in 0.4.8).
- [x] Dropped octowright's local `span()`/`set_attrs()`/`record_exception()`/lazy `counter`/
      `histogram`; `_tracing.py` is now a thin re-export of the **governed** provide.telemetry helpers
      (commit `0395b46`). ~760 LOC net deleted across the branch. All ~15 `span()` call sites + the
      metric instruments (launch / evict / crash / navigate / bridge / macro / artifact) ride the
      library, transitively gaining the cross-context teardown guard.
- [x] **Migrated HTTP metrics → `provide.telemetry.TelemetryMiddleware(auto_slo=…)`** (uncommitted
      working tree). Deleted `http/metrics.py` (HttpMetrics / render_prometheus / HttpMetricsMiddleware)
      and the bespoke `GET /api/metrics` Prometheus scrape endpoint. RED metrics
      (`http.requests/errors/duration`) + request-id/session-id log correlation + W3C propagation +
      cardinality-safe route normalization now flow through the library → OTLP, uniform with the rest
      of octowright. `OCTOWRIGHT_HTTP_METRICS` now gates `auto_slo` only (context propagation stays on).
      Docs updated (README, architecture README, MCP-SHARED-CONTRACT). Tradeoff: no collector-free
      local scrape view anymore — point an OTLP collector at the process.

### Comprehensive code review (3 parallel reviewers + adversarial verification) — all actionable items closed
- [x] MEDIUM — global SLO state leaked across tests (the always-on middleware writes process-global
      `slo._counters` on every HTTP test). Fixed with a module-scoped autouse `_reset_slo_counters`
      fixture in `tests/test_http_server.py` using the narrow `slo._reset_slo_for_tests()` (not the
      heavyweight global plugin). TDD: demonstrated RED contamination → GREEN; order-independent
      across pytest-randomly seeds.
- [x] LOW — stale README version strings (`>=0.3` / `^0.3.0`) → corrected to `>=0.4.8` / `^0.4.7`.
- [~] LOW/info — **PARKED (2026-06-11): `_trace_propagation.py:186-188` `octowright.mcp.request` span
      uses the raw tracer, skipping provide.telemetry governance** (consent → sampling → backpressure
      → health → log-id-sync that `_open_span`/`span()` apply). **Decision: leave it on the raw
      tracer.** Rationale — the raw tracer is the sanctioned escape hatch for manual-lifecycle spans;
      this span MUST close early at `http.response.start` (the SSE response stays open for minutes;
      holding the span that long floods the 2048-span batch buffer and silently drops spans), which
      `span()`'s block scope cannot express. Pre-existing (unchanged by this branch) and intentional.
      Impact of parking = nil unless consent/sampling gating is configured, and even then only this
      one low-sensitivity anchor span (carries `method` + `path` only, no payload/PII) escapes
      provide.telemetry's *extra* governance layer — it still obeys the OTel SDK's TracerProvider
      sampler. A governed-manual API would cost footgun surface + mutation/coverage upkeep + 4-language
      parity (or drift) for a niche gain. If ever genuinely needed: a tiny Python-only documented
      helper — not now. **Do not re-litigate without a concrete sampled-deployment requirement.**

### Verification
- ruff + `mypy src/octowright` clean; full non-live suite **3922 passed**, coverage **90.4%** (gate 83%).
- Pre-existing, out-of-scope: mypy flags `tests/test_http_server.py:618` (`at_times[0]` on
  `list[float] | None` in an unrelated test helper). CI mypy only targets `src/octowright`, so test
  files aren't type-gated — left as-is.

### Branch state
- `chore/adopt-provide-telemetry-tracing`: commits `0395b46` (adopt span/metrics) + `8221c75` (pin).
  The HTTP-middleware migration, the SLO-reset fixture, and the README/doc fixes are **uncommitted**
  in the working tree (left for the auto-commit flow). Not yet merged to `main`.

## Evidence artifacts (this machine, 2026-06-09)
- daemon log: `~/.local/state/octowright/logs/octowright-daemon.log`
- bridge state (641 followers): `~/.local/state/octowright/bridge-state.json`
- MCP stderr (the `-32000` line): `~/Library/Caches/claude-cli-nodejs/-Users-tim-code-gh-provide-io-benchmarks-token-compare/mcp-logs-octowright/*.jsonl`
- Chrome crash (SIGTRAP): `~/Library/Logs/DiagnosticReports/Google Chrome for Testing-2026-06-09-180711.ips`
- Reproduced bloat: `octowright_status` → 242,906 chars / 7,762 lines (`bridge` key = 179 KB of it).
