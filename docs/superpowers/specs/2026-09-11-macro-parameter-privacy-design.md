# Macro parameter privacy: Parts 0 and B, derived

Date: 2026-09-11
Status: design, revision 7. Not approved.

**Part A shipped separately** as `c4d96753` and is out of scope here. What
remains is Part 0 (threading) and Part B (`parameter_specs`).

## Why this revision reads differently

| Revision | Claimed | Actual | Review |
|---|---|---|---|
| r1 | a resolver and a schema | never said how the value reaches the call sites | not converged, 14 upheld |
| r2 | 2 resolve sites, 2 consumers | 3 sites, 6 consumers | converged, 13 upheld, 1 deadlocked |
| r3 | 3 sites, 6 consumers, "purely additive" | a 4th redaction site; the union claim was false | converged, 12 upheld, 0 deadlocked |
| r4 | every table AST-derived | tables were derived; two filters bounding them were not disclosed, and the conclusion drawn from measurement E was false | not converged, 12 upheld, 1 refuted, 1 deadlocked |
| r5 | filters disclosed, script committed | the inventories held; the recorder *lifetime* decision was wrong in three directions at once, and one path builds two ledgers | not converged, 11 upheld, 1 refuted, 1 deadlocked, 1 unproven |
| r6 | session scope, two invariants | scope was right; it created three defects of its own, and a **third** undisclosed write filter hid the screenshot | not converged, 11 upheld, 1 deadlocked, 1 incomplete |

r1-r3 failed by writing prose about code. r4 fixed that and failed one level up,
in three distinct ways worth separating because they have different fixes:

1. **A measured fact, a false implication.** r4 wrote "nested argument values are
   never statically knowable." `substitute()` is pure and `execution.py:545`
   substitutes the *entire* action list before dispatch, including a
   `macro_call` action's own `args` sub-dict. By `calls.py:54` the nested args
   are concrete. Eager resolution is possible; r4 said it was impossible and
   built the architecture on that. Fixed below, on different grounds.
2. **An undisclosed filter on the policy surface.** r4's inventory A covered
   eight callee names that appeared nowhere in the document. `redact_preview`
   and `_redact_sink_value` were therefore invisible to a table claiming to be
   exhaustive. The surface is now derived and printed (inventory 0).
3. **An undisclosed filter on writes.** r4 detected durable writes by
   `atomic_write_text`/`_json_write`, so `Recorder.record` -- which writes and
   flushes a raw handle per action, and is the sink a macro run actually streams
   to -- was missing. Writes are now enumerated by write *shape*, independent of
   the policy surface.

r5 disclosed both filters and committed the script, and the inventories held. It
failed on a **decision**: it restored the recorder wrapper at the run boundary
to stop wrappers stacking, and three findings then showed that restoring is what
*creates* the leak -- the stacking r5 called corruption is also the only thing
scrubbing step 1's credential out of step 2's rows. Restoring it, in a session
whose browser keeps emitting events after the run returns, trades a bounded
annoyance for an unbounded exposure. r6 untied that knot -- session scope is
upheld and no reviewer argued for going back -- and then made three mistakes that
only exist *because* of session scope: it kept an install guard that was correct
per-run, it forgot that the JSONL is also `save_macro`'s input, and it left a
diagnostic-suppression branch reading a set that is now always non-empty.

And it hid a sink for the third revision running. r4's write filter missed the
recorder because it only looked for write helpers; r6's missed the **screenshot**
because Playwright writes that file itself, through a path argument, with no
helper, handle or `open()` call anywhere in this process. The one durable
artifact that can hold a *rendered* credential had no rule, and deleting
`capture_blocked` would have left the gate fully green. Fixed below by naming the
external writers explicitly rather than by another shape heuristic -- a
path-shaped rule was tried first and matched every reader in the tree
(`tail_log`, `_read_window`, `list_macros`), turning a 17-row inventory into 46
rows of noise.

The third generalises into the gate's design: **a rule defined over redaction
calls gets greener when a redaction call is deleted.** The gate is inverted
accordingly.

## The derivation script is committed

`scripts/derive_privacy_sites.py`. One command reproduces every table below:

```bash
uv run --active python scripts/derive_privacy_sites.py
```

r4 cited `scratchpad/derive_privacy_sites.py`, which was never in the
repository, so "pasted verbatim" was itself an unverifiable assertion about
code -- the exact failure the revision claimed to be correcting.

## Measured inventories

### 0. The policy surface -- derived, not hand-listed

A callee counts as a policy call if it is a module-level function of
`macros/privacy.py` or `artifacts/redaction.py`, or if its name matches
`/(redact|scrub|sensitive|credential)/i` anywhere under `src/octowright`.
**59 callee names.** The rule over-matches -- HTTP exposure guards named
`guard_sensitive_http` are access control, not redaction -- and the report keeps
them, tagged, rather than filtering them out of sight. Over-matching is visible;
under-matching is what produced r4.

### A. Policy boundaries -- 30 cross-module in Part 0 scope

Part 0 scope is `macros/`, `artifacts/` and `recorder.py`. Intra-module calls
already receive `sensitive_values` as a parameter and are omitted here.

Two columns, because they are different questions. **Can resolve** is whether the
scope holds a `macro`/`args` binding to resolve policy *from*. **Holds policy**
is whether it already has a resolved one. r4 reported only the first and called
the difference blindness, which overstated the work: `write_run_bundle` cannot
resolve, but it is handed `sensitive_values` today.

