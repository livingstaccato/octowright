# Comprehensive Code Review and Architecture Report

Date: 2026-08-12
Review baseline: `origin/main` at `ecae9bf`
Reviewed integrations: PR #100 (`fix/review-batch-a-correctness`), PR #101
(`feat/dashboard-pairing`), and PR #107 (`fix/manifest-orphan-prune`)

## Executive result

The review found eight release-blocking correctness, security, concurrency, and
performance issues in the integrated code. Adversarial review of the first
remediation pass found additional edge cases in the same ownership
boundaries. All are resolved in the 0.14.3 remediation, with deterministic
regressions for the reproduced interleavings and failure modes.

No unresolved critical, high, or medium finding remains after the final backend
and dashboard re-reviews. The remaining tradeoffs are deliberate fail-safe
choices and are documented below.

## Scope and method

The review followed data and ownership across the main architectural seams:

- MCP mutation dispatch, retry, and idempotency ownership.
- Browser registry lifecycle, synchronous Playwright callbacks, async teardown,
  driver recovery, and keep-id rebinding.
- Cross-process bridge snapshots and launch-manifest transactions on POSIX and
  Windows.
- Boot process reaping and diagnostic-manifest cleanup.
- Credential-helper failures and daemon-log filesystem permissions.
- Dashboard pairing admission, bearer TTL/LRU behavior, SSE and WebSocket
  lifetimes, per-tab isolation, and reconnect behavior.
- Protected media delivery, native HTTP Range semantics, cancellation, teardown,
  and per-client credential isolation.

Review techniques included diff inspection, call-graph tracing, deterministic
clock seams, forced lock contention, same-process and cross-process
interleavings, late-callback simulation, cancellation-resistant and
cancellation-accepting producers, established-stream expiry, and frontend
service-worker/client simulations.

## Findings and resolutions

### 1. Aged idempotency producers could execute a mutation twice — high

The cache treated age as proof that an in-progress producer was gone and
deleted its slot. A same-key resume could then become a second producer while
the first task was still running.

The initial remediation tracked the producer task and requested cancellation,
but adversarial review proved that cancellation was also unsafe: a producer may
commit its side effect, accept cancellation during a later await, and let the
waiting resend immediately claim a fresh slot.

Resolution:

- Every real entry records its producer task.
- Age never cancels or reclaims a live producer.
- Request/session cancellation cancels only its waiter; a shielded producer
  continues and records its result for the reconnect.
- A producer that itself fails or is cancelled leaves an unknown-outcome
  tombstone instead of permitting an automatic same-key rerun.
- Same-key waiters continue to receive an explicit unknown-outcome error.
- Only a taskless synthetic orphan or a confirmed-terminated task can be
  reclaimed.
- A fresh distinct key is refused with `IdempotencyCapacityError` before its
  handler runs when every bounded slot remains authoritative; existing-key
  callers still await or reuse their original slot.
- An oversize successful result leaves an authoritative terminal marker; a
  resend raises `IdempotencyResultUnavailableError` instead of executing the
  tool again.
- Synchronous MCP mutators preserve the SDK's worker-thread execution inside an
  authoritative task-backed producer and deduplicate under the same cache
  contract as async tools without blocking the event loop.
- Storage keys canonicalize wire arguments while excluding only the
  type-identified SDK-injected MCP `Context`; a reconnect's fresh context object
  therefore cannot bypass the original slot.

Safety tradeoff: a producer that never terminates retains one authoritative
slot. At full capacity, the cache rejects new distinct idempotent calls instead
of growing without bound or risking duplicate mutation execution by evicting a
live producer.

### 2. Deferred and late browser close paths could target a replacement — high

A deferred last-page close checked session identity before awaiting the pool
close operation. Keep-id driver recovery could rebind the id during that gap,
causing the stale close to pop and force-close the replacement.

The first fix made expected-session validation and pop atomic under
`_sessions_lock`. Follow-up review found two additional identity edges:

- A late synchronous callback from the old session could still pop the new
  session by id.
- The replacement's own listener captured its temporary pre-rekey id, so its
  later close could leave a dead session registered under the preserved id.

Resolution:

- Pool close accepts an expected session and validates, checks protection, and
  pops in one locked transaction.
- Synchronous eviction accepts the expected object and refuses identity
  mismatches.
- Listener callbacks resolve the owning session's current id at callback time.
- Keep-id rekey uses the same registry lock.
- Driver-reset eviction carries both id and expected object.
- The replacement's launch-manifest row is atomically moved from its temporary
  id to the preserved client-facing id, replacing the stale predecessor row.

### 3. Credential-helper stderr could persist secrets — high/security

