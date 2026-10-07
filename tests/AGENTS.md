# tests — suite bounds and teardown

Extracted from the root `AGENTS.md` so it loads only when you work in this
directory. The root file remains the canonical index.

### Test-run bounds: per-test timeout and pinned order

Two `[tool.pytest.ini_options]` settings keep a wedged suite attributable.
Both are deliberate and worth knowing before changing them.

**`timeout = 300`, `timeout_method = "thread"` (pytest-timeout).** A target
that stops answering (a WebKit leg can) otherwise hangs the run forever,
because `page.on("crash")` never fires for a target that is merely
*unresponsive*. 300s is sized from the suite, not taste: the slowest
legitimate test is a two-participant headless WebKit scenario at ~81s, and a
whole CI leg finishes in ~10.5 minutes.

The `thread` method is chosen over the platform default, and the default
genuinely does not work here. With `signal`, pytest-timeout arms **one** alarm
across the whole runtest protocol and cancels it at the end — so the alarm is
spent the moment it fires: a test that times out in the call phase and then
wedges in teardown has no alarm left, and the process sits alive and silent.
A bound a second wedge walks straight through is not a bound. `thread` uses a `threading.Timer`
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
for source order. Prefer those over `-p no:randomly`: unloading the plugin
also unregisters the `--randomly-seed` option `addopts` still passes. The root
`conftest.py` registers an inert stand-in for that option **when the plugin is
absent**, so the flag parses; it exists because mutmut 3.x hardcodes `-p no:randomly` with no
way to configure it off, not as an endorsement of typing it by hand. The
plugin's own flags leave its seeding machinery intact and stay the right answer
for a human. Bump the constant to re-roll for everyone.

**`norecursedirs` names `mutants`.** mutmut copies the whole project —
`conftest.py` included — into `mutants/` and leaves it behind, and pytest would otherwise
walk it as an ordinary directory. Without the exclusion a bare `pytest`
at the repo root dies in *collection* after any `make mutmut`, with
`ImportPathMismatchError` on the duplicated `tests.conftest` and, under
`-p no:randomly`, "option names `{'--randomly-seed'}` already added" from the
two copies of the root conftest (`make test` passes `tests/` explicitly, so it
is unaffected). The setting **replaces** pytest's
built-in list rather than extending it, so the defaults are restated alongside
`mutants`; dropping one would quietly start collecting `build/`, `dist/` or
`node_modules/`.

**Exported-script tests strip mutmut's trampoline.** The exported macro CLI
(`artifacts/script_export.py`) is built from `inspect.getsource` of live
functions, and mutmut 3.x decorates every function it mutates with
`@_mutmut_mutated(<dict>)`, which `getsource` returns too, so an unstripped
exported script begins with a decorator naming a dict it never defines and
mutmut's clean run fails on every export test with `NameError: name
'mutants_x__serialized_variants__mutmut' is not defined`. Under mutmut only, the root `conftest.py` installs
`tests/_mutmut_compat.py`, which removes exactly that decorator line from
`getsource` output and keeps any other decorator.
The rendered functions are deliberately not excluded from mutation instead:
they are the credential guard and the scrubber. The exported script
therefore runs the unmutated body; a mutant in a rendered function is still
exercised by the live path the export tests compare against.

**Function caches are emptied before every test under mutmut.** mutmut forks
each mutant from the process that ran the clean pass, so a `functools` cache
filled there answers the mutant with the unmutated result and the mutant is
scored as a survivor (`scrub_engine._scrub_patterns`, for one, would hide
word-boundary mutants in `_identifier_bounded` and `_continues_identifier`
that fail the scrubber's tests when applied with `mutmut apply`). The root `conftest.py` therefore clears every `cache_clear`-able
function on an `octowright` module before each test, under mutmut only
(`tests/_mutmut_compat.clear_function_caches`).

**`make mutmut` is capped; `make mutmut-remote` runs it elsewhere.** Left
alone, mutmut starts one pytest child per CPU, each importing the whole test
selection, which saturates a workstation for the length of the run. The target
therefore passes `--max-children $(MUTMUT_JOBS)` (default 4, the CI runner's
vCPU count, so the nightly job is unchanged) and runs under `nice -n 10`;
`make mutmut MUTMUT_JOBS=8` raises it. `MUTMUT_REMOTE_HOST=<ssh host> make
mutmut-remote` (`scripts/mutmut_remote.sh`) sends the git-tracked files, with
their working-tree content, to a host you can already ssh to, runs `make
mutmut` there detached so a dropped connection does not stop it, and fetches
`mutmut-cicd-stats.json`, the log and the exit status into `.mutmut-remote/`
(git-ignored). `MUTMUT_JOBS` reaches the host only when you set it; otherwise
the host uses all its CPUs. The script's header lists its sub-commands
(`start`/`status`/`fetch`/`stop`/`sync`) and its other variables. mutmut copies
only `also_copy` into `mutants/`, which includes `scripts/` because a selected
test loads a script there by path.

**Mutant-killing tests live in `tests/mutation_kills/`**, named after the
module they target (`test_<package>_<module>.py`), and each must be named in
`[tool.mutmut]`'s `pytest_add_cli_args_test_selection`:
`scripts/check_mutmut_selection.py` (part of `make lint`) fails when a fast
test that imports a mutated module is missing from it, and a test outside the
selection never runs against a mutant.

**`# pragma: no mutate` only where every mutant on the line is unkillable.**
The pragma suppresses every mutant on its line, not just the one it was
argued for, so one placed for an equivalent mutant can hide killable ones
beside it (a mutated `.get()` key, an `or` turned `and`). Pragmas stay only on unreachable or import-time lines and literal
initialisations whose only mutants are never observed; an equivalent mutant
on a line that also carries killable ones is left as a known survivor.

