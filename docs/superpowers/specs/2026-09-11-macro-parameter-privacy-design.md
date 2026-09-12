# Macro parameter privacy: Parts 0 and B, derived

Date: 2026-09-11
Status: design, revision 5. Not approved.

**Part A shipped separately** as `c4d96753` and is out of scope here. What
remains is Part 0 (threading) and Part B (`parameter_specs`).

## Why this revision reads differently

| Revision | Claimed | Actual | Review |
|---|---|---|---|
| r1 | a resolver and a schema | never said how the value reaches the call sites | not converged, 14 upheld |
| r2 | 2 resolve sites, 2 consumers | 3 sites, 6 consumers | converged, 13 upheld, 1 deadlocked |
| r3 | 3 sites, 6 consumers, "purely additive" | a 4th redaction site; the union claim was false | converged, 12 upheld, 0 deadlocked |
| r4 | every table AST-derived | tables were derived; two filters bounding them were not disclosed, and the conclusion drawn from measurement E was false | not converged, 12 upheld, 1 refuted, 1 deadlocked |

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

### B. Durable writes -- 15 in scope, enumerated by write shape

```
src/octowright/artifacts/reports.py:21           _json_write               atomic_write_text  (helper)
src/octowright/artifacts/reports.py:29           write_artifact_manifest   _json_write        (helper)
src/octowright/artifacts/reports.py:58           write_run_bundle          _json_write        (helper)
src/octowright/artifacts/reports.py:59           write_run_bundle          _json_write        (helper)
src/octowright/artifacts/reports.py:64           write_run_bundle          _json_write        (helper)
src/octowright/artifacts/reports.py:70           write_run_bundle          atomic_write_text  (helper)
src/octowright/artifacts/reports.py:94           refresh_run_summary       atomic_write_text  (helper)
src/octowright/artifacts/script_export.py:353    write_macro_cli           atomic_write_text  (helper)
src/octowright/macros/artifacts.py:527           macro_artifact_verify     atomic_write_text  (helper)
src/octowright/macros/storage.py:103             save_macro                atomic_write_text  (helper)
src/octowright/macros/storage.py:169             write_macro               atomic_write_text  (helper)
src/octowright/recorder.py:119                   __init__                  open               (open for write)
src/octowright/recorder.py:157                   record                    write              (raw handle)
src/octowright/recorder.py:179                   record_control            write              (raw handle)
src/octowright/recorder.py:193                   _write_truncation_marker  write              (raw handle)

total: 59   in Part 0 scope: 15   not via a write helper: 20
```

These split into two categories with **different mechanisms**, which r4's flat
"all eleven, with the ledger" concealed:

- **Run-scoped (13)** -- a ledger exists. `reports.py` x7, `script_export.py:353`,
  `macros/artifacts.py:527`, `recorder.py` x4.
- **Authoring-time (2)** -- `save_macro` (`storage.py:103`) and `write_macro`
  (`storage.py:169`) run inside `macro_save`/`repair_apply` with no session, no
  args and no run. No ledger can exist. Their exposure is a credential typed
  during recording that was never declared a parameter, so it stays literal in
  the macro JSON. That needs a save-time literal scan, not a ledger; stamping a
  run-time privacy marker into a macro definition would put `privacy_unverified`
  on ordinary authoring output forever.

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

### The ledger has two forms, and only one may be written

`sensitive_arg_values` returns **cleartext** -- `_scrub_text` needs the literal to
build `_serialized_variants` and substitute it. An in-memory ledger accumulating
those values is correct. Persisting it is not: `result.json` under the recordings
tree would then carry the production password on the happy path, for every one of
the 81 `session-sign-in` callers, written deliberately by the mechanism meant to
prevent leaks.

- `PrivacyLedger` -- in-memory only. Holds scrub values. No `__json__`, no
  `asdict`, not a dataclass that serialises by default.
- `PrivacyLedger.persistable() -> PersistedLedger` -- counts, classifier version,
  sink-blocked parameter **names**, warnings, `resolved_sites`, `sealed`, and
  salted digests if a later tripwire needs absence-checking. No values.

`macro_artifact_verify`'s in-process caller (`artifacts.py:249`) already has the
live ledger in scope and needs no disk round-trip at all.

### A green tripwire must not mean "policy never arrived"

The tripwire recognises a credential by matching the ledger's values. If a
resolve site is never reached the ledger is empty, zero values are matched, and
the write looks clean -- so "no tripwire" means either "nothing leaked" or "the
policy never arrived", and an operator cannot tell which. The static gate does
not close this: it proves a boundary *receives* a ledger, not that one was
*populated*.

