# Comprehensive Review Remediation and 0.14.3 Design

## Goal

Resolve all eight findings from the post-0.14.2 review, preserve the safety
properties those features promise, and ship the fixes as version 0.14.3 through
the repository's normal CI-gated pull-request workflow.

## Delivery approach

Three delivery shapes were considered:

1. **One consolidated remediation branch (selected).** The defects are in
   independent modules, but all are already present on `main` after PR #107.
   One branch gives the combined candidate a single full cross-platform signal
   and avoids publishing a partially hardened patch release.
2. **Several subsystem PRs.** This gives smaller reviews, but temporarily leaves
   known security and at-most-once defects on `main` and requires several full
   CI cycles before a release can be cut.
3. **Minimal local guards only.** This is the smallest diff, but it would leave
   the underlying ownership and transaction boundaries ambiguous and would not
   close the reproduced interleavings.

The selected approach fixes each subsystem independently with a failing
regression test, then verifies their integration before the version bump.

## Idempotency producer ownership

An in-progress idempotency entry records the actual producer task. Age alone
never cancels or deletes a live producer: its side effect may already have
committed, so the slot stays authoritative and same-key callers receive the
existing unknown-outcome response. A taskless synthetic orphan or a producer
confirmed done may be reclaimed. The handler runs in a shielded producer task,
so request/session cancellation stops the caller but cannot cancel and evict a
mutation that may already have committed. If the producer itself ends with an
exception or cancellation, the slot becomes an unknown-outcome tombstone rather
than authorizing an automatic resend. Terminal entries retain the normal
TTL/resume horizon. Before a fresh distinct key is admitted, the cache removes
only terminal entries that are safely beyond the bridge resume window. If every
bounded slot is still authoritative, the fresh call fails with an explicit
capacity error before its handler runs; callers for an existing key still await
or reuse that authoritative slot. If a successful result exceeds the configured
cache-size limit, its terminal marker remains authoritative and a resend raises
`IdempotencyResultUnavailableError` instead of re-executing the tool.
Synchronous mutators preserve the MCP SDK's worker-thread execution inside an
authoritative async producer task and use the same slots as native async tools
without blocking the event loop.
Storage keys bind canonical wire arguments and exclude only parameters whose
resolved annotation is the SDK-injected MCP `Context`, so reconnect-local
object identity cannot split one logical call into two producers.

## Atomic browser identity changes

Closing a browser by expected identity becomes one pool operation. The operation
acquires `_sessions_lock`, checks that the registry still contains the expected
session, applies protection rules, and pops that exact object before awaiting
its teardown. A stale deferred-close task returns without affecting a
replacement. Driver `keep-id` rekeying acquires the same lock, so it cannot
interleave between an identity check and pop. Listener callbacks resolve the
session's current id so the replacement can later evict itself, and keep-id
rekeys the replacement's manifest row atomically to the same client-facing id.
If a synchronous close callback removes the replacement during the off-thread
manifest write, the rekey path revalidates object identity, removes the newly
moved manifest row, and reports recovery failure instead of advertising a dead
id.

## Secret-safe credential failures and private daemon logs

Credential-helper failures retain exit code and stderr length only. Raw stderr
is never passed to structured logging, regardless of log level. The detached
daemon log is created and repaired to mode `0600` on platforms that support
POSIX-style permission bits, covering both fresh files and legacy permissive
files. Permission hardening remains best-effort on platforms where chmod cannot
express the same semantics; failure to chmod must not prevent daemon startup.

## Serialized state-file transactions

Bridge-state and session-manifest writers never enter a read-modify-replace body
without owning the corresponding stable sibling lock.

- Bridge-state acquisition stays bounded to protect event-loop responsiveness.
  Timeout/open/lock failure skips the snapshot or removal and logs the reason;
  it never proceeds unlocked.
- Session-manifest `record_launch`, `remove_session`, and dead-daemon pruning
  share one same-process and cross-process lock for the complete transaction.
  Lock failure raises to the existing best-effort caller boundary, which logs
  and leaves the previous manifest intact.
- Async leader call sites run those synchronous filesystem transactions in
  worker threads, while cancellation waits for an in-flight transaction before
  cleanup proceeds. Permanent non-contention lock errors fail immediately;
  only real contention is polled to the deadline. Per-path thread-lock entries
  are reference-counted and removed after their final user exits.
- POSIX uses `flock`; Windows uses `msvcrt` byte-range locking. Temporary files
  remain unique and are replaced only while the transaction lock is held.

No lock timeout authorizes a stale write. Deterministic tests exercise a
contended writer and prove a concurrent live registration is not erased.