**Read the score from `export-cicd-stats`, never from `mutmut results`.**
`mutmut results` prints only the mutants that still need attention — survived,
`no tests`, `timeout` — and **omits every killed one**, so its line count is the
size of the backlog and not the population; reading it as the population
reports a healthy score as a near-zero one and sends a triage after a harness
problem that does not exist. The second half of the same mistake is parsing the status column by last word: `no tests` ends in "tests"
and reads as a kill. `uv run mutmut export-cicd-stats` writes
`mutants/mutmut-cicd-stats.json` with `killed`/`survived`/`no_tests`/`timeout`/
`total` as integers, and that file is the only honest denominator.

Two things are worth knowing before acting on a survivor list. **Count is the
wrong ranking** — a big module dominates it while scoring fine, and a small
one with few survivors can have the worst rate — so rank by rate. And **most
survivors are not logic**: the large majority are string-literal or `None`
substitutions — dict keys, log event names, error wording. A handful of
whole-record equality assertions kills the string bulk in batches; the logic
ones are worth reading individually.

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
`mutmut --help`; no custom swap script is needed.

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

**Correlating a dead browser to the test that killed it.** That file answers
"what is running now" and never "what was running then" — it is overwritten
each phase and deleted at session end. `scripts/watch_test_timeline.py` records
the history it throws away (a 0.25s poll from a separate process, so nothing is
added to the hot path of every phase) and reads it back against a macOS crash
report: `--correlate --newest-crash`, or an explicit `.ips` path or timestamp.

**Read its output knowing the report directory is mostly self-inflicted.**
After a suite run the reports are dominated by deliberate crashes:
`EXC_BREAKPOINT` on `Chrome_ChildIOThread` (children aborting when
`test_stability_chaos_live` kills the shared driver with `pool._pw.stop()`) and
`EXC_BAD_ACCESS` on `CrRendererMain` (its CDP `Page.crash`). The real headed
abort is `EXC_BREAKPOINT` on `CrBrowserMain`, typically under ~30:1 noise, so
`--newest-crash` hands you a manufactured crash after any suite run. A
correlated row whose module carries a deliberate-crash mechanism is therefore
labelled, found by scanning that module rather than by listing
test names so a chaos test added later is covered. Matching is by substring and
cannot separate "uses the mechanism" from "mentions it" — the tool's own test
file flagged itself — so the note means *check whether this was deliberate*,
never proof that it was.

That real headed abort is not yet fixed and only partly explained. The
browser dies **at close**, not at launch (each report's
`procLaunch`/`captureTime` puts its lifetime within a launch/close cycle), and
it is bursty and conditional on machine state: the same reproduction arm can
crash often and then not at all. `scripts/characterize_headed_crash.py`
reproduces it; read its docstring before spending time on it, since it records
which experiments have already come back empty.

