# Review Batch A Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Make `fix/review-batch-a-correctness` race-safe, cross-platform, observable, and green on every CI platform before merging it into `main`.

**Architecture:** Preserve the branch's existing correctness work, but close the remaining race windows at their ownership boundaries. A keyed profile lifecycle lock will serialize persistent-profile launch and deletion; platform-specific bridge-state locking will serialize cross-process updates; bounded discovery caches and explicit dashboard state will keep failure paths cheap and visible. Terminal teardown becomes best-effort per session and records a stable terminal stop state even when the recorder itself fails.

**Tech Stack:** Python 3.11+, asyncio, Starlette, Playwright, pytest, TypeScript, Vitest, Vite, GitHub Actions.

---

### Task 1: Serialize persistent profile launch and deletion

**Files:**
- Create: `src/octowright/profile_lifecycle.py`
- Modify: `src/octowright/browser_pool/pool.py`
- Modify: `src/octowright/server/personas.py`
- Create: `tests/test_profile_lifecycle.py`
- Modify: `tests/test_personas_branches.py`

- [ ] **Step 1: Write the failing keyed-lock tests**

Add async tests proving that two operations for the same `(kind, profile)` cannot overlap, operations for different keys can overlap, and cancelled waiters do not retain a lock. Use events instead of sleeps to make the ordering deterministic.

- [ ] **Step 2: Write the failing launch/delete race tests**

Patch `_open_browser_context` so a persistent launch pauses after taking ownership of the profile path. Start `profile_delete` concurrently and assert deletion cannot enter `delete_profile` until launch either registers the session or fails and releases the lock. Add the analogous persona-wide test: `persona_delete` must acquire every engine-profile key in sorted order and re-check `pool.list_sessions()` while locked.

- [ ] **Step 3: Run the focused tests and capture the RED result**

Run: `uv run pytest tests/test_profile_lifecycle.py tests/test_personas_branches.py -q`

Expected: the new concurrency assertions fail because launch and deletion currently use no common exclusion primitive.

- [ ] **Step 4: Implement the keyed lifecycle lock**

Create a small process-local registry keyed by normalized `(kind, name)`. Use an `asyncio.Lock` per key and expose async context managers for one key and a sorted set of keys. Keep registry bookkeeping under a short synchronous guard and remove idle entries so arbitrary persona names do not grow the registry forever.

- [ ] **Step 5: Hold the lock across the complete profile ownership transition**

In `BrowserPool._launch_impl`, validate the target and base URLs before allocating a session directory, starting Playwright, or creating a recording. For persistent launches, acquire the profile key before `_open_browser_context` first touches the profile directory and hold it through `post_context_setup`, which registers the live session. Always release it on cancellation and failure.

Convert `profile_delete` and `persona_delete` to async MCP tools. Acquire the same key(s), then perform the in-use check and filesystem deletion while still holding the lock. For persona deletion, discover the relevant engine directories, sort keys, acquire them in that stable order, and re-check every live session before deleting.

- [ ] **Step 6: Run focused tests and capture the GREEN result**

Run: `uv run pytest tests/test_profile_lifecycle.py tests/test_personas_branches.py tests/test_pool_disconnect.py -q`

Expected: all pass, including cancellation cleanup and the deterministic launch/delete interleavings.

- [ ] **Step 7: Commit the profile lifecycle fix**

Run: `git add src/octowright/profile_lifecycle.py src/octowright/browser_pool/pool.py src/octowright/server/personas.py tests/test_profile_lifecycle.py tests/test_personas_branches.py && git commit -m "fix(profiles): serialize launch and deletion"`

### Task 2: Reject unsafe launch inputs before allocation

**Files:**
- Modify: `src/octowright/browser_pool/pool.py`
- Modify: `tests/test_pool_disconnect.py`

- [ ] **Step 1: Write the failing allocation-order test**

Patch `_resolve_session_dir`, `_ensure_pw`, and `new_log_path` with call trackers. Launch an unsafe absolute URL and assert all three trackers remain empty. Add the same assertion for an unsafe explicit `base_url`.

- [ ] **Step 2: Run the focused test and capture RED**

Run: `uv run pytest tests/test_pool_disconnect.py -k "unsafe and allocation" -q`

Expected: `_resolve_session_dir` and `_ensure_pw` are called before URL validation.

- [ ] **Step 3: Move pure validation to the top of `_launch_impl`**

