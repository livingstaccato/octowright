# Comprehensive Review Remediation and 0.14.3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Status (2026-08-12):** This file preserves the pre-execution plan and its
> original unchecked task template. Implementation and local review are
> complete; authoritative results are recorded in
> `docs/reviews/2026-08-12-comprehensive-code-review.md` and the release PR.
> Push, exact-head GitHub Actions, merge, and local-main synchronization remain
> delivery gates until the PR records them.

**Goal:** Close all eight verified review findings and release the corrected repository as version 0.14.3.

**Architecture:** Safety-sensitive operations become ownership- or lock-guarded transactions that fail closed. Dashboard streams carry a revalidatable bearer lease, while paired video moves from full-file blobs to client-scoped service-worker header injection so native Range requests remain intact. Each behavior is developed red-green and verified independently before the combined release.

**Tech Stack:** Python 3.11+, asyncio, Starlette, POSIX `flock`, Windows `msvcrt`, TypeScript 6, Service Worker API, Vitest, pytest, GitHub Actions.

---

### Task 1: Keep live idempotency producers authoritative

**Files:**
- Modify: `src/octowright/server/_idempotency.py`
- Modify: `tests/test_idempotency_windows.py`
- Modify: `tests/test_idempotency_cache.py`

- [ ] **Step 1: Write the cancellation-resistant producer regression**

Add an async test that records one producer call, advances `_now()` beyond the
abandon threshold, starts a same-key caller, and makes the first producer catch
and suppress `CancelledError`. Assert the second caller raises
`IdempotencyOutcomeUnknownError` and the mutation count remains one until the
original producer is explicitly released.

```python
@pytest.mark.anyio
async def test_abandon_threshold_never_reclaims_a_live_producer(monkeypatch):
    monkeypatch.setattr(_idempotency.defaults, "IDEMPOTENCY_INPROGRESS_WAIT_SECONDS", 0.05)
    clock = {"now": 0.0}
    monkeypatch.setattr(_idempotency, "_now", lambda: clock["now"])
    # The tool stays live; age must neither cancel it nor run it twice.
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_idempotency_cache.py::test_abandon_threshold_never_reclaims_a_live_producer -q --no-cov`

Expected: FAIL because the aged entry is deleted and the call count becomes two.

- [ ] **Step 3: Track, but never age-cancel or age-delete, the producer task**

Add `producer_task` to `_Entry` and populate it from `asyncio.current_task()`
when a fresh producer claims the slot. At the age threshold, retain a running
producer without cancelling it. Reclaim directly only when the recorded task is
confirmed done.

- [ ] **Step 4: Update the taskless-orphan test deliberately**

Seed a truly taskless entry to retain the existing synthetic-orphan recovery,
but make the test name and comments distinguish “no producer exists” from “old
producer is assumed dead.”

- [ ] **Step 5: Run GREEN and the idempotency suite**

Run: `uv run pytest tests/test_idempotency_cache.py tests/test_idempotency_windows.py -q --no-cov`

Expected: PASS with one mutation and no age-driven cancellation.

- [ ] **Step 6: Keep the cache hard-bounded without displacing producers**

Set `IDEMPOTENCY_MAX_ENTRIES` to two, occupy both slots with live producers,
then call a fresh distinct key. Assert the fresh call raises
`IdempotencyCapacityError` before its handler runs and the cache remains at two
entries. Start a same-key caller against one occupied slot, release the original
producer, and assert both callers receive the original result from one handler
execution.

Run: `uv run pytest tests/test_idempotency_cache.py::test_capacity_refuses_a_fresh_key_without_displacing_live_producers -q --no-cov`

Expected: PASS with two authoritative producers, zero execution of the refused
handler, and no cache growth beyond the configured bound.

- [ ] **Step 7: Isolate the producer from request cancellation**

Run the handler in a shielded internal task. Cancelling the request waiter must
leave that producer alive to finish into the cache. If the producer itself
fails or is cancelled, retain an unknown-outcome tombstone so the same key is
never executed automatically while its prior outcome may have committed.

Run: `uv run pytest tests/test_idempotency_cache.py -q --no-cov`

Expected: request cancellation does not cancel or duplicate the producer, and
producer failure/cancellation makes a resend report unknown outcome.

- [ ] **Step 8: Fail closed when a successful result is too large to cache**

Set the result-size limit below a successful tool response. Assert the first
caller receives the response, a same-key resend raises
`IdempotencyResultUnavailableError`, and the handler execution count remains
one.

