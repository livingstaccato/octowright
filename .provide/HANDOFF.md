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
- [ ] **#11** supervise terminal poll task; `send_input` returns typed disconnected error after EOF
- [x] **#6** `_wire_listeners` now idempotent per page (per-session `_wired_pages` WeakSet); crash-recovery pages-list update converges (new page present once, dead removed) whether or not the `page` event ran first. `listeners.py`, `crash_recovery.py`, `session/core.py`
- [x] **#4** last-page close now schedules the full idempotent `pool.close(force=True)` (context/browser/bg-tasks/lock torn down) instead of bookkeeping-only eviction; context.close/disconnect stay bookkeeping (resource already dead). Close task held off `session._bg_tasks` to avoid drain self-deadlock; racing evict → KeyError swallowed. `listeners.py`
- [x] **#3** `target_url` now validated in `_launch_impl` BEFORE any allocation (was post-registration → leaked a registered browser on rejection); removed the redundant post-register check; `CancelledError` after registration routes through new `cancel_cleanup_after_register` (pop + close) instead of the registered-skip no-op. `pool.py`, `launch_pipeline.py`

## Batch B — needs design/locking (NOT STARTED)
#9 idempotency ownership model · #10 bridge-state locking · #12 scenario-stop shield ·
#14 discovery index >512 · #16 aggregate quotas · #18 terminal schema/geometry ·
#21 commit lockfile + `npm ci` + audit step

## Batch C — architecture/release decisions (OWNER CALL)
#1 dashboard auth boundary + token bootstrap · #7 publish vs label-experimental terminal extra ·
#2 SSRF default posture (parked — user deciding later)

## Refuted / not-a-bug
- **#15** filesystem containment — safe as written.

## Env note
Sibling `../provide-uterm` checkout is absent, so `uv run` (default resolve) fails on the
terminal path-sources. Use **`uv run --frozen ...`** to run tests/tools against the existing lock.
This is finding #7 biting local dev.