Resolve `target_url`, call `_reject_unsafe_url(target_url)`, and validate the effective explicit/persona `base_url` before generating an instance ID or touching session/profile/recording state. Pass the already validated effective base URL into context creation so it is not recomputed after allocations begin.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/test_pool_disconnect.py -k "unsafe or launch" -q`

Run: `git add src/octowright/browser_pool/pool.py tests/test_pool_disconnect.py && git commit -m "fix(pool): validate launch targets before allocation"`

### Task 3: Make bridge-state transactions cross-platform

**Files:**
- Modify: `src/octowright/bridge_state.py`
- Modify: `tests/test_bridge_state.py`

- [ ] **Step 1: Write failing lock contention tests**

Add a subprocess worker test that repeatedly registers unique followers into one state file. Assert the final JSON contains every registration on POSIX and Windows. Add a unit test that patches `sys.platform` and a fake `msvcrt` module to assert the Windows path locks one byte and unlocks it in `finally`.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/test_bridge_state.py -q`

Expected on the emulated Windows path: state updates can overlap because `_state_lock` yields without locking.

- [ ] **Step 3: Implement portable locking**

Retain `fcntl.flock(LOCK_EX)` on POSIX. On Windows, open the `.lock` sibling in binary append/update mode, ensure it contains one byte, seek to byte zero, and retry `msvcrt.locking(..., LK_LOCK, 1)` only for documented contention errors. Always seek back and call `LK_UNLCK` before closing. Add a short process-local keyed `threading.Lock` around the OS lock so threads in one process are serialized as well.

If the lock file cannot be opened, log a warning with the path and exception instead of silently degrading the read-modify-write transaction.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest tests/test_bridge_state.py -q`

Run: `git add src/octowright/bridge_state.py tests/test_bridge_state.py && git commit -m "fix(bridge): lock state updates on Windows"`

### Task 4: Restore test isolation and deterministic text decoding

**Files:**
- Modify: `tests/test_mcp_events_daemon_live.py`
- Modify: `tests/test_replay_passive_covers_recorder.py`

- [ ] **Step 1: Reproduce the contamination**

Run the daemon-live module immediately before a proxy-runtime module in one pytest process and record the failure caused by direct assignment to `resolve_leader_url` and `resolve_leader_token`.

- [ ] **Step 2: Replace direct global assignments with `monkeypatch.setattr`**

Use pytest's `monkeypatch` fixture for both functions so teardown restores production behavior. Preserve the deterministic fake values used by the live daemon test.

- [ ] **Step 3: Make source scanning explicitly UTF-8**

Change every `Path.read_text()` in the recorder call-site scan to `read_text(encoding="utf-8")` so Windows does not decode source with the active ANSI code page.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_mcp_events_daemon_live.py tests/test_replay_passive_covers_recorder.py tests/test_proxy_runtime.py -q`

Run: `git add tests/test_mcp_events_daemon_live.py tests/test_replay_passive_covers_recorder.py && git commit -m "test: restore daemon patches and decode sources as UTF-8"`

### Task 5: Bound closed-session discovery misses

**Files:**
- Modify: `src/octowright/http/discovery.py`
- Modify: `tests/test_http_discovery.py`

- [ ] **Step 1: Write failing overflow-cache tests**

Fill the primary discovery index to its configured limit, then request the same missing ID three times while counting directory scans. Assert only the first miss scans. Add a test where a file appears after a negative result and a recordings-directory mtime change invalidates the negative entry. Add an LRU bound assertion for distinct misses.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/test_http_discovery.py -k "overflow or negative or saturated" -q`

Expected: every saturated miss performs a full sorted glob.

- [ ] **Step 3: Add a generation-scoped bounded overflow cache**

Store hit and miss entries in an `OrderedDict` keyed by session ID. Associate them with the current recordings-directory mtime/generation, move accessed entries to the end, and evict the oldest above the configured bound. Clear this overflow cache whenever the primary index rebuilds or directory generation changes.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_http_discovery.py -q`

Run: `git add src/octowright/http/discovery.py tests/test_http_discovery.py && git commit -m "fix(discovery): cache saturated session misses"`

### Task 6: Surface degraded dashboard data

**Files:**
- Modify: `packages/octowright-frontend/src/dashboard.ts`
- Modify: `packages/octowright-frontend/src/dashboard-state.ts`
- Modify: `packages/octowright-frontend/src/dashboard.test.ts`
- Modify: `packages/octowright-frontend/src/dashboard-state.test.ts`
- Modify: `packages/octowright-frontend/src/styles.css`

- [ ] **Step 1: Write failing rendering tests**

Construct state where one endpoint fails while other dashboard data succeeds. Assert the dashboard retains successful panels and renders an accessible `role="status"` degraded-data notice naming the failed data source. Assert the notice clears after the next successful refresh.