A related trap for anyone counting dumps: `browser_pool.crash_reports.enrich`
only decorates incidents Octowright already observed, so this noise does **not**
reach `octowright_status()["crash"]["recent"]`. Any method that instead counts
fresh `.ips` files is measuring the chaos tests unless it filters by
signature.

### Test-suite driver reaping

`tests/conftest.py` tracks every `BrowserPool` as it is constructed and, at
each test's teardown, sends `SIGTERM` to the driver of any pool still holding
one. A pool starts its Playwright driver lazily and only `shutdown_pool` ever
calls `pw.stop()`, so without the reaper every module that launches a real
browser and never shuts its pool down leaks a `playwright/driver/node` child
(several live at once under one pytest process), each holding a pipe, an OS
process and an `asyncio-waitpid` thread. With the reaper the peak is **1**,
and the affected tests run noticeably faster.

Signalling a pid rather than awaiting `pool.shutdown()` is deliberate. An async
autouse fixture *does* run for sync tests under `asyncio_mode = "auto"`, but it
also forces an asyncio loop onto the trio half of every
`pytest-anyio`-parametrized test, which then fails inside anyio's shielded
`CancelScope` with "must be called from async context" (the trio cases in
`tests/test_roster.py` among them). A sync fixture that signals a pid
needs no loop and cannot care which backend ran the test.

### Requiring live engines: `OCTOWRIGHT_REQUIRE_LIVE_ENGINES`

Every live fixture catches a launch exception and calls `pytest.skip` -- right
on a laptop with one engine missing, wrong on a runner that just ran
`playwright install`: an engine regression there (a launch arg WebKit starts
rejecting, a dependency lost in a runner image update) skips every test that
measures that engine, and a skip is green. The abort-string table in
`request_failures.py`, the closed-shadow snapshot and the SSRF redirect checks
all rest on those measurements.

Set `OCTOWRIGHT_REQUIRE_LIVE_ENGINES` on such a runner and those skips fail
instead: `1`/`all` for chromium, firefox and webkit, or a comma-separated list
(`chromium,webkit`) for a runner that installs only some. An unknown engine
name is a usage error rather than a silent no-op. Off (unset, `0`, `off`) is the
default, so a local run is unchanged.

The judgement lives in `tests/_live_engines.py`, applied from a
`pytest_runtest_makereport` hook in `tests/conftest.py`, not in each fixture --
19 modules carry their own catch-and-skip and a new one would not know to
participate. A skip of a `live_browser` test counts when it was **raised while
handling an exception** (`Skipped.__context__` is set, which is exactly the
`except Exception: pytest.skip(...)` launch pattern, `_maybe_skip_live_engine`
and `pytest.importorskip("playwright")`), or when its reason says `no usable
browser engine` (the daemon-driven tests read the failure from a tool result, so
there is no exception). A deliberate skip raised from nowhere -- "closed shadow
roots are only reachable through Chromium", "CDP Page.crash did not deliver" --
stays a skip. The engine is the test's parametrization, else Chromium, the
pool's default. So with `PLAYWRIGHT_BROWSERS_PATH` pointed at an empty
directory and `=webkit`, `test_macro_network_clean_no_text_live`'s webkit case
errors and its chromium/firefox cases still skip.

Known edge: the system-Chrome-channel test in
`test_browser_launch_engine_selection_live.py` skips from an `except` when no
system Chrome is installed, and counts as Chromium. Deselect it on a runner
that requires Chromium without installing system Chrome.

### Socket-leak tripwire

`tests/_socket_leak_tripwire.py` (registered from `pytest_configure`) fails an
otherwise green run whose pytest process ends holding more than `LEAK_BOUND`
(16) sockets it did not hold at session start, and names the tests that opened
them. Linux only (it reads `/proc/self/fd`); everywhere else it is a no-op.

It guards against ephemeral-port exhaustion (`net::ERR_NO_BUFFER_SPACE`,
`WSAENOBUFS`, seen on a Windows leg's loopback navigation): a per-test server
or client left open is how a serial run would get there without Linux
noticing. The suite does not currently leak — the process holds at most a
handful of sockets at any test's end, and a small session-end excess (a
handler thread still inside `time.sleep(20)`) stays well under the bound — so
that Windows failure is not explained by a leak in this process. Per-test cost is one `listdir` plus a
`readlink` per fd new since the previous test (~56us). To run without it, pass
`-p no:octowright-socket-leak-tripwire`.