### Task 2: Make browser close and keep-id rekey one identity transaction

**Files:**
- Modify: `src/octowright/browser_pool/lifecycle.py`
- Modify: `src/octowright/browser_pool/pool.py`
- Modify: `src/octowright/browser_pool/listeners.py`
- Modify: `src/octowright/browser_pool/driver_relaunch.py`
- Modify: `tests/test_post_review_hardening.py`
- Modify: the existing driver-relaunch test file located by `rg -n "_finalize_id|keep-id" tests`

- [ ] **Step 1: Write the lock-wait rebind regression using a real `BrowserPool`**

Hold `pool._sessions_lock`, start `_run_deferred_full_close` for the original
session, replace the registry entry with a protected fake session while close is
waiting, release the lock, and assert the original/replacement are both
unclosed and the replacement remains registered.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_post_review_hardening.py::test_deferred_close_does_not_pop_replacement_while_waiting_for_lock -q --no-cov`

Expected: FAIL because the replacement is popped and closed.

- [ ] **Step 3: Add an expected-session close primitive**

Move lookup, protection validation, expected-identity validation, and pop under
`_sessions_lock`. Expose a private pool method used by the deferred listener:

```python
async def _close_if_current(self, instance_id, expected, *, force, _reason):
    return await close_browser(
        self, instance_id, force=force, _reason=_reason, expected_session=expected
    )
```

An identity mismatch returns `None`; explicit public close retains its existing
dictionary result and missing-ID exception.

- [ ] **Step 4: Lock keep-id rekeying**

Make `_finalize_id` async, acquire `pool._sessions_lock`, and perform the
new-id pop, old-id assignment, session ID mutation, and eviction-marker cleanup
inside the lock. Update its caller to await it.

- [ ] **Step 5: Run GREEN and browser lifecycle suites**

Run: `uv run pytest tests/test_post_review_hardening.py tests/test_browser_pool_branches.py tests/test_crash_recovery.py -q --no-cov`

### Task 3: Remove secret-bearing logging and harden daemon-log permissions

**Files:**
- Modify: `src/octowright/personas.py`
- Modify: `src/octowright/daemonize.py`
- Modify: `tests/test_personas.py`
- Modify: the daemonize test file located by `rg -l "_open_daemon_log" tests`

- [ ] **Step 1: Write RED tests for structured fields and legacy permissions**

Capture the `persona.cred.cmd_failed` call and assert neither its message nor any
field contains helper stderr. Create an existing `0644` daemon log, call
`_open_daemon_log()`, and assert its mode is `0600` where permission bits are
supported.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_personas.py -k 'credential and stderr' -q --no-cov`

Run the exact daemon-log test selected in Step 1.

Expected: the logging test sees `stderr_excerpt`; the mode test sees `0644`.

- [ ] **Step 3: Remove raw stderr and use a private open path**

Keep only `returncode` and `stderr_len` in the debug event. Open/create the log
with mode `0600`, best-effort chmod an existing file to `0600`, and return a
binary append handle without changing rotation behavior.

- [ ] **Step 4: Run GREEN**

Run: `uv run pytest tests/test_personas.py tests/test_personas_branches.py tests/test_daemonize.py -q --no-cov`

### Task 4: Fail closed for bridge-state and manifest transactions

**Files:**
- Modify: `src/octowright/bridge_state.py`
- Modify: `src/octowright/session_manifest.py`
- Modify: `src/octowright/housekeeping.py`
- Modify: `tests/test_post_review_hardening.py`
- Modify: `tests/test_bridge_state.py`
- Modify: `tests/test_manifest_orphan_prune.py`
- Modify: `tests/test_session_manifest.py`
- Modify: `tests/test_housekeeping.py`

- [ ] **Step 1: Write bridge-state contention RED**

Replace the current test that expects entry after timeout. Hold the sibling
`flock`, call `record_snapshot()` with a short timeout, and assert the state file
is unchanged and the call returns within the bound.

- [ ] **Step 2: Write manifest lost-update RED**

Use two threads/processes and events so prune reads a stale entry while a launch
attempts to register a live entry. Assert both completed transactions leave the
new live entry present. Add a remove-vs-prune counterpart or generation check
that prevents resurrection.

- [ ] **Step 3: Run RED**

Run: `uv run pytest tests/test_post_review_hardening.py tests/test_manifest_orphan_prune.py -q --no-cov`

Expected: bridge write occurs without a lock and manifest live entry is lost.

