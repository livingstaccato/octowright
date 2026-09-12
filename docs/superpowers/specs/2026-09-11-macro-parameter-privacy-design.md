# Macro parameter privacy: Parts 0 and B, derived

Date: 2026-09-11
Status: design, revision 6. Not approved.

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
annoyance for an unbounded exposure. That is the knot this revision unties, and
it is the same knot as issues #234 and #235.

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

### B. Durable writes -- 15 in scope, by write shape and what they hold

```
file:line                                         enclosing fn                shape             holds policy
src/octowright/artifacts/reports.py:21            _json_write                 helper            -- NONE --
src/octowright/artifacts/reports.py:29            write_artifact_manifest     helper            -- NONE --
src/octowright/artifacts/reports.py:58            write_run_bundle            helper            sensitive_values
src/octowright/artifacts/reports.py:59            write_run_bundle            helper            sensitive_values
src/octowright/artifacts/reports.py:64            write_run_bundle            helper            sensitive_values
src/octowright/artifacts/reports.py:70            write_run_bundle            helper            sensitive_values
src/octowright/artifacts/reports.py:94            refresh_run_summary         helper            -- NONE --
src/octowright/artifacts/script_export.py:353     write_macro_cli             helper            -- NONE --
src/octowright/macros/artifacts.py:527            macro_artifact_verify       helper            -- NONE --
src/octowright/macros/storage.py:103              save_macro                  helper            -- NONE --
src/octowright/macros/storage.py:169              write_macro                 helper            -- NONE --
src/octowright/recorder.py:119                    __init__                    open for write    -- NONE --
src/octowright/recorder.py:157                    record                      raw handle        -- NONE --
src/octowright/recorder.py:179                    record_control              raw handle        -- NONE --
src/octowright/recorder.py:193                    _write_truncation_marker    raw handle        -- NONE --

total: 59   in Part 0 scope: 15
of those STREAMING (not via a write helper): 4   holding no policy: 11
```

The "holds policy" column is new in r6 and it fixes a budget that was counted
from the wrong inventory: r5 derived twelve signature changes from inventory A
while the gate ran over inventory B, so sinks that appear only in B were costed
at zero. **Four sink categories, not two** -- r5's binary split missed the last
two:

| Category | Sinks | Mechanism |
|---|---|---|
| Streaming | `recorder.py:119,157,179,193` | The session wrapper. No parameter; asserts *installed*, never *sealed*. |
| Bundle writers holding a tuple | `reports.py:58,59,64,70` | Swap `sensitive_values` for a sealed run snapshot. |
| Bundle writers holding nothing | `reports.py:29`, `reports.py:94`, `script_export.py:353` | Take the snapshot as a parameter. |
| Authoring-time | `storage.py:103`, `storage.py:169` | No run exists. Save-time literal scan. |
| Post-hoc rewriters | `artifacts.py:527` | An MCP tool with no session, args or run. Operates only on already-redacted data; may mark `privacy_unverified`, may never introduce values. |

`reports.py:21` is `_json_write` itself, the helper the bundle writers call, not
a sink of its own.

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
- `RunLedgerView` is what a single run contributes: its resolved sites, its
  sink-blocked parameter names, its warnings. It is **sealed at run end** and it
  is what bundle writers receive. Run-scoped artifacts stay run-scoped; only
  recorder coverage is session-wide.

Two invariants, because there are two kinds of sink:

| Sink kind | Invariant |
|---|---|
| Streaming (`recorder.py` x4) | A live ledger is **installed**. Asserted at install, not at write. |
| Bundle write | A **sealed** `RunLedgerView` was supplied. |

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

### Bounding the hot path

A session-scoped set that only grows would put unbounded regex work on
`Recorder.record`, which is synchronous and runs on the single event loop that
owns every live browser session.

- **Deduplicate.** The set is keyed by value, so re-running the same macro
  contributes nothing. r5's stacking made the cost O(runs); this makes it
  O(distinct qualifying values).
- **Compile once.** `_serialized_variants` output is built per value at insert
  and cached with it, not rebuilt per `record()`.
- **Cap it.** `MAX_SCRUB_VALUES` bounds the set. On overflow the ledger stops
  accepting values, sets `scrub_saturated`, and the run result carries a warning:
  degraded to key-level redaction is a state an operator must be told about, not
  a silent ceiling.

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

