# cli — the octowright command line

Extracted from the root `AGENTS.md` so it loads only when you work in this
directory. The root file remains the canonical index.

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