```
file:line                                     enclosing fn                  call                      can resolve           holds policy
src/octowright/artifacts/models.py:38         new_manifest                  redact_mapping            ** NONE **            --
src/octowright/artifacts/models.py:68         new_run_result                redact_args               args_used,macro       --
src/octowright/artifacts/reports.py:27        write_artifact_manifest       redact_mapping            ** NONE **            --
src/octowright/artifacts/reports.py:47        write_run_bundle              redact_args               ** NONE **            sensitive_values
src/octowright/artifacts/reports.py:54        write_run_bundle              scrub_sensitive_values    ** NONE **            sensitive_values
src/octowright/artifacts/reports.py:55        write_run_bundle              scrub_sensitive_values    ** NONE **            sensitive_values
src/octowright/artifacts/reports.py:56        write_run_bundle              scrub_sensitive_values    ** NONE **            sensitive_values
src/octowright/artifacts/reports.py:66        write_run_bundle              scrub_sensitive_values    ** NONE **            sensitive_values
src/octowright/artifacts/reports.py:131       _redact_evidence              redact_preview            ** NONE **            --
src/octowright/artifacts/script_export.py:441 _safe_default                 is_sensitive_arg_key      args                  --
src/octowright/artifacts/script_export.py:445 _safe_default                 scrub_sensitive_values    args                  --
src/octowright/artifacts/script_export.py:445 _safe_default                 sensitive_arg_values      args                  --
src/octowright/macros/artifacts.py:65         plan_macro_artifact           redact_args               args,args_used,macro  --
src/octowright/macros/artifacts.py:158        run_macro_artifact            sensitive_arg_values      args,args_used,macro  sensitive_values
src/octowright/macros/artifacts.py:229        run_macro_artifact            scrub_sensitive_values    args,args_used,macro  sensitive_values
src/octowright/macros/artifacts.py:344        _manifest_for_plan            redact_args               args_used,macro       --
src/octowright/macros/artifacts.py:394        _compact_manifest             redact_args               ** NONE **            --
src/octowright/macros/execution.py:215        _format_status                _redact_action            ** NONE **            --
src/octowright/macros/execution.py:449        _build_failure_payload        _redact_action            ** NONE **            sensitive_values
src/octowright/macros/execution.py:471        _build_failure_payload        _redact_action            ** NONE **            sensitive_values
src/octowright/macros/execution.py:544        _run_macro_impl               install_sensitive_recorder args,effective_args,macro  sensitive_values
src/octowright/macros/lint.py:139             _field_carries_credential     url_carries_credential    ** NONE **            --
src/octowright/macros/lint.py:145             _field_carries_credential     code_carries_credential   ** NONE **            --
src/octowright/macros/lint_urls.py:151        _param_name_is_secret         is_sensitive_key          ** NONE **            --
src/octowright/macros/lint_urls.py:264        code_carries_credential       is_sensitive_key          ** NONE **            --
src/octowright/macros/repair.py:120           repair_preview                _redact_action            macro                 --
src/octowright/macros/repair.py:125           repair_preview                _redact_action            macro                 --
src/octowright/macros/repair.py:192           repair_apply                  _redact_action            macro                 --
src/octowright/macros/repair.py:193           repair_apply                  _redact_action            macro                 --
src/octowright/macros/substitution.py:130     is_credential_arg             is_credential_key         ** NONE **            --

in Part 0 scope: 86   cross-module: 30
cannot resolve AND holds no policy (BLIND): 10   holds a resolved policy already: 7
```

That resolves into three kinds of work:

- **Seven sites swap a frozen tuple for the ledger** -- `write_run_bundle` x5 and
  `_build_failure_payload` x2. No new parameter, a type change.
- **Five are name-only** and classify a bare key with no value in reach:
  `lint.py:139`, `lint.py:145`, `lint_urls.py:151`, `lint_urls.py:264`,
  `substitution.py:130`. Not threading targets at all.
- **Five need a parameter they do not have**: `models.py:38`, `reports.py:27`,
  `reports.py:131`, `artifacts.py:394`, `execution.py:215`.

`execution.py:215`, `execution.py:449`, `execution.py:471` and the four
`repair.py` sites call `_redact_action`, which blanks `value`/`text` for
`fill`/`type`/`fill_by` only -- so a credential in a `navigate` URL passes
through. They work without a ledger and get strictly better with one.

81 further boundaries exist outside Part 0 scope (42 of them one HTTP guard); the
derivation script lists them by package and callee so the scope decision is
visible rather than implied.

### B. Durable writes -- 17 in scope, including the screenshot

Three shapes are derived from our own code (a write helper, a raw handle write,
`open()` in a write mode). The fourth cannot be: Playwright writes the file
itself. **The external writer list is explicit and stated here rather than left
implicit** -- `screenshot`, `save_as`, `pdf`, and `tracing.stop(path=...)`, found
by grepping the tree for each. That is a hand-list, and disclosing it is the
whole point: r4, r5 and r6 each shipped a table bounded by a filter the document
did not name.

