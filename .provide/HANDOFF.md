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
