# tests — suite bounds and teardown

Extracted from the root `AGENTS.md` so it loads only when you work in this
directory. The root file remains the canonical index.

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

**Correlating a dead browser to the test that killed it.** That file answers
"what is running now" and never "what was running then" — it is overwritten
each phase and deleted at session end. `scripts/watch_test_timeline.py` records
the history it throws away (a 0.25s poll from a separate process, so nothing is
added to the hot path of every phase) and reads it back against a macOS crash
report: `--correlate --newest-crash`, or an explicit `.ips` path or timestamp.

**Read its output knowing the report directory is mostly self-inflicted.**
Measured on a real machine, 31 reports split 27 `EXC_BREAKPOINT` on
`Chrome_ChildIOThread` (children aborting when `test_stability_chaos_live`
kills the shared driver with `pool._pw.stop()`), 3 `EXC_BAD_ACCESS` on
`CrRendererMain` (its CDP `Page.crash`), and **one** `EXC_BREAKPOINT` on
`CrBrowserMain` — the real headed abort. So the signal sits
under 30:1 noise and `--newest-crash` hands you a manufactured crash after any
suite run.

That real abort is no longer wholly unexplained, though it is not yet fixed.
`scripts/characterize_headed_crash.py` reproduced it on 2026-09-07 (26 reports
byte-exact to the field signature, Chromium 151), and each report's own
`procLaunch`/`captureTime` puts the browser's lifetime at **1.09–1.76s against
a 1.34s launch/close cycle** — so it dies **at close**, not at launch. What
remains open is what makes it happen at all: the same arm scored 26 crashes in
134 launches and then 0 in 872 an hour later, so it is bursty and conditional
on machine state. Read that script's docstring before spending time on it; it
records which experiments have already come back empty. A correlated row whose module carries a deliberate-crash mechanism
is therefore labelled, found by scanning that module rather than by listing
test names so a chaos test added later is covered. Matching is by substring and
cannot separate "uses the mechanism" from "mentions it" — the tool's own test
file flagged itself — so the note means *check whether this was deliberate*,
never proof that it was.

A related trap for anyone counting dumps: `browser_pool.crash_reports.enrich`
only decorates incidents octowright already observed, so this noise does **not**
reach `octowright_status()["crash"]["recent"]`. Any method that instead counts
fresh `.ips` files is measuring the chaos tests unless it filters by
signature.

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