### Read paths do not write

r5 proposed that a manifest without a `privacy_classifier_version >= 3` stamp be
redacted and rewritten on load, then trusted. That turns `macro_artifact_list`,
`macro_artifact_status` and `macro_artifact_critical_points_get` -- read-only
tools -- into writers of durable state, with no stated behaviour when the write
fails, no copy of the original, and a self-asserted stamp as the only gate.

Split it: **reads redact, and only redact.** `_compact_manifest`
(`artifacts.py:394`) keeps its read-side `redact_args`, which is the floor
protecting every manifest written before Part A. A separate explicit
`macro_artifact_migrate` performs the one-time rewrite, reports what it changed,
and is the only thing that writes. `reports.py:27` stays until the gate proves
every caller pre-redacts.

### `parameter_specs` must survive a re-save, without a lost-update race

`save_macro` composes a fresh dict (`storage.py:92-99`) and carries no key it
does not name, so a re-record would silently drop `{"display": {"sensitive":
true}}`. `write_macro` deep-copies (`storage.py:144`) and does not.

Carrying specs forward means read-modify-write, which r5 specified as if there
were a single writer in a document that elsewhere notes `macro_save` and
`repair_apply` are concurrent MCP calls. So the carry-forward is
**compare-and-set**: the existing file is read once (the collision guard already
reads it, `storage.py:72-76`), its `updated_at` is captured, and the write
refuses if the value on disk changed in between. A refused save is an error the
author sees, not a spec silently lost to the other writer.

### Capture decouples from redaction

`ResolvedPrivacy.capture_blocked`, decided by its own rule (a CREDENTIAL-tier
value is present), replaces the truthiness tests at `artifacts.py:301` and
`execution.py:291`. `artifacts.py:301` also does `path.unlink(missing_ok=True)`
inside a directory created empty with `mkdir(exist_ok=False)`
(`artifacts/paths.py:53`) -- it can never remove anything. Replace it with a
`screenshot_suppressed` evidence record.

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

## The gate, inverted -- and scoped

A rule that enumerates redaction calls and checks each receives a ledger is
satisfiable by **deleting a redaction call**. The gate therefore enumerates
**durable-write sinks** (inventory B, by write shape) and requires each to carry
the invariant its category names: a sealed `RunLedgerView` for bundle writes, an
installed ledger for streaming sinks. Deleting redaction does not make that rule
greener.

The review argued for cutting the derived gate entirely in favour of contract
tests on the privacy outputs, on the grounds that a repository-wide write-shape
inventory is broader than this feature. Half-accepted. The scan is **scoped to
Part 0 packages** (`macros/`, `artifacts/`, `recorder.py`) so it constrains the
code this design owns and not the whole tree, and the derivation script keeps
printing the out-of-scope counts so narrowing it further is a visible diff. The
generic part is kept deliberately: it is the only rule that survives someone
deleting the thing it checks, and that is the failure r1-r5 kept reproducing by
hand.

Contract tests on the outputs are added alongside, not instead -- they catch a
wrong value, which the gate cannot, and the gate catches a missing call site,
which they cannot.

Part A's `tests/fixtures/privacy_classifier_baseline.json` is this idea for
vocabulary; this is it for threading.

## Testing