- [ ] **Step 2: Run and capture RED**

Run: `cd packages/octowright-frontend && npm test -- --run src/dashboard.test.ts src/dashboard-state.test.ts`

- [ ] **Step 3: Render errors without discarding partial data**

Normalize endpoint failures into stable source labels in dashboard state. Render one compact notice above the panels with a retry-safe text summary; do not replace healthy panel content. Add restrained warning styling consistent with the existing dashboard palette.

- [ ] **Step 4: Verify and commit**

Run: `cd packages/octowright-frontend && npm test -- --run src/dashboard.test.ts src/dashboard-state.test.ts`

Run: `git add packages/octowright-frontend/src/dashboard.ts packages/octowright-frontend/src/dashboard-state.ts packages/octowright-frontend/src/dashboard.test.ts packages/octowright-frontend/src/dashboard-state.test.ts packages/octowright-frontend/src/styles.css && git commit -m "fix(dashboard): expose partial refresh failures"`

### Task 7: Make terminal failure and teardown paths stable

**Files:**
- Modify: `src/octowright/terminal/engine.py`
- Modify: `src/octowright/terminal/pool.py`
- Modify: `tests/terminal/test_engine.py`
- Modify: `tests/test_terminal_supervision.py`

- [ ] **Step 1: Write failing recorder and multi-close tests**

Use a recorder whose `record("terminal_stop", ...)` raises. Assert poll-task completion still stores the original poll exception, marks the engine stopped, and does not raise from the task callback. Put three sessions in `TerminalPool`, make the first and third close fail, and assert all three closes are attempted before an aggregated error is raised.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/terminal/test_engine.py tests/test_terminal_supervision.py -q`

- [ ] **Step 3: Separate state transition from best-effort recording**

Have engine stop logic set the terminal status and preserved failure exactly once, then attempt the stop record under a guarded helper that logs recorder failures. Ensure `_on_poll_done` consumes task exceptions and cannot raise into the event loop callback.

Change `TerminalPool.close_all` to attempt every session, collect `(instance_id, exception)` failures, and raise one deterministic summary after cleanup. Preserve protection semantics and successful removals.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/terminal/test_engine.py tests/test_terminal_supervision.py -q`

Run: `git add src/octowright/terminal/engine.py src/octowright/terminal/pool.py tests/terminal/test_engine.py tests/test_terminal_supervision.py && git commit -m "fix(terminal): harden failure recording and close-all"`

### Task 8: Repair the frontend build contract

**Files:**
- Modify: `packages/octowright-frontend/package.json`
- Modify: `package-lock.json`
- Modify: `packages/octowright-frontend/vite.config.ts`
- Modify: `tests/test_frontend_build.py`

- [ ] **Step 1: Add a failing wheel-asset assertion**

Extend the build test to assert Vite emits the exact stable asset names consumed by the Python wheel/static verifier, including `styles.css`.

- [ ] **Step 2: Add Vite as a direct development dependency and stabilize CSS output**

Declare the pinned Vite major directly in the frontend package instead of relying on a transitive executable. Configure `assetFileNames` so the single stylesheet is emitted as `styles.css` while preserving hashed names for other assets that are not part of the server contract. Regenerate the lockfile with `npm install --package-lock-only`.

- [ ] **Step 3: Verify and commit**

Run: `cd packages/octowright-frontend && npm ci && npm run build && npm test -- --run`

Run: `uv run pytest tests/test_frontend_build.py -q`

Run: `git add packages/octowright-frontend/package.json packages/octowright-frontend/vite.config.ts package-lock.json tests/test_frontend_build.py && git commit -m "fix(frontend): make Vite build outputs explicit"`

### Task 9: Validate and merge PR 100

**Files:**
- Review only: all files changed from `origin/main`

- [ ] **Step 1: Run branch verification**

Run: `make format`

Run: `make ci`

Run: `cd packages/octowright-frontend && npm run build && npm run test`

Run: `git diff --check origin/main...HEAD`

- [ ] **Step 2: Push and inspect PR checks**

Run: `git push origin fix/review-batch-a-correctness`

Run: `gh pr checks 100 --watch --interval 10`

Inspect every failing check log with `gh run view --log-failed` before changing code. Repeat focused RED/GREEN fixes for branch-caused failures.

- [ ] **Step 3: Merge without deleting the stacked base branch**

Run: `gh pr merge 100 --merge`

Keep `fix/review-batch-a-correctness` on the remote until PR 101 is retargeted from it to `main`.