```
file:line                                         enclosing fn                    shape               holds policy
src/octowright/artifacts/reports.py:21            _json_write                     helper              -- NONE --
src/octowright/artifacts/reports.py:29            write_artifact_manifest         helper              -- NONE --
src/octowright/artifacts/reports.py:58            write_run_bundle                helper              sensitive_values
src/octowright/artifacts/reports.py:59            write_run_bundle                helper              sensitive_values
src/octowright/artifacts/reports.py:64            write_run_bundle                helper              sensitive_values
src/octowright/artifacts/reports.py:70            write_run_bundle                helper              sensitive_values
src/octowright/artifacts/reports.py:94            refresh_run_summary             helper              -- NONE --
src/octowright/artifacts/script_export.py:353     write_macro_cli                 helper              -- NONE --
src/octowright/macros/artifacts.py:312            _capture_screenshot             delegated           sensitive_values
src/octowright/macros/artifacts.py:316            _capture_screenshot             delegated           sensitive_values
src/octowright/macros/artifacts.py:527            macro_artifact_verify           helper              -- NONE --
src/octowright/macros/storage.py:103              save_macro                      helper              -- NONE --
src/octowright/macros/storage.py:169              write_macro                     helper              -- NONE --
src/octowright/recorder.py:119                    __init__                        open for write      -- NONE --
src/octowright/recorder.py:157                    record                          raw handle          -- NONE --
src/octowright/recorder.py:179                    record_control                  raw handle          -- NONE --
src/octowright/recorder.py:193                    _write_truncation_marker        raw handle          -- NONE --

total: 71   in Part 0 scope: 17
of those STREAMING (not via a write helper): 6   holding no policy: 11
```

`artifacts.py:312` is `await screenshot(path)` -- the file write, passed
positionally, which is why a keyword-only rule missed it too. `:316` is the
evidence record pointing at that file. `reports.py:21` is `_json_write` itself,
the helper the bundle writers call.

Five categories, by the entry point that reaches them:

| Category | Sinks | Invariant |
|---|---|---|
| Streaming | `recorder.py:119,157,179,193` | The session wrapper is installed. Asserted at install (see below), not at the write. |
| Rendered | `artifacts.py:312` | `capture_blocked` from the **run view**, never from the session set. |
| Bundle, holding a tuple | `reports.py:58,59,64,70` | Sealed `RunLedgerView` replaces `sensitive_values`. |
| Bundle, holding nothing | `reports.py:29`, `reports.py:94`, `script_export.py:353` | Sealed view as a parameter -- *when reached from a run*. |
| No run exists | `storage.py:103`, `storage.py:169`, `artifacts.py:527` | Authoring-time and post-hoc. No ledger; a save-time literal scan and already-redacted input respectively. |

The fourth row carries a caveat r6 asserted away and the review deadlocked on:
`write_artifact_manifest` and `refresh_run_summary` are also reachable from MCP
tools with no session and no run (`macro_artifact_critical_points_set`,
`macro_artifact_verify`). Requiring a sealed view unconditionally would fire
`privacy_unresolved` on legitimate paths. The invariant is therefore
**per-entry-point, not per-sink**: reached from a run, a sealed view is required;
reached from a tool, the payload must already be redacted and the sink may not
introduce values. Which entry points reach which sinks is not yet derived -- see
open items.

### C. Branches on the scrub tuple -- 5

```
src/octowright/macros/artifacts.py:301    _capture_screenshot          if sensitive_values
src/octowright/macros/execution.py:291    _dispatch_one                if action.get('action') == 'screenshot' and sensitive_values
src/octowright/macros/execution.py:437    _build_failure_payload       if sensitive_values
src/octowright/macros/execution.py:573    _run_macro_impl              if not sensitive_values
src/octowright/macros/privacy.py:263      install_sensitive_recorder   if sensitive_values and recorder is not None
```

Only the first two are the capture-coupling defect. The other three are
legitimate: suppressing a diagnostic bundle, preserving an exception cause when
there is nothing to scrub, and skipping a no-op wrap.

### D. Return signatures of the dispatch chain

```
dispatch_macro_call    (calls.py)      -> tuple[int, int]
_dispatch_one          (execution.py)  -> tuple[int, int]
_run_macro_impl        (execution.py)  -> MacroRunResult
run_sequence           (execution.py)  -> MacroSequenceResult
```

Counts only at the two inner levels. Nothing composed can travel back up.

### E. Substitution -- eager resolution *is* possible

```
I/O calls inside macros/substitution.py : none (pure)
substitute() call site                  : src/octowright/macros/calls.py:55
substitute() call site                  : src/octowright/macros/execution.py:545
```

Two call sites, no I/O. `execution.py:545` substitutes the whole outer action
list -- a `macro_call` action's `args` sub-dict included, since
`_substitute_value` recurses into dicts -- so `call_args` at `calls.py:54` are
already literals. **This refutes r4's stated justification for the ledger.** The
ledger survives on other grounds; see below.

### F. The corpus -- and what it says about nested exposure

```
macros                                    340
macro_call                                124
macro_call_with_args                      120
macro_call_args_with_placeholders         120
actions_in_branches                        24
macro_call_under_branch                     0
nested_credential_args                    240
nested_credential_LITERAL                   0
nested_credential_from_UNCLASSIFIED_outer   0
nested_credential_from_classified_outer   240

most-called nested macros:
   session-sign-in         81
   admin-session-sign-in   37
   stripe-pay               4
   buyer-buy-offer          2
```