Therefore the ledger carries `resolved_sites: int` and `sealed: bool`. Every
run-scoped write asserts sealed; an unsealed or zero-resolve ledger arriving at a
durable write is itself an event (`privacy_unresolved`), not a pass.

`conditional.dispatch_conditional` (`execution.py:294-306`) re-enters
`_dispatch_one` with the parent's values and appends nothing -- it is in the
resolve-site inventory for exactly this reason.

### The recorder is the sink, and it is currently frozen

`install_sensitive_recorder` (`execution.py:544`) captures a tuple built from the
*outer* args at `execution.py:543`. `SensitiveRecorder` holds it immutably
(`privacy.py:247-249`). Per measurement B the recorder is a durable write; per
measurement F the gap is latent today. Part B must not make it live: the recorder
takes a **reference to the mutable ledger**, so a nested resolve tightens the
recording sink immediately.

### Recorder lifetime -- r4 open item 2, decided

`session.recorder = SensitiveRecorder(...)` has no uninstall anywhere in the
tree: no restore, no context manager, and `_run_macro_impl`'s `finally` does not
unwrap. `run_sequence` loops `run_macro` per step, so an N-step sequence leaves N
nested wrappers on a long-lived session, each holding a previous step's cleartext
and each running its own `_serialized_variants` pass per `record()`.

Decision: **one ledger per step; the recorder wrapper is per-run and restored.**
Install it as a context manager around the run, restoring `session.recorder` in
`finally`, with a test asserting the attribute is the original `Recorder` after
`run_macro` returns. Cross-step scrubbing of recordings is corruption, not
caution: step 2's output rewritten with step 1's values destroys the replay log
an operator is reading.

Note for the release in flight: Part A widened the vocabulary, so more values now
enter that stacking. It does not create the bug; it enlarges its input.

### Keep a read-side floor

r4 removed redaction at `_compact_manifest` (`artifacts.py:394`, read) and
`write_artifact_manifest` (`reports.py:27`, write) in the same change. Manifests
written before Part A contain exactly what `privacy.py:20-30` records as fact --
`private_key`, `cookie`, `set_cookie`, `otp` and plurals in cleartext -- and
`_compact_manifest` is what masks them today on the path to `macro_artifact_list`,
`macro_artifact_status` and `macro_artifact_critical_points_get`.

Replace, do not remove: on load, a manifest without a
`privacy_classifier_version >= 3` stamp is redacted and rewritten once, then
trusted. Keep `reports.py:27` until the gate proves every caller pre-redacts.

### Resolve from the dict that executes

`load_macro` is an uncached disk read and both `save_macro` and `write_macro`
rewrite the same path atomically as concurrent MCP calls. Resolving policy from
one read and executing from another lets a mid-flight rewrite separate the
enforced policy from the executed macro.

The judges split on the scope of this, and they were right to: `calls.py:54`
already holds one loaded `called` dict and `calls.py:55` is in-memory, so nested
calls introduce no second read. Only a sequence-level pre-resolve does. So:
**pass the loaded dict, never the name, to the resolver**, and record the macro's
`updated_at` (or `digest_macro`, `artifacts/digest.py`) in the ledger so a
mid-flight rewrite is detectable afterwards.

### `run_sequence`: keep the fallback, narrow it

The r4 text -- resolve before the `try`, fall back to heuristic-only if it raises
-- does not abort a sequence, because the fallback catches the raise and
`run_macro` fails inside the existing handler. That claim was refuted correctly.

The fallback is still too wide: it swallows `FileNotFoundError` and
`JSONDecodeError` from a missing or truncated macro into "use heuristics". Only
spec-shape errors fall back. A macro that cannot be loaded must reach
`run_macro`'s handler and become a recorded failed step honouring
`stop_on_failure`.

### `parameter_specs` must survive a re-save

`save_macro` composes a fresh dict (`storage.py:92-99`) and writes it; it carries
no key it does not name. `write_macro` deep-copies (`storage.py:144`), so specs
survive there and not in the tool an author actually calls. Re-recording a macro
to fix a selector would silently drop `{"display": {"sensitive": true}}` -- the
non-heuristic case the feature exists for, with no warning and no diff.

`save_macro` already reads the existing file for its collision guard
(`storage.py:72-76`). Carry `parameter_specs` forward from it unless the caller
supplies a new one, test it, and make lint warn when a macro's resolved
`sensitive_parameters` shrinks against the previous version on disk.

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
to the ledger.

