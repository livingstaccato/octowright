# Profiling

Finding a hot path in Octowright — a slow shell-out, a blocked event loop, a
wedged call — historically meant tailing the daemon log and eyeballing
timestamps during a live incident. `py-spy` gives you a sampling profiler
instead, with no code changes and no restart.

## Why py-spy, not memray

`memray` is also a dev dependency, but it's Linux/macOS-only — it hooks
native allocator internals with no Windows equivalent, and it isn't wired up
anywhere in this repo. `py-spy` is a native **sampling** profiler that
attaches to an already-running PID and works identically on Windows, Linux,
and macOS — the one hot-path tool that covers every platform this project
ships tests on. Reach for `memray` instead if you specifically need
allocation/memory profiling on Linux or macOS; for "where is time going,"
`py-spy` is the cross-platform default.

## Quick start

Both targets default to the live `octowright` leader, discovered from its
own lockfile — nothing to look up by hand in the common case:

```bash
# One-shot stack snapshot of every thread, right now
make profile-dump

# Flame graph over 30 seconds (profile.svg, open in a browser)
make profile-record
```

Override the target process or duration when needed:

```bash
make profile-dump PID=12345
make profile-record PID=12345 PROFILE_DURATION=90
```

`profile-dump` is the faster of the two for a live stall: it prints one
stack per thread immediately, so if the daemon is mid-hang you'll see
exactly which call it's blocked in. `profile-record` is better for a
recurring or slow-building cost (like a periodic loop) — sample across at
least one full cycle to catch it, not just an instant.

## Reading the output

`profile-dump`'s output names the exact call stack per thread:

```
Thread 5168 (idle): "MainThread"
    _poll (asyncio\windows_events.py:825)
    select (asyncio\windows_events.py:444)
    _run_once (asyncio\base_events.py:1898)
    ...
Thread 116 (idle): "asyncio_0"
    _worker (concurrent\futures\thread.py:81)
    ...
```

A thread pool worker (`asyncio_N`) sitting inside a `subprocess`/Windows wait
syscall for the full sampling window is the signature of a slow shell-out —
this is exactly how the housekeeping loop's blocking `Get-CimInstance`
process-table scan would show up, before it was moved onto a worker thread.
If instead the **MainThread** itself is stuck outside `_poll`/`select` (the
normal idle-event-loop shape above), that's the event loop itself blocked —
worth checking for a synchronous call that should have been `await
asyncio.to_thread(...)`'d, or wrapped in `session.timeouts.bounded()`.

`profile-record`'s flame graph (`profile.svg`) aggregates many samples: wide
frames are where the process spent the most wall-clock time across the
recording window.

## Direct py-spy usage

The Makefile targets are a convenience; `py-spy` itself has more commands
worth knowing about directly (`uv run py-spy --help`), including `py-spy
top` for a live, continuously-refreshing view — closer to `htop` than a
one-shot dump, useful for watching a suspected hot path in real time rather
than capturing it after the fact.