The last four lines settle a severity question r4 got wrong in the other
direction. Collection is **name-based and happens once**, on the outer args
(`execution.py:543`); scrubbing is **value-based** across everything the recorder
sees. A nested credential is therefore already covered whenever its value arrived
from an outer arg whose own name classifies. All 240 nested credential args are
of that shape -- `password` fed by `{{password}}` -- so the structural gap is
**latent, not live exposure**, for this corpus.

It becomes live in two measurable shapes, both zero here: a credential
**literal** in a macro definition, and a chain where the **outer** name does not
classify (`{{p1}}` feeding a nested `password`). Both are things an author can
write tomorrow. This is the same latent/live distinction Part A ended up stated
in, and it is stated here with the measurement attached rather than as a severity
adjective.

## What the measurements decide

### The ledger, justified correctly this time

Measurement E says eager whole-graph resolution is *possible*. It is still not
the design, for reasons that are themselves measurable:

- **D**: nothing composed comes back up the chain, so every downstream consumer
  needs a sink regardless of when values are computed.
- **Eager pre-expansion duplicates `load_macro`.** Walking the graph ahead of
  execution reads each nested macro once to resolve and again to run -- the
  TOCTOU below, created rather than avoided.
- **Eager pre-expansion moves sink-guard failures ahead of execution.** The
  credential-sink guard raises *inside* `substitute()` (`substitution.py:140`).
  Pre-expanding a `macro_call` that sits under a conditional would refuse a
  branch that never runs. `macro_call_under_branch` is 0 today, so this is a
  fragility argument, not a live one -- stated as such.

So: `ResolvedPrivacy` stays frozen and per-invocation, and a **mutable
`PrivacyLedger` is threaded down** beside it. Each resolve appends. Down-passing
a mutable sink needs no change to `tuple[int, int]`.

### Scope: the ledger belongs to the session, not the run

This is r6's central change and it reverses r5.

The recorder is one long-lived append-only JSONL handle per browser session
(`recorder.py:93-122`). `SensitiveRecorder` scrubs by literal value match, so its
coverage is only as wide as the value set it holds. r5 restored the wrapper at
the run boundary to stop wrappers stacking. Three consequences, all of them
arguments against restoring:

- **Restoring removes real coverage.** `run_sequence` passes the same `session`
  to every step (`execution.py:657-659`). A credential typed in step 1 keeps
  appearing in step 2's page-derived rows -- `get_text_by` results, console
  entries, `navigate` URLs, websocket `payload_preview` -- none of which any
  other guard covers. Today's stacking is what scrubs them. r5 called that
  corruption and removed it without noticing it was also the protection.
- **Recorder writes outlive the run.** They are driven by asynchronous page
  events, so a per-run restore opens a cleartext window between one run ending
  and the next installing, with no quiesce step that could close it.
- **A streaming sink cannot assert a sealed ledger.** Sealing happens at run end;
  the recorder writes continuously during the run. r5's single gate rule was
  unsatisfiable at the one sink r6's inventory B was rebuilt to capture.

So the scrub set is **session-scoped and never uninstalled**, and stacking is
prevented by identity rather than by removal:

- `SessionPrivacyLedger` lives on the session. Exactly **one**
  `SensitiveRecorder` wraps `session.recorder`, installed idempotently -- if the
  recorder is already wrapped, the install is a no-op that returns the existing
  ledger. Nested resolves and later runs *append to the ledger the wrapper
  already holds*. Never a second wrapper. This is the constraint that reconciles
  #234 with #235: fixing nested collection by installing a wrapper per nested
  call would make #234 strictly worse.
- **The install is unconditional.** `install_sensitive_recorder` currently skips
  the wrap when the tuple is empty (`privacy.py:263`), which was right when the
  tuple was resolved once per run from the outer args. Under session scope it is
  the hole: a run whose outer args classify nothing installs no wrapper, and the
  nested or author-marked credential resolved later appends to a ledger nothing
  reads, then streams to the JSONL in cleartext. Wrap on the first macro run of
  a session regardless; an empty ledger is a cheap no-op (measured below).
- `RunLedgerView` is what a single run contributes: its resolved sites, its
  sink-blocked parameter names, its warnings. It is **sealed at run end** and it
  is what bundle writers receive. Run-scoped artifacts stay run-scoped; only
  recorder coverage is session-wide.

Two invariants, because there are two kinds of sink:

| Sink kind | Invariant |
|---|---|
| Streaming (`recorder.py` x4) | A live ledger is **installed**. |
| Bundle write reached from a run | A **sealed** `RunLedgerView` was supplied. |

The streaming invariant cannot be asserted at the write. Those four sites are
inside `Recorder`, *below* the wrapper, and never see a session -- and the
invariant is also simply false for a session that never runs a macro, which is
most of them. It is asserted where it is decidable: at install, and by a test
that after any macro run `session.recorder` is a `SensitiveRecorder` holding the
session ledger. Stating it as a per-write assertion, as r6 did, would have been
unimplementable at every site it named.

### What makes session scope safe: the shape guard, applied to everything

r5's worry about cross-step scrubbing was not baseless -- a short or common value
from step 1 rewriting step 2's output is corruption of the log an operator is
reading. r5 answered it by narrowing *lifetime*. r6 answers it by narrowing
*membership*, which is the axis that actually distinguishes the two cases.

