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

### P1 — browser **crash** detection & surfacing
- [ ] `src/octowright/browser_pool/listeners.py` → `_wire_close_evictor` / `_evict`. The
      `browser.on("disconnected")` path (L67) evicts and publishes `SessionClosedEvent(
      reason="user_close")` for **both** user-close and crash (comment L110–115:
      "Playwright cannot reliably distinguish…"). `SessionClosedEvent` already reserves
      `external_disconnect`. Heuristic: a `disconnected` with **no preceding explicit
      `pool.close`** (and/or a non-zero/signal process exit) = crash → publish
      `reason="crashed"`/`external_disconnect`; stash a crash marker keyed by `instance_id`.
- [ ] Make later tool calls / `browser_list` on a crashed `instance_id` return a clear
      **"browser `<id>` crashed (signal …) — relaunch"** instead of "unknown instance".
- [ ] If Octowright owns the child process, capture its exit signal (SIGTRAP/SIGSEGV) for the
      message (`browser_pool/lifecycle.py`, `events.py`).

### P1 — bridge in-flight resilience
- [ ] `src/octowright/proxy_bridge.py`: retry **idempotent** in-flight calls across a
      stdio↔leader reconnect (bounded) so a `browser_launch` survives a bridge blip instead of
      failing with `-32000`; otherwise return a typed "bridge reconnecting — retry" error.
      Existing smoke tests to extend: `scripts/bridge_reconnect_smoke.py`,
      `scripts/bridge_dead_leader_smoke.py`.
- [ ] **Multi-action replay can exceed the bridge request timeout, and partial execution is
      silent.** 2026-06-09: the recorded `order_brightmart` macro (carrying recorder-noise steps —
      see macro hygiene below) `macro_run` timed out twice (`bridge error: request N timed out
      while waiting for leader response`), half-applied (browser navigated, fill/click never
      landed, `qty=1`), client saw only `-32000`. A hand-stripped 3-step macro then ran in **62
      ms**, so the trigger was the inflated step list — but the bridge should still stream/extend
      the timeout for multi-action ops and return a structured partial-result / idempotent resume
      so a half-applied replay isn't a silent `-32000`. Single-action calls were unaffected.

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
- [ ] **Progress-pill overlay collides with text locators during replay.** Replaying a
      `click_by text="Place order"` failed with a Playwright **strict-mode violation**: Octowright
      injects a status overlay `<span data-role="label">… | click_by text=Place order</span>`
      whose text matches `get_by_text("Place order")` → 2 elements (the real button + the pill).
      The macro's own instrumentation breaks the macro. Fix: keep the pill out of locator
      resolution (closed shadow DOM / `aria-hidden` + non-text marker / exclude from `get_by_text`),
      or have replay scope text locators to content excluding the overlay. Today's workaround:
      record clicks by role/selector, not text.
- [ ] Test: record+replay a `click_by text=…` whose only real match is the target; assert the
      overlay never triggers a strict-mode violation.

### P2 — OTel context-detach storm
- [ ] `src/octowright/_tracing.py` → `span()` (~L50–68) is a **sync** `@contextmanager` using
      `tracer.start_as_current_span(...)` around **async** tool handlers; the span's context
      token is attached in one asyncio task and detached in another → `ValueError: Token created
      in a different Context` on every CallToolRequest. Provide an **async-safe** span (attach/
      detach in the same task, or `opentelemetry.trace.use_span(span, end_on_exit=True)` without
      crossing the contextvar reset over an `await`).
- [ ] Test: a `span()` wrapping an awaiting body emits **no** "Failed to detach context" log.

## Evidence artifacts (this machine, 2026-06-09)
- daemon log: `~/.local/state/octowright/logs/octowright-daemon.log`
- bridge state (641 followers): `~/.local/state/octowright/bridge-state.json`
- MCP stderr (the `-32000` line): `~/Library/Caches/claude-cli-nodejs/-Users-tim-code-gh-provide-io-benchmarks-token-compare/mcp-logs-octowright/*.jsonl`
- Chrome crash (SIGTRAP): `~/Library/Logs/DiagnosticReports/Google Chrome for Testing-2026-06-09-180711.ips`
- Reproduced bloat: `octowright_status` → 242,906 chars / 7,762 lines (`bridge` key = 179 KB of it).
