# Dashboard Pairing Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan.

**Goal:** Replace PR 101's cross-port cookie bootstrap with an origin-scoped bearer session that protects HTTP, SSE, WebSocket, and media traffic without leaking the leader token.

**Architecture:** A one-time pairing code is redeemed through a body-capped endpoint for a short-lived, random dashboard bearer. The frontend stores that bearer in `sessionStorage` for the current origin and attaches it to every protected transport: Authorization headers for HTTP/fetch-SSE/media and a private WebSocket subprotocol for tail/screencast. Server state owns bounded true-LRU maps for pair codes and dashboard sessions; all route guards share one credential parser and constant-time validation path.

**Tech Stack:** Starlette/ASGI, Python secrets and hmac, Click, TypeScript, Fetch streams, WebSocket subprotocols, Vitest, pytest.

---

### Task 1: Rebase the stacked change without rewriting public history

**Files:**
- Branch metadata only

- [ ] **Step 1: Create a feature worktree after PR 100 merges**

Run: `git fetch origin`

Run: `git worktree add .worktrees/dashboard-pairing -b feat/dashboard-pairing --track origin/feat/dashboard-pairing`

- [ ] **Step 2: Merge updated main and retarget PR 101**

Run: `git merge --no-edit origin/main`

Run: `git push origin feat/dashboard-pairing`

Run: `gh pr edit 101 --base main`

This avoids a force push while collapsing the already-merged PR 100 commits out of PR 101's effective diff.

### Task 2: Define the bearer session model and true LRU behavior

**Files:**
- Modify: `src/octowright/http/pairing.py`
- Modify: `tests/test_dashboard_pairing.py`

- [ ] **Step 1: Write failing state-machine tests**

Cover one-time code redemption, expiry under a monotonic fake clock, code non-reuse, random bearer creation, bearer expiry, touch-on-success LRU ordering, capacity eviction of the least-recently-used entry, and no recency mutation for invalid credentials. Assert bearer/code values never appear in `repr`, metrics labels, or log capture.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/test_dashboard_pairing.py -q`

- [ ] **Step 3: Implement app-local bounded stores**

Use `OrderedDict` maps owned by a `DashboardPairingState` instance attached to `app.state`. Store digests of codes and bearer values, not the raw credentials. On successful bearer validation, `move_to_end`; on insert, expire old entries first, then `popitem(last=False)` until within the cap. Use `hmac.compare_digest` for credential comparisons and a monotonic clock injected in tests.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_dashboard_pairing.py -q`

Run: `git add src/octowright/http/pairing.py tests/test_dashboard_pairing.py && git commit -m "fix(pairing): issue bounded dashboard bearer sessions"`

### Task 3: Cap redemption before parsing and return JSON bearer data

**Files:**
- Modify: `src/octowright/http/routes/pairing.py`
- Modify: `src/octowright/http/app.py`
- Modify: `tests/test_dashboard_pairing.py`
- Modify: `tests/test_request_body_cap.py`

- [ ] **Step 1: Write failing oversized-body and response-contract tests**

Stream a redemption request larger than the route limit without a `Content-Length` and assert `413` before JSON/form parsing. Test misleading small `Content-Length`, malformed JSON, missing code, expired/used code, and valid redemption. The success response must be JSON `{ "bearer": <opaque>, "expires_at": <unix-seconds> }`, set `Cache-Control: no-store`, and set no cookies.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/test_dashboard_pairing.py tests/test_request_body_cap.py -q`

- [ ] **Step 3: Put body capping in the ASGI receive path**

Apply the existing request-body cap middleware/route wrapper to pairing redemption before Starlette buffers the body. Keep the cap large enough for the compact JSON contract and return a consistent JSON error. Redeem through the app-local state and emit only the bearer response described above.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_dashboard_pairing.py tests/test_request_body_cap.py -q`

Run: `git add src/octowright/http/routes/pairing.py src/octowright/http/app.py tests/test_dashboard_pairing.py tests/test_request_body_cap.py && git commit -m "fix(pairing): cap redemption and return bearer JSON"`

### Task 4: Authenticate HTTP, SSE, WebSocket, and media uniformly

**Files:**
- Modify: `src/octowright/http/exposure.py`
- Modify: `src/octowright/http/routes/events.py`
- Modify: `src/octowright/http/routes/screencast.py`
- Modify: `src/octowright/http/routes/registry.py`
- Modify: `tests/test_dashboard_pairing.py`
- Modify: `tests/test_http_exposure.py`

- [ ] **Step 1: Write a route/transport authentication matrix**

