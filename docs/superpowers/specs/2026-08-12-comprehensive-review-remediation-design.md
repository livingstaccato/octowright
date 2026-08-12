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

An in-progress idempotency entry records the actual producer task. Reaching the
abandon threshold may request cancellation, but it must not delete the entry
until that producer is confirmed done. A producer that ignores cancellation
keeps its slot and causes same-key callers to receive the existing
unknown-outcome response rather than executing a second mutation. Successful,
failed, and cancelled producers retain the existing completion/eviction
behavior, and completed result TTL/size bounds remain unchanged.

## Atomic browser identity changes

Closing a browser by expected identity becomes one pool operation. The operation
acquires `_sessions_lock`, checks that the registry still contains the expected
session, applies protection rules, and pops that exact object before awaiting
its teardown. A stale deferred-close task returns without affecting a
replacement. Driver `keep-id` rekeying acquires the same lock, so it cannot
interleave between an identity check and pop.

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

## Range-preserving paired video

Paired video uses a dedicated same-origin service worker instead of converting
the complete response to a blob. Each controlled dashboard client sends its
current bearer to the worker through `postMessage`; the worker stores it by
`Client.id`. For video requests only, the fetch handler clones the request,
preserves headers including `Range`, adds `Authorization`, and forwards it to
the existing guarded `FileResponse` endpoint. A duplicated or unpaired tab does
not register a bearer for its client id, so it cannot inherit another tab's
worker credential.

Clearing dashboard authentication also clears that client's worker entry.
Expired credentials still fail at the server on every Range request. If service
workers are unavailable or cannot take control, paired video reports an
accessible unsupported-streaming error instead of buffering an unbounded file.
Downloads and bounded image blobs continue using the existing object-URL helper.

## Boot housekeeping semantics

Dead-daemon manifest pruning is independent of orphan-browser process
enumeration. Boot runs both operations in separate guarded blocks. A reaper
exception no longer suppresses manifest pruning, and the documentation no
longer claims surviving browsers were proven gone. Partial reaper summaries are
logged according to their actual result without changing the PID-based manifest
decision.

## Testing and release

Every production change begins with a regression test that fails for the
reviewed behavior. Focused suites cover cancellation-resistant producers,
identity rebind while close waits, log-field secrecy and file modes, lock
contention, established-stream expiry/LRU eviction, service-worker Range header
injection and tab isolation, and independent boot cleanup.

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
