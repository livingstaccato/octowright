# Reliable MCP Follower Bridge Design

## Problem

Octowright currently uses a singleton leader plus stdio follower bridge. The
leader owns browser state and exposes HTTP-MCP at `/mcp`; each LLM client owns a
local `octowright serve` follower that proxies stdio MCP frames to that leader.

This works for fresh clients, but repeated field failures show a bad state:

- the daemon may be healthy,
- fresh HTTP-MCP clients may work,
- fresh stdio MCP followers may work,
- but the already-attached LLM MCP handle reports `Transport closed` or times
  out for 120 seconds.

The current bridge is a stateless bidirectional pump. If the remote leader
stream closes, stalls, or is replaced during restart, the follower process exits
or silently stops forwarding useful work. Some MCP clients do not reliably
respawn their configured stdio server inside the same conversation, so a single
bad bridge can poison the session.

## Goal

Make Octowright fail fast and recover for the next call whenever possible.

Target behavior:

- The first call affected by a broken leader stream may fail.
- That failure must happen in seconds, not after the client tool-call timeout.
- The follower stdio process should remain alive unless the local MCP client
  closes stdin/stdout.
- The next call should use a fresh HTTP-MCP leader session without restarting
  Codex, Claude, or another LLM client.
- Avoidable daemon/follower shutdown causes should be removed.

Transparent same-request recovery is not required for non-idempotent in-flight
requests. It is safer to return a clear bridge error than to guess whether the
leader partially executed a tool call.

## Non-Goals

- Replacing Playwright, FastMCP, or the singleton leader architecture.
- Rewriting browser lifecycle, profile storage, macro execution, or dashboard
  routing.
- Guaranteeing recovery if the LLM host refuses to use an already-live stdio
  process after receiving a JSON-RPC error.
- Replaying non-idempotent tool calls after the remote stream dies.

## Architecture

`octowright.proxy_bridge` will become a supervised bridge with three roles.

### Local Stdio Endpoint

The local endpoint owns `stdio_server()` for the lifetime of the follower
process. It reads MCP messages from the LLM client and writes responses or
bridge errors back to that same stdio connection.

It should exit only when the local client closes its streams or the follower
process receives an explicit shutdown signal. Remote leader failures must not
close local stdio by default.

### Remote Leader Session

The remote session owns one `streamablehttp_client()` connection to the leader's
HTTP-MCP endpoint. It is disposable.

The remote URL should be resolved from the current singleton lockfile whenever
the bridge reconnects. The bridge must not assume that the URL captured at
follower startup is still correct after `octowright restart`.

### Bridge Supervisor

The supervisor coordinates local and remote sides. It tracks:

- the cached client `initialize` request,
- the successful remote initialization state,
- local request IDs currently waiting for a response,
- per-request start times and deadlines,
- current remote URL/session id,
- reconnect attempts and last bridge error.

On remote failure, the supervisor cancels the remote session, fails unresolved
in-flight requests with a bridge JSON-RPC error, reconnects to the current
leader, replays initialization to the remote server, and forwards later client
requests over the new session.

## Message Handling

The bridge must be MCP-aware enough to handle JSON-RPC request IDs and the
initialize handshake.

### Initialize

The client sends `initialize` once per stdio process. The bridge forwards it to
the current leader and caches the request payload. When a later remote
reconnect happens, the bridge replays that cached initialize request to the new
remote HTTP-MCP session before forwarding any other client request.

The bridge must not send duplicate initialize responses to the local client.
Replay is only for remote session setup.

### In-Flight Requests

For each local request with an ID, the bridge records the ID and deadline before
forwarding it to the remote session.

If the matching remote response arrives, the bridge forwards it to local stdio
and clears the in-flight record.

If the remote stream closes or the deadline expires first, the bridge returns a
JSON-RPC error for that request and clears the in-flight record. The error
message should name the bridge layer and say that the remote leader session was
reset.

The bridge must not replay non-idempotent in-flight tool calls automatically.

### Notifications

Notifications have no response ID. If the remote session is healthy, forward
them. If the remote session is down, drop or log them according to MCP SDK
capabilities, but do not block later requests on notification delivery.

## Timeouts And Recovery

Add these defaults in `defaults.py`:

- `OCTOWRIGHT_BRIDGE_REQUEST_TIMEOUT_SECONDS`, default `20`.
- `OCTOWRIGHT_BRIDGE_CONNECT_TIMEOUT_SECONDS`, default `10`.
- `OCTOWRIGHT_BRIDGE_RECONNECT_MAX_SECONDS`, default `5`.