Parametrize protected HTTP JSON and media endpoints with missing, malformed, expired, and valid `Authorization: Bearer ...` values. Test dashboard SSE the same way. For tail and screencast WebSockets, test missing/wrong/private subprotocol values, success with `octowright.dashboard.bearer.<base64url-token>`, and that the server accepts/responds with only the stable `octowright.dashboard` protocol rather than echoing the secret-bearing protocol.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/test_dashboard_pairing.py tests/test_http_exposure.py -q`

- [ ] **Step 3: Implement shared credential extraction**

For HTTP requests, parse exactly one case-insensitive Bearer scheme and reject ambiguous/empty values. For WebSockets, scan `Sec-WebSocket-Protocol` entries for the private credential prefix, decode the base64url token, validate it, and negotiate the stable public protocol. Do not support query-string credentials or cookies.

Keep `/`, static bootstrap assets, `/pair`, `/api/health`, and the redemption endpoint available to the local pairing flow; protect dashboard data, event streams, session controls, recordings, screenshots, video, downloads, tail, and screencast endpoints.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_dashboard_pairing.py tests/test_http_exposure.py -q`

Run: `git add src/octowright/http/exposure.py src/octowright/http/routes/events.py src/octowright/http/routes/screencast.py src/octowright/http/routes/registry.py tests/test_dashboard_pairing.py tests/test_http_exposure.py && git commit -m "fix(pairing): authorize every dashboard transport"`

### Task 5: Store and attach the bearer in the frontend

**Files:**
- Create: `packages/octowright-frontend/src/dashboard-auth.ts`
- Create: `packages/octowright-frontend/src/dashboard-auth.test.ts`
- Modify: `packages/octowright-frontend/src/api.ts`
- Modify: `packages/octowright-frontend/src/api.test.ts`
- Modify: `packages/octowright-frontend/src/dashboard.ts`

- [ ] **Step 1: Write failing origin-scoping tests**

Test parsing the bootstrap pairing code, POST redemption, removal of the code from browser history, storage under a versioned `sessionStorage` key, expiry handling, and clearing the bearer after `401`. Assert no credential is written to localStorage, cookies, URLs, DOM text, or logs.

- [ ] **Step 2: Write failing API header tests**

Assert `fetchJson` attaches `Authorization: Bearer <value>` when pairing is enabled, preserves caller headers, and leaves unpaired/default deployments unchanged.

- [ ] **Step 3: Run and capture RED**

Run: `cd packages/octowright-frontend && npm test -- --run src/dashboard-auth.test.ts src/api.test.ts`

- [ ] **Step 4: Implement the auth helper and API integration**

Keep all bearer access behind functions that read/write only current-origin `sessionStorage`. Bootstrap once at dashboard startup, replace the URL with its uncredentialed form, and expose helpers that add HTTP headers or encode the private WebSocket protocol. On an authenticated `401`, clear state and show a concise re-pair prompt without redirect loops.

- [ ] **Step 5: Verify and commit**

Run: `cd packages/octowright-frontend && npm test -- --run src/dashboard-auth.test.ts src/api.test.ts src/dashboard.test.ts`

Run: `git add packages/octowright-frontend/src/dashboard-auth.ts packages/octowright-frontend/src/dashboard-auth.test.ts packages/octowright-frontend/src/api.ts packages/octowright-frontend/src/api.test.ts packages/octowright-frontend/src/dashboard.ts && git commit -m "feat(dashboard): bootstrap origin-scoped bearer auth"`

### Task 6: Replace native SSE and authenticate WebSockets

**Files:**
- Modify: `packages/octowright-frontend/src/dashboard-events.ts`
- Modify: `packages/octowright-frontend/src/dashboard-events.test.ts`
- Modify: `packages/octowright-frontend/src/tail.ts`
- Modify: `packages/octowright-frontend/src/tail.test.ts`
- Modify: `packages/octowright-frontend/src/live-preview-screencast.ts`
- Modify: `packages/octowright-frontend/src/live-preview-screencast.test.ts`

- [ ] **Step 1: Write failing transport tests**

For dashboard events, feed chunked SSE data whose lines and UTF-8 code points cross chunk boundaries; assert headers include the bearer, events parse once, abort closes the stream, and reconnect uses bounded backoff. For tail and screencast, assert their constructors receive both the stable protocol and the credential protocol and never put the bearer in the URL.

- [ ] **Step 2: Run and capture RED**

Run: `cd packages/octowright-frontend && npm test -- --run src/dashboard-events.test.ts src/tail.test.ts src/live-preview-screencast.test.ts`

- [ ] **Step 3: Implement authenticated transports**