The shape guard r5 applied only to author-marked values applies to **every**
value entering the substring pass: `str` leaves only, at least `MIN_SCRUB_LEN`
characters, not a common word. A value failing it never enters the session set;
it gets key-level redaction in bundles instead, and lint reports the exclusion.
A password survives that filter. `true`, `1`, `admin` and a two-letter locale do
not, and those are exactly the values whose substring match corrupts unrelated
rows.

### The recording is also an input, not only a sink

`save_macro` reads the JSONL back and maps recorded **values** to placeholder
names: `value_to_name = {value: key for key, value in param_map.items()}`,
applied by `substitute_in_action` over `iter_macro_actions` (`storage.py:64-67`).
Session-scoped scrubbing therefore breaks macro authoring, quietly: once a
credential-bearing run has happened on a session, a later recording on that same
session has `<redacted>` where the value was, the mapping matches nothing, and
the macro is saved with the literal marker baked into the action. The author sees
no error -- they see a macro that does not work.

r6 did not notice because it treated the recording as output only. The fix keeps
cleartext off disk while preserving the mapping: the wrapper substitutes a
**stable per-value token** rather than a constant, `<redacted:ab12cd34>`, where
the suffix is a truncated HMAC of the value under a **per-session key held only
in memory**. `save_macro` recomputes the token for each declared parameter value
the author supplies and maps token to placeholder exactly as it maps cleartext
today.

Costs, stated rather than buried:

- A recording saved in a **later** session cannot be mapped -- the key is gone.
  `save_macro` must detect tokens it cannot resolve and fail with that reason,
  not silently write `<redacted:...>` into a macro.
- The token is a per-session correlator: equal tokens mean equal values within
  one session. That is already true of `<redacted>` plus position, and the HMAC
  key never leaves memory, so it is not an offline guessing oracle the way a bare
  hash of a weak password would be.

### Bounding the hot path -- measured, then fixed properly

r6 asserted that dedup, compile-once and a cap bound the cost. Measured on this
machine, against a representative recorded action (`fill` with a 40-character
value, ~220 characters of text, a URL), 200 iterations per row:

```
values=   1  per-record=    53.9 us
values=   8  per-record=   431.3 us
values=  32  per-record=  1695.3 us
values= 128  per-record=  6812.7 us
values= 512  per-record= 27318.2 us
```

Linear in the value count, ~53us per value per record, on the synchronous
`Recorder.record` path that runs on the single event loop owning every live
browser session. At 128 values a 100-action macro spends 0.7 seconds in regex.
Session scope makes that multiplier strictly larger than r5's per-run set, which
is exactly what the review said and what r6's three mitigations did not address:
none of them touches the dominant term.

The dominant term is the **loop**: `scrub_sensitive_values` runs one pass per
value. One alternation over all values, compiled when the set changes, does the
same work in a single pass. Measured with the same records:

```
values=   8  current=   422.3 us   alternation=  0.9 us   speedup=   451x
values=  32  current=  1691.3 us   alternation=  0.9 us   speedup=  1830x
values= 128  current=  6716.0 us   alternation=  0.9 us   speedup=  7178x
values= 512  current= 38503.0 us   alternation=  0.9 us   speedup= 42234x
```

Flat, and under a microsecond. So: **one compiled alternation per ledger
generation**, rebuilt on append, longest-first so a longer secret wins over a
shorter substring of itself. Dedup still matters (it keeps the pattern small);
the cap survives as a safety net rather than as the mechanism, and the
`scrub_saturated` warning with it. `_serialized_variants` returns exactly one
variant for a plain value (measured), so the "up to three quote levels" in the
existing comment is the exception, not the cost driver.

This is the one place r7 changes shipped behaviour rather than only the design:
`scrub_sensitive_values` gets a compiled-pattern path. It is measurable, so it
gets a benchmark test with a regression bound rather than a claim.

### Persisted form: no new abstraction

r5 proposed a `PersistedLedger`. Cut. The run result already carries `warnings`
and the design already adds `privacy_tripwire`; the classifier version and
`resolved_sites` join them as plain fields. No second representation, no digests
justified by hypothetical diagnostics, and -- the point r5 got right and keeps --
**no scrub values on disk in any form**. `sensitive_arg_values` returns cleartext
because `_scrub_text` needs the literal; that tuple stays in memory.

`macro_artifact_verify`'s in-process caller (`artifacts.py:249`) has the live
ledger in scope and needs no disk round-trip.

### A green tripwire must not mean "policy never arrived"

The tripwire recognises a credential by matching the ledger's values, so an empty
ledger matches nothing and the write looks clean. `RunLedgerView` therefore
carries `resolved_sites: int` and `sealed: bool`, and a bundle write that
receives an unsealed or zero-resolve view raises `privacy_unresolved` rather than
passing. The streaming sinks assert `installed` instead, per the table above.

`conditional.dispatch_conditional` (`execution.py:294-306`) re-enters
`_dispatch_one` with the parent's values and appends nothing -- it is in the
resolve-site inventory for exactly this reason.

### One resolve per invocation, one ledger per session

`run_macro_artifact` resolves at `artifacts.py:158` and then calls `run_macro`
at `artifacts.py:194`, which resolves again at `execution.py:543`. Under r5 that
produced **two independent ledgers**, and the bundle was written from the outer
one, which never sees a nested resolve -- making r5's own first test
("ledger accumulates across `macro_call` and is visible to the outer bundle")
unsatisfiable on the path that writes bundles.