- [ ] **Step 4: Make bridge-state lock failure explicit**

Raise a private lock-unavailable exception when open/acquire times out or fails.
Catch it around `record_snapshot()` and `remove_followers()` so the operation is
skipped. Do not yield the context-manager body without ownership.

- [ ] **Step 5: Lock every manifest RMW**

Add one stable `.lock` sibling with per-path thread serialization and bounded
POSIX/Windows cross-process acquisition. Wrap the complete read/modify/replace
transactions in `record_launch`, `remove_session`, and
`prune_dead_daemon_entries`. Use collision-safe temporary siblings.

Run async leader call sites through a cancellation-ordered worker-thread
adapter so lock polling never stalls MCP heartbeats or dashboard streams.
Differentiate contention from permanent lock errors, and release ref-counted
per-path thread-lock entries when idle.

- [ ] **Step 6: Decouple boot cleanup**

Run browser reaping and manifest pruning in separate guarded paths. A reaper
exception logs and then pruning still runs. Remove the “provably gone” claim and
add tests for thrown and partial-failure reaper results.

- [ ] **Step 7: Run GREEN**

Run: `uv run pytest tests/test_post_review_hardening.py tests/test_bridge_state.py tests/test_manifest_orphan_prune.py tests/test_session_manifest.py tests/test_housekeeping.py -q --no-cov`

### Task 5: Revalidate dashboard authorization on established streams

**Files:**
- Modify: `src/octowright/http/pairing.py`
- Modify: `src/octowright/http/exposure.py`
- Modify: `src/octowright/http/routes/events.py`
- Modify: `src/octowright/http/routes/screencast.py`
- Modify: `tests/test_dashboard_pairing.py`
- Modify: `tests/test_dashboard_events.py`
- Modify: `tests/test_screencast_ws.py`
- Modify: relevant frontend stream tests when close reason handling changes

- [ ] **Step 1: Write established-stream expiry RED tests**

Open SSE, tail, and screencast with a valid bearer under a fake monotonic clock.
Advance beyond the bearer TTL while the connection remains open. Assert SSE
ends and both WebSockets close with `1008`. Add an LRU-eviction variant for one
established stream.

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/test_dashboard_pairing.py tests/test_dashboard_events.py tests/test_screencast_ws.py -q --no-cov`

Expected: established transports remain open after `bearer_ok()` becomes false.

- [ ] **Step 3: Create and attach a digest-based stream lease**

Have the shared guard attach a lease to `connection.state`. Pairing-disabled and
capability-token requests receive a bypass lease; bearer requests store only the
validated digest and app-local state. Preserve the existing public boolean and
WebSocket-protocol return contracts.

- [ ] **Step 4: Revalidate on bounded stream cadence**

SSE checks before every event/heartbeat. Tail and screencast pass the lease into
their existing poll/receive race and close with `1008` as soon as invalidation is
observed. Frontend close handlers dispatch the existing auth-required event and
stop reconnecting for that close code/reason.

For a same-origin socket rejected by pairing at initial admission, accept the
handshake selecting the stable public protocol if and only if the client
offered it (never select the private bearer protocol), then immediately close
`1008` before any session lookup or data emission. Accept with no subprotocol
when none was offered. This makes Chromium expose the pairing reason instead of
synthetic `1006`; keep Host/Origin rejection pre-accept.

- [ ] **Step 5: Run GREEN**

Run the backend command from Step 2 and the affected Vitest files with
`npm run test:nocov -- --run <files>`.

### Task 6: Preserve native Range video playback with per-client worker auth

**Files:**
- Create: `packages/octowright-frontend/static/dashboard-media-sw.js`
- Create: `packages/octowright-frontend/src/dashboard-media-auth.ts`
- Create: `packages/octowright-frontend/src/dashboard-media-auth.test.ts`
- Modify: `src/octowright/http/routes/media.py`
- Modify: `tests/test_http_server.py`
- Modify: `packages/octowright-frontend/src/dashboard-auth.ts`
- Modify: `packages/octowright-frontend/src/session.ts`
- Modify: `packages/octowright-frontend/src/session.test.ts`
- Modify: `packages/octowright-frontend/src/session-boot.test.ts`
- Modify: `packages/octowright-frontend/vite.config.ts`
- Modify: packaging/static-asset verification tests if the worker is not already copied

- [ ] **Step 1: Write worker and session RED tests**

Test that a client-scoped bearer message causes a video `Request` with
`Range: bytes=...` to be forwarded with both Range and Authorization unchanged.
Test that another client ID has no bearer, clearing auth removes the mapping, and
paired `loadProtectedVideo` assigns the normal `/video` URL without calling
`response.blob()`.

Add route coverage showing pairing-off video retains ordinary caching while a
pairing-protected `206` response is `private, no-store`, varies on
`Authorization` and `X-Octowright-Token`, and retains correct Range bytes. Add
worker coverage showing every forwarded video request bypasses stale caches.

- [ ] **Step 2: Run RED**

Run: `cd packages/octowright-frontend && npm run test:nocov -- --run src/dashboard-media-auth.test.ts src/session.test.ts src/session-boot.test.ts`

Expected: worker coordinator is absent and paired session video still becomes a blob URL.

- [ ] **Step 3: Implement client-scoped worker injection**

The page registers `/dashboard-media-sw.js`, waits for control, and posts only
the current tab's bearer. The worker keys it by `event.source.id`, intercepts
only same-origin `GET /api/sessions/<id>/video`, clones request headers, sets
Authorization, forces same-origin/no-store forwarding, and preserves Range and
credentials semantics with a `new Request(original, {...})` clone.

Keep worker credentials memory-only. A matching request with a missing client
entry notifies only that `Client.id`; the page coordinator re-sends its own
bearer, waits for acknowledgement, and reloads video once. Reauthorize on
`controllerchange` through the same path. If an authenticated fetch returns
`401` or `403`, notify only the originating client, clear dashboard auth, remove
the video source, surface the existing re-pair UX, and do not recover/retry.

- [ ] **Step 4: Wire lifecycle and fail boundedly**

Paired session boot configures worker auth before assigning `video.src`.
Teardown and terminal auth denial post a clear message. If worker control is
unavailable, render an accessible error and do not fall back to an unbounded
blob download.

- [ ] **Step 5: Verify worker output and GREEN**

Run the Vitest command from Step 2.

Run: `cd packages/octowright-frontend && npm run typecheck && npm run lint && npm run build`

Assert `src/octowright/server/frontend/dashboard-media-sw.js` exists and built
session HTML/JS references remain valid.

### Task 7: Bump and synchronize version 0.14.3

**Files:**
- Modify: `tests/test_version_sync.py`
- Modify: `tests/test_upgrade.py`
- Modify: `VERSION`
- Modify: `.antigravity-plugin/plugin.json`
- Modify: `.claude-plugin/plugin.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: `CHANGELOG.md`
- Modify: `src/octowright/upgrade.py`