Replace `EventSource` with `fetch` plus `ReadableStreamDefaultReader`, a streaming `TextDecoder`, and a minimal SSE line/event accumulator supporting `event`, `data`, and blank-line dispatch. Use an `AbortController` for close and preserve the current invalidation/reconnect contract.

Pass `["octowright.dashboard", credentialProtocol]` to `new WebSocket` for both tail and screencast. Keep injection-compatible constructor types so unit tests and non-browser test environments remain simple.

- [ ] **Step 4: Verify and commit**

Run: `cd packages/octowright-frontend && npm test -- --run src/dashboard-events.test.ts src/tail.test.ts src/live-preview-screencast.test.ts`

Run: `git add packages/octowright-frontend/src/dashboard-events.ts packages/octowright-frontend/src/dashboard-events.test.ts packages/octowright-frontend/src/tail.ts packages/octowright-frontend/src/tail.test.ts packages/octowright-frontend/src/live-preview-screencast.ts packages/octowright-frontend/src/live-preview-screencast.test.ts && git commit -m "fix(dashboard): authenticate streaming transports"`

### Task 7: Fetch protected media into revocable object URLs

**Files:**
- Modify: `packages/octowright-frontend/src/live-preview.ts`
- Modify: `packages/octowright-frontend/src/live-preview.test.ts`
- Modify: `packages/octowright-frontend/src/session.ts`
- Modify: `packages/octowright-frontend/src/session.test.ts`

- [ ] **Step 1: Write failing media lifecycle tests**

Assert screenshot fallback and closed-session video fetch with the bearer header, create a blob URL only after an OK response, revoke the previous object URL on replacement, and revoke it on destroy/navigation. Assert failures preserve the last good frame and expose an accessible error state.

- [ ] **Step 2: Run and capture RED**

Run: `cd packages/octowright-frontend && npm test -- --run src/live-preview.test.ts src/session.test.ts`

- [ ] **Step 3: Implement authenticated blob loading**

Add one helper that fetches protected media with auth, converts it to a blob, and returns a revocable object URL. Use it for screenshot fallback and any direct recording/video source. Thread an `AbortSignal` through view teardown to prevent a late response from reattaching stale media.

- [ ] **Step 4: Verify and commit**

Run: `cd packages/octowright-frontend && npm test -- --run src/live-preview.test.ts src/session.test.ts`

Run: `git add packages/octowright-frontend/src/live-preview.ts packages/octowright-frontend/src/live-preview.test.ts packages/octowright-frontend/src/session.ts packages/octowright-frontend/src/session.test.ts && git commit -m "fix(dashboard): authorize protected media loads"`

### Task 8: Validate CLI host and pairing output

**Files:**
- Modify: `src/octowright/cli/dashboard.py`
- Modify: `tests/test_cli_dashboard.py`

- [ ] **Step 1: Write failing CLI tests**

Cover default loopback host, bracketed IPv6 loopback, invalid host injection containing delimiters/control characters, remote hosts without opt-in, unreachable health checks, and browser-open behavior. Assert CLI output contains the short-lived pairing URL/code but never the bridge token or resulting dashboard bearer.

- [ ] **Step 2: Run and capture RED**

Run: `uv run pytest tests/test_cli_dashboard.py -q`

- [ ] **Step 3: Parse and validate host input**

Use structured host/IP parsing rather than string concatenation. Require a valid hostname or IP literal, reject userinfo/path/query/fragment/control characters, bracket IPv6 for URL construction, and apply the existing remote-dashboard opt-in policy before making the request.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_cli_dashboard.py -q`

Run: `git add src/octowright/cli/dashboard.py tests/test_cli_dashboard.py && git commit -m "fix(cli): validate dashboard pairing targets"`

### Task 9: Validate and merge PR 101

**Files:**
- Review only: all files changed from `origin/main`

- [ ] **Step 1: Run full verification**

Run: `make format`

Run: `make ci`

Run: `cd packages/octowright-frontend && npm run build && npm run test`

Run: `git diff --check origin/main...HEAD`

- [ ] **Step 2: Push and wait for every required check**

Run: `git push origin feat/dashboard-pairing`

Run: `gh pr checks 101 --watch --interval 10`

Inspect branch-caused failures with `gh run view --log-failed`; apply focused tests before fixes.

- [ ] **Step 3: Merge and clean the temporary stacked base**

Run: `gh pr merge 101 --merge`

After GitHub reports PR 101 merged into `main`, delete the no-longer-needed remote `fix/review-batch-a-correctness` branch and remove both feature worktrees.