`_run_macro_impl` and `run_macro` take `ledger: SessionPrivacyLedger | None`.
`run_macro_artifact` passes the session ledger and its own `RunLedgerView` down.
Session scope makes this cheap to get right: the second resolve appends to the
same set rather than starting a new one, so the failure mode is a duplicated
resolve, not a divergent policy.

### Resolve from the dict that executes

`load_macro` is uncached and `save_macro`/`write_macro` rewrite the same path
atomically as concurrent MCP calls. Policy is therefore resolved from the dict
that executes -- `_run_macro_impl` and `dispatch_macro_call` each pass the macro
they already loaded, never the name, and `run_sequence` resolves inside the step
rather than ahead of it.

This **designs the divergence out rather than detecting it**, which settles what
r5 left as an unbuilt detector: r5 recorded a digest but named no comparator, no
comparison time and no operator-visible outcome. With one read per invocation
there is nothing to compare. `digest_macro` (`artifacts/digest.py`) is still
recorded in the run result, for provenance -- so an operator can tell afterwards
*which* revision ran -- and explicitly not as a tripwire.

### `run_sequence`: keep the fallback, narrow it

Resolving inside the step keeps the `load_macro` inside the handler that turns a
bad step into a recorded failure. Only spec-shape errors fall back to a
heuristic-only policy; `FileNotFoundError` and `JSONDecodeError` must reach
`run_macro`'s handler and become a recorded failed step honouring
`stop_on_failure`.

`stop_on_failure` defaults to **`True`** (`execution.py:629`,
`server/macros.py:197`), so on the default path the re-raise discards `steps`
entirely -- including the record of which steps already mutated the live browser.
That is a pre-existing gap, not one this design introduces, and it is named in
the open items rather than quietly assumed away: r5's test line asserted
behaviour the default does not have.

### Read paths do not write, and nothing migrates

r5 proposed that a manifest without a `privacy_classifier_version >= 3` stamp be
redacted and rewritten on load, then trusted -- turning `macro_artifact_list`,
`macro_artifact_status` and `macro_artifact_critical_points_get` into writers of
durable state with no stated failure behaviour and no copy of the original. r6
split that into read-side redaction plus an explicit `macro_artifact_migrate`.

r7 drops the migration tool as well. Reads redact, and that is the whole design:
`_compact_manifest` (`artifacts.py:394`) keeps its read-side `redact_args`, which
already makes every pre-Part-A manifest safe on every read. No consumer was ever
named that needs those files rewritten in place, and a rewrite tool is a second
code path that can corrupt an artifact nobody was being harmed by.
`reports.py:27` stays until the gate proves every caller pre-redacts.

### `parameter_specs` must survive a re-save, without a lost-update race

`save_macro` composes a fresh dict (`storage.py:92-99`) and carries no key it
does not name, so a re-record would silently drop `{"display": {"sensitive":
true}}`. `write_macro` deep-copies (`storage.py:144`) and does not.

Carrying specs forward means read-modify-write, and r6 called its answer
"compare-and-set" when it was not one: it read `updated_at` once and compared
nothing at write time, so the lost update it described remained possible in the
window it described. It also guarded only `save_macro`, while naming
`repair_apply`/`write_macro` as the other concurrent writer.

A timestamp comparison cannot fix this by itself -- `atomic_write_text` is atomic
in its rename, not in the read-modify-write around it, and there is no
conditional-write primitive on the filesystem here. So the transaction is
**serialized**: both writers take a per-macro exclusive lock (the pattern already
used by `singleton.py` and `session_manifest.py`) across read, merge and write.
`updated_at` is still compared inside the lock, because a lock does not protect
against a writer that never took it -- an older build, or a human editing the
file -- and a mismatch there is an error the author sees rather than a spec
silently dropped.

### Capture and diagnostics decouple from the session set

Every branch that reads the scrub tuple must be re-examined under session scope,
because the tuple it reads is now non-empty for the rest of the session after
the first credential-bearing run. r5 and r6 both called three of the five
branches in inventory C legitimate. Two of those were only legitimate per-run:

- `artifacts.py:301` and `execution.py:291` (capture) take
  `capture_blocked` from the **run view** -- decided by its own rule, a
  CREDENTIAL-tier value in *this run* -- not from the session set.
- `execution.py:437` (`_build_failure_payload`) suppresses the entire diagnostic
  bundle when the tuple is truthy, substituting `{"diagnostic_suppressed":
  "classified macro arguments"}`. Session-scoped, that is permanently true after
  the first credential run: every later failure on that session loses its
  console tail, its HTML and its screenshot, and is told a reason that is no
  longer the real one. It reads the run view too.

The remaining two are genuinely scope-independent: `execution.py:573` preserves
an exception cause when there is nothing to scrub, and `privacy.py:263` skips a
no-op wrap -- and that one is removed anyway, since the install is now
unconditional.

`artifacts.py:301` also does `path.unlink(missing_ok=True)` inside a directory
created empty with `mkdir(exist_ok=False)` (`artifacts/paths.py:53`) -- it can
never remove anything. Replace it with a `screenshot_suppressed` evidence
record.

## Part B -- `parameter_specs`