## Dashboard streaming authorization leases

The shared pairing guard creates a stream lease when it admits dashboard SSE or
a dashboard WebSocket. The lease contains no reusable URL credential: it keeps
the app-local pairing state plus the bearer digest, or records that the request
used an allowed non-expiring path (pairing disabled or the leader capability
token).

SSE revalidates before emitting an event or heartbeat. Tail and screencast
WebSockets revalidate on their existing bounded polling/heartbeat cadence. An
expired or LRU-evicted bearer ends SSE and closes a WebSocket with code `1008`.
The frontend treats that closure as an authentication-required state and stops
automatic protected retries until the tab is paired again.

For a same-origin WebSocket whose bearer is already invalid at admission, the
server accepts the handshake selecting only the offered stable public protocol,
never the private credential protocol, and immediately closes with `1008`
before session lookup or data transfer. This
is intentionally different from Host/Origin rejection: Chromium masks a
pre-accept close as synthetic `1006` with no reason, while the accept-then-close
sequence preserves the actionable pairing reason. Host and cross-origin
failures remain rejected before acceptance.

## Range-preserving paired video

Paired video uses a dedicated same-origin service worker instead of converting
the complete response to a blob. Each controlled dashboard client sends its
current bearer to the worker through `postMessage`; the worker stores it by
`Client.id`. For video requests only, the fetch handler clones the request,
preserves headers including `Range`, adds `Authorization`, bypasses pre-existing
browser cache entries, and forwards it to the existing guarded `FileResponse`
endpoint. When pairing is enabled, every successful guarded response (including
video, frame, trace, screenshot, and JSON) is marked `private, no-store` and
varies on both supported authorization headers. Blob fetch helpers also bypass
the cache. Pairing-off responses keep their existing server caching behavior;
a browser still controlled by the persistent media worker conservatively
bypasses video cache across a pairing-mode transition so stale authenticated
bytes cannot cross that boundary. A duplicated or unpaired tab does not
register a bearer for its client id, so it cannot inherit another tab's worker
credential.

Clearing dashboard authentication also clears that client's worker entry.
Worker credentials remain memory-only. If worker termination or replacement
loses that map, the worker notifies only the originating client; that page
re-sends its own current bearer, waits for acknowledgement, and reloads the
native video once. A controller change uses the same bounded recovery path. If
that recovery acknowledgement times out, the page clears authorization and
renders the terminal re-pair state instead of leaving playback silently stuck.

Expired credentials still fail at the server on every Range request. An
authenticated `401` or `403` is reported only to the requesting client; the
page clears dashboard auth, removes the media source, dispatches the existing
authentication-required event, and renders terminal re-pair guidance rather
than retrying. Worker registration, controller acquisition, and bearer
acknowledgement share bounded timeout/abort behavior. If service workers are
unavailable or cannot take control, paired video reports an accessible
unsupported-streaming error instead of buffering an unbounded file. Downloads
and bounded image blobs continue using the existing object-URL helper.

## Boot housekeeping semantics

Dead-daemon manifest pruning is independent of orphan-browser process
enumeration. Boot runs both operations in separate guarded blocks. A reaper
exception no longer suppresses manifest pruning, and the documentation no
longer claims surviving browsers were proven gone. Partial reaper summaries are
logged according to their actual result. A confirmed surviving browser keeps
the manifest as its only session-level diagnostic; otherwise the PID-based
manifest decision remains independent.

## Testing and release

Every production change begins with a regression test that fails for the
reviewed behavior. Focused suites cover cancellation-resistant producers,
identity rebind while close waits, log-field secrecy and file modes, lock
contention, established-stream expiry/LRU eviction, page-level re-pair guidance,
service-worker Range header injection and tab isolation, authorization-scoped
cache behavior, bounded worker registration, worker-state recovery, terminal
media-auth denial, and independent boot cleanup.

After focused tests pass, run backend lint/type/security checks, the complete
Python suite, frontend typecheck/lint/tests/build, package construction, and the
full GitHub Actions matrix. Version metadata advances from 0.14.2 to 0.14.3 in
`VERSION`, all plugin manifests, upgrade highlights, version-sync tests, and the
changelog. Merge only after the exact pushed head is green; do not tag or publish
a package unless a separate established workflow does so automatically.

## Scope boundaries

This work does not redesign dashboard pairing, add remote-dashboard support,
change the same-user lockfile trust boundary, or make the session manifest a
database. It fixes the reviewed safety, authorization, and performance gaps
without unrelated refactoring.