Credential helpers are allowed to echo tokens or secret-bearing error payloads
to stderr. The failure path suppressed stderr in the MCP exception but copied a
raw excerpt into DEBUG telemetry, which the daemon log persisted.

Resolution:

- Failure telemetry retains only persona, field, return code, and stderr
  length.
- Raw helper stderr is never logged.
- Daemon logs are opened with mode `0600` and legacy permissions are repaired
  through the open descriptor when supported, with a path fallback.
- Unsupported chmod behavior no longer prevents daemon startup.

### 4. Bridge-state lock timeout reopened the lost-update race — high

Bridge-state writers waited boundedly for the cross-process lock, but entered
the read-modify-replace transaction without ownership after timeout or lock-file
failure. A frozen old writer could later replace the file from a stale read and
erase newer follower registrations.

Resolution:

- Lock acquisition yields explicit ownership.
- Snapshot and follower-removal transactions are skipped unless the lock is
  owned.
- Timeout, open failure, and lock failure all leave the prior state untouched.
- The same-process thread lock is bounded too, and shares one total deadline
  with the OS lock.
- Later heartbeats and housekeeping passes retry naturally.

### 5. Launch-manifest transactions lost concurrent updates — medium

`record_launch`, `remove_session`, and dead-daemon pruning performed unlocked
read-modify-replace operations. Concurrent `--no-singleton` leaders or a
split-election window could lose a fresh launch or resurrect a closed entry.

Resolution:

- Every manifest writer uses a stable sibling lock across processes plus a
  same-process thread lock.
- POSIX uses `flock`; Windows locks byte zero with `msvcrt.locking`.
- A bounded acquisition failure raises before entering the transaction.
- Temp filenames include PID plus a process-local sequence, preventing
  concurrent collision.
- Keep-id manifest rekey is part of the same transaction discipline.
- Async leader call sites offload lock polling and filesystem I/O from the
  event loop, with repeated asyncio cancellation and persistent AnyIO
  cancellation ordered after the transaction completes.
- Permanent lock errors fail immediately, while only actual contention polls;
  ref-counted per-path thread locks are removed when idle.
- Reads remain lock-free because atomic replacement always exposes a complete
  old or new document.

### 6. Boot cleanup coupled independent failure domains — medium

An exception while probing or reaping orphan browser processes returned before
dead-daemon manifest pruning ran. Conversely, pruning after a reaper reported a
browser still alive would delete the only session-level diagnostic for that
survivor.

Resolution:

- A thrown process-reaper failure is logged and manifest pruning still runs.
- Error-only/unknown summaries do not suppress conservative PID-based pruning.
- A browser explicitly confirmed still alive preserves the manifest diagnostic.
- Partial outcomes are logged without claiming browsers are "provably gone."

Because the manifest records daemon PID rather than browser PID, a confirmed
survivor conservatively defers that boot's whole manifest prune; there is no
sound way to map and retain only its row.

### 7. Established paired streams outlived bearer expiry or eviction — high

Pairing was checked only during HTTP or WebSocket admission. An already-open
dashboard SSE, JSONL tail, or screencast connection could continue receiving
events, typed-input recordings, or frames after its bearer expired or was
evicted from the bounded LRU.

Resolution:

- Successful admission attaches a digest-only stream lease to connection
  state; the raw bearer is not retained.
- Pairing-disabled and capability-token connections receive an explicit bypass
  lease.
- SSE revalidates before events and heartbeats and then ends.
- Tail and screencast WebSockets revalidate at a bounded cadence and close with
  code `1008` and a stable pairing-expired reason.
- A same-origin WebSocket whose bearer is invalid during initial admission is
  accepted selecting only the offered stable public protocol (never the private
  bearer protocol), then immediately closed `1008` before session lookup or
  data transfer. Chromium otherwise reports a
  pre-accept close as `1006` with no reason, hiding the re-pair signal. Host and
  cross-origin failures remain pre-accept rejections.
- Frontend stream handlers clear local auth, dispatch the existing re-pair
  event, stop reconnect/fallback loops, and surface a concise page-level
  re-pair state for terminal and browser debugger views, including boot-time
  denial and screenshot fallback expiry.
- Tests cover TTL expiry and LRU eviction after a stream is established.

### 8. Paired video defeated Range and progressive playback — medium

The frontend fetched the entire protected recording into a Blob before setting
the video source. Multi-hour recordings could delay first frame until the full
download completed, consume recording-sized memory, and discard native Range
seeking semantics.

Resolution:

- A module service worker handles only same-origin session-video GET requests.
- Each page sends its bearer to the worker, which stores it by the sending
  `Client.id`; another or duplicated client cannot inherit it.