```json
{
  "parameters": ["email", "password", "display", "user_count"],
  "parameter_specs": {
    "display":    {"sensitive": true},
    "user_count": {"sensitive": false}
  }
}
```

Resolution: `parameter_specs[name]["sensitive"]` when present and a real `bool`,
else the Part A heuristic. Top-level names only; sensitivity inherits down a
branch; no path syntax, so a nested leaf cannot be unmarked.

Resolved at three sites -- `_run_macro_impl:541`, `run_macro_artifact:158`,
`dispatch_macro_call:54` -- each from the dict it already loaded, each appending
to the one session ledger. `run_macro_artifact` passes its ledger into the
`run_macro` it calls (`artifacts.py:194`) rather than letting a second one be
built.

Sink guard is **one-directional**: `sensitive: true` tightens, `sensitive: false`
never loosens. Only `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` unblocks a sink; an
author-supplied file must not. An ignored unmark warns in lint and in the run
result.

The shape guard is not special to marked values -- since r6 it admits *every*
value into the session set, marked or heuristic. An author-marked value that
fails it is still excluded, and lint reports that exclusion, because a marked
short or common value is precisely the one that would rewrite unrelated rows.

**No new environment variables.** r4 proposed `OCTOWRIGHT_PARAMETER_SPEC_WARN`
(three modes) and `OCTOWRIGHT_PARAMETER_SPEC_UNKNOWN` (two) -- six combinations of
ambient configuration for a feature with no implementation and no consumer asking
for variation. Ship one behaviour: preserve unknown fields, warn on
credential-related mismatches. Add a knob when a deployment needs one.

**One published shape.** `parameter_specs` is declared on `MacroDetail`.
`sensitive_parameters` is **not** added to `MacroListEntry` and `MacroSummary`
both; no named client needs the resolved verdict in either, and two wire shapes
would need synchronised compatibility tests on every classifier change. Add it to
the one representation a client asks for, when one does.

`warnings: NotRequired[list[str]]` on `MacroRunResult` (`total=True`),
`warnings: list[str]` on `MacroSequenceStep` (already `total=False`,
`mcp_types.py:71`), and on the `run_macro_artifact` result.

**Tripwire response**: scrub the output, persist `privacy_tripwire: true`, log at
error. No dedicated counter until an alert or telemetry consumer is named -- the
marker already makes the event machine-detectable and CI asserts on it.

## The gate: a sink lockfile, not a framework

A rule that enumerates redaction calls is satisfiable by **deleting a redaction
call**, so the gate enumerates durable-write **sinks** instead and asks what each
carries. That property is why the gate exists and it is not negotiable.

What is negotiable is the machinery, and the review has now asked twice to cut
it: a repository-wide AST write-shape scanner, in CI, with one policy consumer,
is a bespoke static-analysis framework for a feature that has none of it yet. r6
half-accepted by scoping it. r7 accepts the substance:

- `scripts/derive_privacy_sites.py` stays an **audit aid** that a human runs. It
  is not a CI framework and nothing in the build depends on its classifications.
- CI keeps one cheap, deletion-resistant check: the derived sink list is frozen
  into `tests/fixtures/privacy_sinks.json`, and a test re-derives and diffs it.
  Adding a durable write to `macros/`, `artifacts/` or `recorder.py` fails that
  test until someone updates the fixture *and* states which invariant the new
  sink carries. Deleting a redaction call does not make it pass.
- Contract tests on the outputs do the rest -- they catch a wrong value, which
  no structural rule can.

This is the same shape as Part A's `privacy_classifier_baseline.json`: one
fixture, one test, no framework. That fixture is what let Part A ship a
vocabulary change without a reviewer having to re-read the classifier.

**The script's own bugs, since a table is only as good as it:** r6 shipped
`is_cross_module`, a dead predicate documented as if it were live, and keyed its
definition map by bare function name, so same-named functions in different
modules (`record`, `routes`, `redact_header_values`) collided and could mislabel
a cross-module call as intra-module. Both fixed here; the map now holds every
defining file per name.

## Testing