- Exactly one `SensitiveRecorder` wraps a session no matter how many runs,
  sequence steps or nested calls execute: after N runs, `session.recorder` is
  wrapped once and the ledger holds the union (#234).
- A value first seen in step 1 is still scrubbed from step 2's page-derived rows
  -- `get_text_by`, console, `navigate` URL, websocket preview -- which is the
  coverage r5's restore would have removed.
- A two-level macro whose **inner** macro holds the credential produces a JSONL
  with no cleartext (#235), and the nested resolve appends to the ledger the
  wrapper already holds rather than installing another.
- A credential **literal** inside a nested macro definition is caught; a nested
  credential fed by an unclassified outer name is caught. Both measure zero in
  the corpus and are the shapes that turn measurement F live.
- The artifact path resolves once: `run_macro_artifact` and the `run_macro` it
  calls share one ledger, and the bundle sees nested resolves.
- A value failing the shape guard never enters the substring pass, gets
  key-level redaction, and is reported by lint.
- `MAX_SCRUB_VALUES` overflow sets `scrub_saturated` and surfaces a warning in
  the run result.
- Bundle writes with an unsealed or zero-resolve view raise `privacy_unresolved`.
- Re-saving a macro preserves `parameter_specs`; a concurrent rewrite between
  read and write is refused rather than silently dropping specs.
- A read-only artifact tool never writes; `macro_artifact_migrate` does, and
  reports what it changed.
- `run_sequence` with `stop_on_failure=False` and a missing macro returns the
  other steps.
- Widening does not newly refuse a corpus macro in a sink -- before/after over
  the real corpus, the check omitted for Part A and caught in peer review.
- `session-sign-in` marked via `parameter_specs` takes effect for all 81 callers.
- Marking a non-credential parameter does not suppress screenshots.

## Risks

**The signature budget, derived from inventory B rather than asserted**: four
bundle writers swap a tuple for a sealed view (`reports.py:58,59,64,70`), three
take one they do not have (`reports.py:29`, `reports.py:94`,
`script_export.py:353`), five boundaries in inventory A need a parameter
(`models.py:38`, `reports.py:27`, `reports.py:131`, `artifacts.py:394`,
`execution.py:215`), and `run_macro`/`_run_macro_impl` take an optional ledger.
The four streaming sinks need no signature change at all -- they are served by
the wrapper. r5 costed twelve from inventory A while gating over inventory B;
this is thirteen plus two optional parameters, counted from the inventory the
gate actually runs on.

**Session scope is a deliberate trade.** A credential from run 1 is scrubbed out
of run 40's recording on the same session. That is protection, and it is also
why the shape guard is load-bearing: without it, session scope would eventually
rewrite unrelated text. The guard, not the lifetime, is what keeps this safe.

**A saturated ledger degrades quietly if the warning is ignored.** The cap is a
real ceiling, not a soft limit, and an operator who does not read warnings gets
key-level redaction without knowing.

**Corpus shape is one machine's.** 340 macros, the sign-in concentration, and
every zero in measurement F are from this checkout.

## Open items

1. `MIN_SCRUB_LEN`, `MAX_SCRUB_VALUES`, and the common-word list. All three are
   now load-bearing rather than cosmetic.
2. Whether `lint_urls` migrates off `artifacts/redaction.py` here or later; Part A
   kept that module alive deliberately.
3. The authoring-time literal scan for `save_macro`/`write_macro`: severity (warn
   versus refuse) and whether it runs in `macro_lint` instead.
4. `run_sequence` with the default `stop_on_failure=True` discards accumulated
   step records on the re-raise, so an operator loses the record of which steps
   already mutated the browser. Pre-existing and out of scope for privacy, but it
   is the reason a privacy test cannot be written against the default path.

## Review provenance

- r1 `run-20260911T174636-f6cafbbc` -- not converged, 14 upheld
- r2 `run-20260911T185536-055a7916` -- converged, 13 upheld, 1 deadlocked
- r3 `run-20260911T204026-c1bddcab` -- converged, 12 upheld, 0 deadlocked
- r4 `run-20260911T221508-a3242176` -- not converged, 12 upheld, 1 refuted, 1
  deadlocked, 1 unproven
- r5 `run-20260911T230447-2a443ad4` -- not converged, 11 upheld, 1 refuted, 1
  deadlocked, 1 unproven

r5 findings addressed: `c-0001` (session scope replaces the per-run restore),
`c-0002` (compare-and-set carry-forward), `c-0003` (one resolve per invocation,
ledger passed into `run_macro`), `c-0004` (no restore, so no post-run window),
`c-0005` (two invariants: installed for streaming sinks, sealed for bundle
writes), `c-0006` (refuted), `c-0007` (five sink categories, post-hoc rewriters
named), `c-0008` (budget derived from inventory B, with the "holds policy"
column), `c-0009` (`stop_on_failure` default stated; test corrected; gap in open
items), `c-0010` (divergence designed out; digest kept for provenance only),
`c-0011` (reads redact, `macro_artifact_migrate` writes), `c-0012` (dedup,
compile-once, cap), `c-0013` (`PersistedLedger` cut), `c-0014` (gate scoped to
Part 0 packages, contract tests added alongside).

Tracked as issues from this work: livingstaccato/octowright#234 (recorder
wrappers stack and are never uninstalled) and #235 (nested `macro_call` args are
never collected). They are one design, not two fixes -- see "Scope: the ledger
belongs to the session, not the run".