Sink guard is **one-directional**: `sensitive: true` tightens, `sensitive: false`
never loosens. Only `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` unblocks a sink; an
author-supplied file must not. An ignored unmark warns in lint and in the run
result.

A marked value enters the substring scrub only behind a shape guard: `str` leaves
only, minimum length, not a common word. Values failing it get key-level
redaction, and lint reports the exclusion.

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

## The gate, inverted

A rule that enumerates redaction calls and checks each receives a ledger is
satisfiable by **deleting a redaction call**. The gate therefore enumerates
**durable-write sinks first** (inventory B, by write shape) and requires each
run-scoped sink to receive a sealed ledger. Deleting redaction does not make that
rule greener; removing a sink does, and removing a sink is the safe direction.

Boundary threading (inventory A) stays a secondary check. The scope decision --
`macros/`, `artifacts/`, `recorder.py` in; `session/`, `http/`, `personas.py` out
-- is printed by the derivation script with per-package counts, so narrowing it later is a
visible diff rather than an unstated filter.

Part A's `tests/fixtures/privacy_classifier_baseline.json` is this idea for
vocabulary; this is it for threading.

## Testing

- Ledger accumulates across `macro_call` and is visible to the outer bundle,
  against a real two-level macro.
- A two-level macro whose **inner** macro holds the credential produces a JSONL
  with no cleartext -- the live form of the latent gap in measurement F.
- A credential **literal** inside a nested macro definition is caught; a nested
  credential fed by an unclassified outer name is caught. Both measure zero in
  the corpus and are the shapes that turn F live.
- `session-sign-in` marked via `parameter_specs` takes effect for all 81 callers.
- Re-saving a macro preserves `parameter_specs`.
- `session.recorder` is the original `Recorder` instance after `run_macro`
  returns; an N-step sequence leaves no wrapper behind.
- Every run-scoped inventory-B sink receives a sealed ledger (the derived gate);
  an unsealed ledger at a sink raises `privacy_unresolved`.
- A pre-Part-A manifest without a classifier stamp is redacted on first load.
- Widening does not newly refuse a corpus macro in a sink -- before/after over the
  real corpus, the check omitted for Part A and caught in peer review.
- `run_sequence` with a missing macro still returns the other steps.
- Marking a non-credential parameter does not suppress screenshots.

## Risks

**Twelve signature changes** across `artifacts/` and `macros/`: five new
parameters and seven tuple-to-ledger type changes. The inverted gate is what
keeps that honest.

**The recorder reference makes scrubbing live-mutable.** A ledger that grows
mid-run changes what later `record()` calls scrub. That is the point, and it means
the recording is not uniformly scrubbed across a run: actions written before a
nested resolve saw a smaller value set. Values are per-run constants here, so this
only matters for a credential first seen at depth 2, which the inner-macro test
covers.

**Corpus shape is one machine's.** 340 macros, the sign-in concentration, and
every zero in measurement F are from this checkout.

## Open items

1. `MIN_SCRUB_LEN` and the common-word list.
2. Whether `lint_urls` migrates off `artifacts/redaction.py` here or later; Part A
   kept that module alive deliberately.
3. The authoring-time literal scan for `save_macro`/`write_macro`: severity (warn
   versus refuse) and whether it runs in `macro_lint` instead.

## Review provenance

- r1 `run-20260911T174636-f6cafbbc` -- not converged, 14 upheld
- r2 `run-20260911T185536-055a7916` -- converged, 13 upheld, 1 deadlocked
- r3 `run-20260911T204026-c1bddcab` -- converged, 12 upheld, 0 deadlocked
- r4 `run-20260911T221508-a3242176` -- not converged, 12 upheld, 1 refuted, 1
  deadlocked, 1 unproven

r4 findings addressed: `c-0001` (measurement E corrected, ledger re-justified),
`c-0002` (`PersistedLedger`), `c-0003` (`sealed`/`resolved_sites`,
`privacy_unresolved`, conditional recursion in the inventory), `c-0004` (recorder
in inventory B and wired to the ledger), `c-0005` (recorder lifetime, open item 2
decided), `c-0006` (read-side floor kept with a migration stamp), `c-0007`
(`save_macro` preserves specs), `c-0008` (resolve from the loaded dict, digest
recorded), `c-0009` (refuted; fallback narrowed anyway), `c-0010` (derived
surface, sinks-first gate, scope printed), `c-0011` (inventory B split
run-scoped/authoring-time), `c-0012` (derivation script committed), `c-0013` (knobs cut),
`c-0014` (single published shape), `c-0015` (counter cut).