- The worker adds `Authorization` while preserving the original request and
  `Range` header, so the existing server `FileResponse` continues native `206`
  delivery.
- Every pairing-protected guarded response is `private, no-store` and varies on
  both authorization headers, including video `200`/`206`, frames, traces,
  screenshots, and JSON. Protected blob fetches and worker forwards also use
  `cache: no-store`. Pairing-off server responses retain prior cache behavior;
  a persistent media worker conservatively keeps bypassing video cache across a
  later pairing-mode transition.
- Configuration waits for a worker acknowledgement before assigning the video
  source, preventing the first Range request from racing credential setup.
- Worker bearer state remains memory-only. Missing state after worker
  termination, restart, or replacement is reported only to the originating
  client; that page re-sends its own current bearer, waits for acknowledgement,
  and reloads native playback once. `controllerchange` uses the same recovery;
  a bounded acknowledgement failure clears auth and surfaces re-pair guidance
  instead of stranding playback.
- Authenticated video `401`/`403` responses are reported only to the requesting
  client. That page clears auth, removes the source, emits the existing re-pair
  event, renders re-pair guidance, and does not enter a recovery loop.
- Teardown and auth clearing remove that client's worker credential.
- Worker registration, controller acquisition, and bearer acknowledgement are
  timeout- and abort-bounded. If service workers cannot take control, paired
  video fails with an accessible message rather than buffering the full file.

## Previously reported pairing hardening revalidated

The merged pairing remediation already addressed four earlier concerns and the
comprehensive pass revalidated them:

- Browser-tab duplication is rejected through an exclusive Web Lock keyed by
  the stored tab id; lack of Web Locks fails closed for inherited credentials.
- Protected-video setup is cancellable and does not block the rest of debugger
  boot.
- Paired screenshot loading is viewport-driven, bounded to three concurrent
  requests, and revokes Blob URLs on rerender/unload.
- Missing, locally expired, or server-rejected auth dispatches the re-pair event
  and stops protected polling/reconnect loops.
- The private redirect file used by `octowright dashboard --open` is removed
  after a short browser-read grace period.

## Architecture after remediation

### Pairing and streaming authorization

```text
fragment code -> one-time redemption -> per-tab sessionStorage bearer
                                      -> exclusive Web Lock

HTTP/SSE/WS admission -> bearer digest lease -> periodic server revalidation
                                         |-> expiry/LRU: end SSE or WS 1008
                                         `-> frontend auth-required state
```

### Protected video path

```text
dashboard Client.id --bearer + acknowledgement--> module service worker
video element --native Range request-----------> service worker
service worker --Range + Authorization,
                cache bypass-------------------> guarded FileResponse
server --private/no-store when paired----------> 206 partial response
worker state loss --client-scoped request------> reauthorize + reload once
server 401/403 --client-scoped denial----------> clear auth + re-pair state
```

### Concurrency ownership

```text
mutation key -> one authoritative producer task (never age-cancelled)
browser id   -> _sessions_lock + expected object identity
manifest     -> thread lock + stable cross-process sibling lock
bridge state -> bounded thread/OS locks; no ownership means no write
```

## Release verification plan

The remediation uses RED-to-GREEN regressions for every finding. Before merge,
the release candidate must pass:

- Focused backend concurrency/security/lifecycle suites.
- Full Python test suite with coverage.
- Ruff formatting and lint, mypy, ty, Bandit, codespell, SPDX, LOC, vulture,
  xenon, and detect-secrets gates.
- Full frontend Vitest suite with coverage, TypeScript typecheck, Biome lint,
  production Vite build, and frontend audit.
- Version/plugin/changelog synchronization for 0.14.3.
- A final independent code review of the complete diff.
- GitHub Actions on the pushed integration branch before merge.

Exact final command results and CI links will be recorded in the pull request
and merge handoff after those gates complete.

## Residual, accepted tradeoffs

- Dashboard pairing remains opt-in and the same-user 0600 lockfile remains the
  trust boundary; a same-user process can mint its own code.
- A permanently stuck mutation producer can pin one bounded idempotency slot.
  If all slots are authoritative, new distinct calls fail before execution;
  automatic reclamation would risk duplicate side effects.
- Bridge snapshots and manifest writes can be skipped/fail after their bounded
  lock timeout. They never proceed unlocked; later normal activity retries.
- Paired progressive video requires service-worker support. The failure mode is
  explicit and bounded.
- Once installed, the origin-scoped media worker persists. Its no-credential
  video path keeps bypassing browser cache even if a later daemon disables
  pairing; this conservative performance tradeoff prevents stale authenticated
  bytes from crossing configuration transitions.
- A confirmed surviving orphan browser defers manifest pruning for that boot so
  its only session-level diagnostic is preserved.
