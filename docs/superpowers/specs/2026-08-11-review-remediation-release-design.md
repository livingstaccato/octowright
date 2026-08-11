# Review Remediation and 0.14.2 Release Design

## Goal

Resolve every merge-blocking and important correctness issue identified in the
review of PRs #100 and #101, merge the corrected branches in their existing
stack order, and ship the result as version 0.14.2 through the repository's
normal pull-request and CI process.

## Delivery shape

Preserve the existing PR boundary:

1. Fix `fix/review-batch-a-correctness` in place and merge PR #100 to `main`.
2. Rebase `feat/dashboard-pairing` onto the updated `main`, redesign its browser
   credential transport, and merge PR #101.
3. Create a focused release branch from the resulting `main`, bump 0.14.1 to
   0.14.2, update synchronized release metadata and changelog content, and merge
   that release PR.

This keeps the broad external-review remediation separate from the dashboard
authentication feature and gives each change set its own full CI signal.

## PR #100 correctness remediation

### Persona/profile lifecycle exclusion

Introduce shared per-persona/profile exclusion that covers both sides of the
race: browser launch must hold it from profile directory creation/open through
session registration, and persona/profile deletion must hold the same exclusion
from the in-use check through removal. Locks must not be held for unrelated
profiles, and deletion must retain its existing live-session refusal behavior.

### Bridge-state serialization

Keep the existing POSIX `flock` path and add a real Windows file-lock path for
the complete read-modify-replace transaction. Lock acquisition failures remain
best-effort and must not kill the follower heartbeat, but supported Windows
hosts must no longer deliberately run the lost-update path.

### CI and packaging correctness

- Restore every direct test monkeypatch after the daemon integration test.
- Read scanned Python source explicitly as UTF-8.
- Declare Vite as a direct frontend development dependency and restore a stable
  CSS artifact contract so the generated HTML, package data, and wheel verifier
  agree.
- Preserve the zero-high-vulnerability audit gate.

### Incomplete behavior fixes

- Validate launch URLs before session-directory or Playwright allocation.
- Make saturated discovery misses avoid repeated sorted full-directory scans by
  caching overflow hits and negative results against the directory generation.
- Render dashboard degraded/stale state instead of only recording it in memory.
- Make terminal `close_all` continue after per-session failures and make poll
  death observable even when the recorder itself caused the failure.

Each behavior change receives a failing regression test before implementation.

## PR #101 dashboard authentication redesign

### Credential model

Do not use a browser cookie for the dashboard bearer. Cookies do not isolate
ports, so a service on another loopback port can receive a bearer issued by the
Octowright port.

The pairing page redeems its single-use fragment ticket and receives a random
dashboard bearer in the JSON response. It stores that bearer in `sessionStorage`,
which is scoped to the exact scheme, host, and port, then redirects to the
dashboard. A new tab must pair independently; a leader restart invalidates all
bearers.

### Authenticated transports

- The shared API fetch helper sends the bearer in a dedicated dashboard auth
  header. The existing `X-Octowright-Token` capability header remains accepted
  for follower and programmatic callers.
- Dashboard SSE uses streaming `fetch` so it can carry the auth header instead
  of native `EventSource`.
- Browser WebSockets carry the bearer in `Sec-WebSocket-Protocol`; the server
  validates the bearer and selects only the fixed Octowright protocol, never
  echoing the secret as the selected protocol.
- Protected media is fetched with the auth header and presented through
  revocable blob URLs rather than direct credentialless element URLs.

The public static SPA and `/new-tab` remain cookieless. Sensitive APIs, media,
SSE, and WebSockets remain centrally denied when pairing is enabled and no
valid bearer or capability token is supplied.

### State and request boundaries

Pairing state belongs to one Starlette app instance rather than a process-global
singleton. Replacing a leader token creates a fresh state. Tickets and sessions
remain bounded, ticket comparison remains constant-time, and session access
updates true LRU order. Pairing redemption uses the shared capped JSON reader so
`OCTOWRIGHT_MAX_REQUEST_BODY_BYTES` applies to the unauthenticated bootstrap.

The CLI derives its reachable dashboard base from validated leader metadata,
including IPv6 loopback, rather than hard-coding `127.0.0.1`.

## Testing and CI

Use test-driven changes for every production behavior. Focused tests cover each
regression first, followed by `make lint`, frontend tests/build, the Python test
suite, wheel/sdist verification, and the repository's full GitHub Actions
matrix. The stacked pairing branch must receive an explicit full workflow run
if its non-`main` PR base prevents the automatic trigger.

Do not merge a branch with unresolved required checks. After each merge, fetch
and verify the next branch against the actual updated `main` before continuing.

## Version 0.14.2

After both functional PRs merge, create a release branch from updated `main`.
Change `VERSION` from `0.14.1` to `0.14.2`, synchronize every plugin manifest
validated by `scripts/check_plugin_versions.py`, and add the corresponding
changelog release entry using the repository's existing format. Run the full
release-relevant verification and merge the release PR. Tagging or publishing a
GitHub Release is outside scope unless the repository's established version-bump
workflow performs it automatically.

## Failure handling

If a platform-only failure cannot be reproduced locally, use the failing Actions
job as the authoritative regression signal and rerun the smallest applicable
matrix slice before the full matrix. If a merge changes the expected head SHA,
stop and rebase rather than force-pushing shared history without explicit need.
Any unrelated failure discovered during the work is reported separately and is
not hidden by weakening tests or checks.