The bridge should use short reconnect backoff: `0.25s`, `0.5s`, `1s`, `2s`,
then cap at `5s`.

When the daemon is healthy but the stream is bad:

- do not restart the daemon,
- replace only the HTTP-MCP streamable session.

When the daemon is unavailable:

- return fast bridge errors to local requests,
- keep local stdio alive,
- keep reconnecting in the background with capped backoff.

When the local client closes:

- terminate the remote HTTP-MCP session,
- exit the follower process normally.

## Restart And Idle Interactions

The existing restart hardening should remain:

- `octowright restart` must not kill bare follower transports owned by MCP
  clients.
- `octowright restart` should wait for the requested port to become free and
  avoid silently accepting a fallback port during restart.

The idle watchdog should continue to treat active HTTP-MCP sessions as liveness
signals. Reconnect loops should be careful not to keep the daemon alive forever
when there is no local client. Local stdio liveness is the important signal:
when the local client disconnects, the follower should exit and stop creating
remote sessions.

## Diagnostics

Add structured bridge diagnostics without dumping large payloads:

- follower PID,
- current remote URL,
- current remote session id if available,
- last reconnect time,
- last error category/message,
- in-flight request count,
- reconnect attempt count,
- request timeout count.

Expose this in both places:

- `octowright_status` should include the latest leader-visible bridge
  diagnostics for HTTP-MCP sessions that reached the leader.
- follower stderr logs should report bridge state transitions for cases where
  the leader is unreachable and `octowright_status` cannot run.

The diagnostics must not log full MCP payloads by default. Log method names,
request IDs, and byte sizes only.

## Testing

### Unit Tests

Add supervisor tests with fake local and remote streams:

- happy path initialize, list tools, call tool,
- remote closes after initialize and next request reconnects,
- remote closes during an in-flight request and local receives JSON-RPC error,
- remote request deadline returns a bridge error before client timeout,
- initialize is replayed to the remote session after reconnect,
- local stdio close exits the bridge cleanly,
- daemon unavailable returns fast errors while local stdio remains alive.

### Integration Tests

Add a fake streamable HTTP leader that can:

- accept initialize/list tools/call tool,
- hang a response,
- close a stream mid-request,
- come back with a new session id.

Verify the follower process remains alive and later requests succeed after
remote failure.

### Real Smoke Test

Add a script or test that launches `octowright serve` through stdio and calls:

1. `initialize`
2. `octowright_status`
3. restart or kill the daemon leader
4. call `octowright_status` again
5. call `octowright_status` a second time if the first post-restart call was
   the expected fast bridge error

Expected result: the same stdio client process survives, and a later call
succeeds without restarting the MCP client.

## Cross-Platform Requirements

Core bridge behavior must avoid POSIX-only process assumptions.

Use anyio/httpx abstractions for stream and timeout behavior. Process-kill
smoke tests should either use existing platform guards or run only where the
repository already supports the relevant signal behavior.

Windows behavior should be validated for:

- no reliance on process groups in bridge tests,
- no POSIX-only `ps` parsing in bridge core,
- clean follower exit when local stdio closes.

## Rollout Plan

1. Implement the supervised bridge behind the existing `run_proxy()` entry
   point so callers do not change.
2. Keep the old raw-pump behavior temporarily reachable through a private test
   helper if useful, but production should use the supervisor.
3. Add unit tests before enabling reconnect behavior.
4. Add integration tests for remote stream failure and reconnect.
5. Add the real stdio smoke test as an opt-in reliability command first. Move
   it into default CI only after runtime and flake rate are measured.
6. Update agent-facing docs to say `Transport closed` should be exceptional; if it
   appears, run the diagnostic proof that distinguishes daemon, fresh client,
   and current client-handle failures.

## Implementation Decisions

- Bridge-generated failures should use JSON-RPC server error code `-32000` with
  messages prefixed by `Octowright bridge error:`. If the MCP SDK exposes a
  clearer application-error helper, wrap that helper around the same stable
  code/message shape.
- Bridge diagnostics should be written to stderr and to a bounded local state
  file under the XDG state directory. The state file should keep only the latest
  snapshot per follower PID plus a small recent-event ring.
- The real kill/restart smoke test starts as an opt-in reliability test command,
  not part of default `make test`.