- [ ] **Step 1: Change the release expectation first and run RED**

Set `RELEASE_VERSION = "0.14.3"` and update the upgrade test expectation.

Run: `uv run pytest tests/test_version_sync.py tests/test_upgrade.py -q --no-cov`

Expected: FAIL because repository metadata remains 0.14.2.

- [ ] **Step 2: Synchronize metadata and notes**

Set the canonical and three plugin versions to 0.14.3. Add a 2026-08-12
changelog section and newest upgrade highlights describing at-most-once safety,
atomic lifecycle/state transactions, secret-safe logging, expiring live streams,
and Range-preserving paired video.

- [ ] **Step 3: Run GREEN**

Run: `uv run pytest tests/test_version_sync.py tests/test_upgrade.py -q --no-cov`

### Task 8: Combined verification, review, and delivery

**Files:**
- Review all changes from `origin/main...HEAD`

- [ ] **Step 1: Run focused regression suites**

Run every focused command from Tasks 1–7 and `git diff --check`.

- [ ] **Step 2: Run full local verification**

Run: `make ci`

Run: `cd packages/octowright-frontend && npm run build && npm run test`

Run: `uv build`

- [ ] **Step 3: Obtain independent spec and code-quality review**

Review the complete diff for all eight findings, cross-platform lock behavior,
credential exposure, worker tab isolation, test quality, and unintended scope.
Resolve every actionable issue and rerun affected tests.

- [ ] **Step 4: Push, open PR, and wait for exact-head CI**

Push `fix/comprehensive-review-remediation`, open a non-draft PR targeting
`main`, and wait for all required checks. Fix branch-caused failures only after
reproducing the failing command or platform condition.

- [ ] **Step 5: Merge and synchronize**

Merge with the repository's merge strategy, fetch `origin/main`, fast-forward
the local `main` without touching unrelated untracked files, verify `VERSION`
is 0.14.3 and the PR state is `MERGED`, then remove the remediation worktree.