- Exactly one `SensitiveRecorder` wraps a session no matter how many runs,
  sequence steps or nested calls execute: after N runs, `session.recorder` is
  wrapped once and the ledger holds the union (#234).
- The wrapper is installed even when the outer args classify nothing, and a
  credential resolved only at depth 2 is scrubbed from the JSONL (#235 plus the
  install-guard hole).
- A value first seen in step 1 is still scrubbed from step 2's page-derived rows
  -- `get_text_by`, console, `navigate` URL, websocket preview.
- **Authoring survives scrubbing**: record a macro on a session that has already
  run a credential-bearing macro, `save_macro` it, and the declared parameter
  maps to a placeholder rather than to `<redacted:...>`. A recording carrying
  tokens from a previous session fails with that reason instead of writing the
  marker into the macro.
- A credential **literal** inside a nested macro definition is caught; a nested
  credential fed by an unclassified outer name is caught. Both measure zero in
  the corpus and are the shapes that turn measurement F live.
- The artifact path resolves once: `run_macro_artifact` and the `run_macro` it
  calls share one ledger, and the bundle sees nested resolves.
- **A failure after a credential-bearing run still produces a diagnostic
  bundle** -- the `execution.py:437` branch reads the run view, so it is not
  permanently suppressed by the session set.
- A screenshot is suppressed only when *this run* blocks capture, and
  `artifacts.py:312` is covered by the gate rather than absent from it.
- A value failing the shape guard never enters the alternation, gets key-level
  redaction, and is reported by lint.
- Scrub cost per record is flat in the value count: a benchmark asserts the
  alternation path stays within a bound at 128 values, where the per-value loop
  measured 6.8 ms.
- `MAX_SCRUB_VALUES` overflow sets `scrub_saturated` and surfaces a warning.
- Bundle writes reached from a run with an unsealed or zero-resolve view raise
  `privacy_unresolved`; the same sinks reached from an MCP tool do not.
- Re-saving a macro preserves `parameter_specs`; two concurrent writers cannot
  lose one another's specs, and a rewrite by a writer that never took the lock
  is refused rather than merged.
- Read-only artifact tools never write.
- `tests/fixtures/privacy_sinks.json` matches the derived sink list; adding a
  durable write in Part 0 scope fails until the fixture names its invariant.
- `run_sequence` with `stop_on_failure=False` and a missing macro returns the
  other steps.
- Widening does not newly refuse a corpus macro in a sink -- before/after over
  the real corpus, the check omitted for Part A and caught in peer review.
- `session-sign-in` marked via `parameter_specs` takes effect for all 81 callers.

## Risks

**The signature budget, derived from inventory B**: four bundle writers swap a
tuple for a sealed view (`reports.py:58,59,64,70`), three take one they do not
have *when reached from a run* (`reports.py:29`, `reports.py:94`,
`script_export.py:353`), five boundaries in inventory A need a parameter
(`models.py:38`, `reports.py:27`, `reports.py:131`, `artifacts.py:394`,
`execution.py:215`), `run_macro`/`_run_macro_impl` take an optional ledger, and
`scrub_sensitive_values` gains a compiled-pattern path. The four streaming sinks
and the rendered sink need no signature change -- they are served by the wrapper
and by the run view.

**Session scope is a deliberate trade.** A credential from run 1 is scrubbed out
of run 40's recording on the same session. That is protection, and it is why the
shape guard is load-bearing: without it, session scope would eventually rewrite
unrelated text.

**Authoring now depends on an in-memory key.** The HMAC key dies with the
session, so a recording outlives the ability to map its tokens. That is a real
loss of a workflow that works today -- record now, save days later -- and it is
traded for keeping cleartext out of the JSONL. If that trade is wrong, the
alternative is to keep authoring-time recordings unscrubbed and say so, not to
pretend both are free.

**A saturated ledger degrades quietly if the warning is ignored.**

**Corpus shape is one machine's.** 340 macros, the sign-in concentration, and
every zero in measurement F are from this checkout. The timings above are one
machine too.

## Open items

1. `MIN_SCRUB_LEN`, `MAX_SCRUB_VALUES`, and the common-word list. The cap is now
   a safety net rather than the cost mechanism, so it can be generous.
2. Which entry points reach `write_artifact_manifest`, `refresh_run_summary` and
   `macro_artifact_verify`. The invariant is per-entry-point and that reachability
   is asserted here, not derived -- the exact shape of mistake this document
   exists to stop making. It needs a call-graph pass in the derivation script.
3. Whether `lint_urls` migrates off `artifacts/redaction.py` here or later.
4. The authoring-time literal scan for `save_macro`/`write_macro`: severity and
   whether it belongs in `macro_lint`.
5. `run_sequence` with the default `stop_on_failure=True` discards accumulated
   step records on the re-raise. Pre-existing, out of scope, and the reason a
   privacy test cannot be written against the default path.

## Review provenance

- r1 `run-20260911T174636-f6cafbbc` -- not converged, 14 upheld
- r2 `run-20260911T185536-055a7916` -- converged, 13 upheld, 1 deadlocked
- r3 `run-20260911T204026-c1bddcab` -- converged, 12 upheld, 0 deadlocked
- r4 `run-20260911T221508-a3242176` -- not converged, 12 upheld, 1 refuted, 1
  deadlocked, 1 unproven
- r5 `run-20260911T230447-2a443ad4` -- not converged, 11 upheld, 1 refuted, 1
  deadlocked, 1 unproven
- r6 `run-20260912T001157-76c8d73b` -- not converged, 11 upheld, 1 deadlocked, 1
  incomplete

r6 findings addressed: `c-0001` (upheld, no change -- the shape guard is the
right trade), `c-0002`/`c-0008` (a real lock, not a timestamp compare),
`c-0003` (unconditional install), `c-0004` (invariant is per-entry-point; the
underived reachability is open item 2), `c-0005` (screenshot in inventory B via
an explicit external-writer list), `c-0006` (per-session HMAC tokens keep
authoring working), `c-0007` (cost measured, then removed by a single
alternation), `c-0009` (streaming invariant asserted at install, where it is
decidable), `c-0010` (capture and diagnostics read the run view),
`c-0011` (dead predicate removed, name-collision fixed), `c-0012`
(`macro_artifact_migrate` cut), `c-0013` (gate reduced to a fixture and a diff
test; the script is an audit aid).

Tracked as issues from this work: livingstaccato/octowright#234 (recorder
wrappers stack and are never uninstalled) and #235 (nested `macro_call` args are
never collected). They are one design, not two fixes -- see "Scope: the ledger
belongs to the session, not the run".
