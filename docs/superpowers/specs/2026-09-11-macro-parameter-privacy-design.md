# Macro parameter privacy: one resolver, composed per invocation

Date: 2026-09-11
Status: design, revision 3. Not approved.

Revision history, because each revision failed in a way worth recording:

- **r1** — specified a resolver and a schema, and never said how the value
  reaches the redaction call sites. `parameter_specs` would have silently
  no-opped. Review: not converged, 14 upheld.
- **r2** — added threading, then named **two** resolve sites when there are
  three, and **two** policy consumers when there are six. Same defect class,
  one layer down: Part B would have no-opped for any macro reached via
  `macro_call`. Review: converged, 13 upheld, 1 deadlocked.
- **r3** — this revision. Resolves per *invocation* rather than per run,
  enumerates every site against the code, and corrects three claims r2 asserted
  that were false.

Review provenance is recorded at the end.

## Scope

- **Part 0** — resolve per macro invocation; compose policies down the call
  chain; writers stop redacting; a tripwire covers what they stopped guarding.
- **Part A** — one vocabulary with **per-token match modes**, replacing three
  classifiers that disagree.
- **Part B** — `parameter_specs`, a per-macro override.

The code this targets **shipped in 0.23.0** (`cd507ef7`). This is no longer a
candidate branch; changes here alter released behaviour.

## Problem

### Three classifiers, disagreeing

Sensitivity is inferred from the parameter name by three implementations that
disagree on 20 of 30 sampled names:

| Definition | Location | Gates |
|---|---|---|
| `is_sensitive_key` | `artifacts/redaction.py:14-39` | manifests, run results, **and URL linting** |
| `is_sensitive_arg_key` | `macros/privacy.py:19-57` | `args_used`, exports, the value scrub |
| `is_credential_arg` | `macros/substitution.py:104-107` | `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` |

Measured holes: `private_key`, `cookie`, `set_cookie` are caught by
`redaction` and **missed by `privacy`**; `otp` is known only to `substitution`;
`passphrase`, `pw`, `pwd`, `authorization`, `access_key` are **not**
sink-blocked, so `{{passphrase}}` may expand into a URL while `{{password}}` is
refused; plural forms bypass all three.

### They also disagree about *how* they match

This is the correction r2 got wrong. `redaction.py` matches **by substring**;
`privacy.py` matches **by token**. Measured on main:

```
supersecretkey   redaction=True   privacy=False
userpassword     redaction=True   privacy=False
apitoken         redaction=True   privacy=False
mysecretvalue    redaction=True   privacy=False
private_key      redaction=True   privacy=False
cookie/cookies   redaction=True   privacy=False
```

r2 claimed Part A was "purely additive — nothing that is redacted today stops
being redacted." **That was false.** Unifying on token matching drops every
name above.

`redaction.py` already encodes the right design, and Part A adopts it: long
unambiguous tokens match by substring (`password`, `secret`, `token`,
`credential`, `private_key`, …), short ambiguous ones match exactly (`auth`,
`pw`, `pwd`, `cookie`, `email`, `username`). That split is why `author` and
`authority` do **not** match while `secretary` and `tokenizer` do — the latter
being over-claim, which is the safe direction.

### Redaction is applied more than once, by different rules

```
macros/artifacts.py:344   redact_args(args_used)      privacy vocabulary
artifacts/models.py:38    redact_mapping(parameters)  redaction vocabulary
artifacts/reports.py:27   redact_mapping(...)  again  redaction vocabulary
```

The last two run after the first and cannot see an author's declaration. Three
passes with two vocabularies is not defence in depth — applying a *different*
policy twice means the last one wins.

### One durable write is unredacted today

`macros/artifacts.py:527` writes `verification.json` through a locally-imported
`atomic_write_text`, bypassing `_json_write` and every redactor. So does the
summary markdown at `reports.py:70` and `refresh_run_summary` at `:94`.

### The case no heuristic can catch

A secret an author puts under `display` is never classified.

## Decisions

| Decision | Chosen | Rejected, and why |
|---|---|---|
| Resolve granularity | Per macro **invocation**, composing down the chain | Per run cannot see a nested macro's `parameter_specs`, so Part B no-ops under `macro_call` |
| Match mode | **Per token**: substring \| token \| exact | Per tier; token-only loses `supersecretkey`-shaped names, substring-only matches `user` inside `browser` |
| Writer-side redaction | Removed; writers that don't own `parameters` don't touch it | Threading specs into all three redactors entrenches passes that agree today and drift tomorrow |
| Tripwire on failure | Scrub, write, and mark the artifact | Refusing destroys the evidence bundle that would explain the run |
| Tripwire configurability | **None** | It fires only on *our* bug. It is an assertion, not policy — no deployment legitimately wants less signal about a leak, and CI asserts on the marker instead |
| Screenshot suppression | Its own `capture_blocked` decision | Branching on scrub-tuple truthiness makes marking a parameter silently disable evidence capture |
| Where the declaration lives | Sibling key beside `parameters` | Changing `parameters` to a map breaks six readers and collides with `normalise_parameters` |
| Sink guard | One-directional, loud | An author-supplied data file must not unblock an operator-controlled security control |
| Mark → value scrub | Yes, behind a shape guard | Unguarded, a marked value of `main`/`admin` destroys the failure bundle |

## Part 0 — resolve per invocation, compose, tripwire

### The policy object

```python
@dataclass(frozen=True)
class ResolvedPrivacy:
    verdicts: Mapping[str, bool]  # resolved, per arg name at this level
    scrub_values: tuple[str, ...]  # values that cleared the shape guard
    sink_blocked: frozenset[str]  # names the sink guard refuses
    capture_blocked: bool  # a CREDENTIAL-tier value is present
    marker: str
    warnings: tuple[str, ...]

    def compose(self, inner: ResolvedPrivacy) -> ResolvedPrivacy: ...
```

`compose` unions `scrub_values`, `sink_blocked`, `capture_blocked` and
`warnings`; `verdicts` are per-level and do not leak outward. A secret revealed
inside a nested macro must stay scrubbed in the **outer** failure bundle, so
composition accumulates and never narrows.

### Three resolve sites, not two

| Site | Why it resolves |
|---|---|
| `execution._run_macro_impl:541` | top-level `macro_run` |
| `artifacts.run_macro_artifact:158` | the durable-artifact path |
| `calls.dispatch_macro_call:54` | **nested macros** — it already calls `load_macro(called_name)` on this line, so the nested document (hence its `parameter_specs`) is in scope exactly where it is needed |

r2 named only the first two. `_dispatch_one` already threads `sensitive_values`
into the `macro_call` closure (`execution.py:264-268`), so the plumbing exists;
what was missing is consulting the *called* macro's own specs. The nested
policy is composed into the one already in flight and passed down.

### `run_sequence` resolves before the `try`

`execution.py:657-672` redacts `step_args` inside an `except` handler. A policy
cannot be built there, because the `load_macro` needed to build it is itself
what may have raised. Resolve per step **before** the `try`; if that resolve
fails, fall back to a heuristic-only policy (no specs, no values) so the error
path always has something to redact with.

### Writers stop redacting — and stop rewriting what they don't own

`redact_mapping` is removed from `models.new_manifest:38` and
`reports.write_artifact_manifest:27`; `write_run_bundle:47` stops calling
`redact_args`.

r2 said "thread a policy to all six `write_artifact_manifest` callers" and had
no answer for the four with nothing to thread. The real resolution is
ownership — only two paths *set* `parameters`:

| Caller | Sets | Policy in scope |
|---|---|---|
| `artifacts.py:65`, `:136`, `:176` | `parameters`, via `_manifest_for_plan` | yes |
| `artifacts.py:243` | `latest_run` | not needed |
| `artifacts.py:482`, `:530` | `critical_points` | not needed |

The latter three carry `parameters` through from `_merge_existing_manifest`
untouched. They must **preserve that block byte-for-byte** rather than
re-serialising it through any redactor. Then there is nothing for a tripwire to
check on those paths, and the "unarmed tripwire" problem dissolves instead of
being papered over.

### The tripwire

`assert_no_sensitive(payload, policy)` re-scrubs with **the same policy**. If
anything changed it writes the scrubbed payload, logs at error level,
increments `octowright_privacy_tripwire_total`, and sets `privacy_tripwire:
true` in the written artifact.

Same policy, so it cannot disagree. Idempotent, so it cannot corrupt. Marked,
so the degradation is visible to whoever opens the bundle rather than only to
whoever reads the daemon log.

**Not configurable, deliberately.** It fires only when our threading has a bug.
Configuring it would configure how loudly we are told about a defect. CI gets
its loud failure from the marker — `make ci` fails when `privacy_tripwire` is
true in any artifact or the counter is non-zero — which is a CI assertion
expressed in CI, not a runtime branch that makes production and test differ.

**Coverage.** r2 claimed "golden/capture writes share `reports._json_write`".
There are none; that was wrong. The actual durable writes are:

| Write | Path | Today |
|---|---|---|
| artifact manifest | `reports.py:25` → `_json_write` | redacted (being removed) |
| run result, evidence, checks | `reports.py:58,59,64` → `_json_write` | redacted (being removed) |
| summary markdown | `reports.py:70` `atomic_write_text` | scrubbed via `summary` arg |
| refreshed summary | `reports.py:94` `atomic_write_text` | **bypasses `_json_write`** |
| `verification.json` | `artifacts.py:527` local-import `atomic_write_text` | **unredacted today** |

The tripwire covers all five. The last two are pre-existing leaks this design
closes rather than introduces.

## Part A — unified vocabulary, per-token match modes

One table in `macros/privacy.py`. Each token declares its match mode.

**CREDENTIAL** — sink guard, capture blocking, and redaction act on this tier.

| Mode | Tokens |
|---|---|
| substring | `password`, `passwd`, `passphrase`, `secret`, `token`, `authorization`, `credential`, `private_key`, `api_key`, `apikey`, `access_key` |
| exact | `pw`, `pwd`, `auth`, `otp`, `bearer`, `cookie`, `cookies`, `set_cookie` |

**IDENTITY** — redaction only.

| Mode | Tokens |
|---|---|
| substring | — |
| exact | `email`, `username`, `phone` |

**CONTEXTUAL** — redaction only.

| Mode | Tokens |
|---|---|
| token | `user`, `peer`, `subject`, `session`, `contact` |

`user` is token-matched, never substring, because `browser` contains it.
Pairs (`api`+`key`, `access`+`key`, `session`+`id`) keep the existing
`SENSITIVE_KEY_PAIRS` mechanism.

Plurals: match the written token first and treat a depluralized form as an
*additional* candidate, never a replacement. Several tokens already end in `s`
without being plural — `access`, `address`, `pass`, `process` — and for
`access` a naive strip destroys the `access`+`key` pair, silently
un-classifying `access_key`. Pairs depluralize both elements independently,
since `api_keys` tokenizes to `('api', 'keys')`.

**The invariant, stated correctly this time:** no name classified sensitive by
*either* `redaction.is_sensitive_key` *or* `privacy.is_sensitive_arg_key` today
may become insensitive. A test asserts this over the union of both, plus the
names measured above. "Purely additive" was r2's false claim; this is the
checkable version of it.

`macros/substitution.py` imports the CREDENTIAL tier instead of owning
`_CREDENTIAL_ARG_RE`.

**`artifacts/redaction.py` is not orphaned.** r2 said it "loses its only
callers"; that was false. `macros/lint_urls.py:58` imports `is_sensitive_key`
**and `_NON_ALNUM`**, using them at `:151` and `:264`, and its own docstring at
`:45` says it deliberately reuses that helper "rather than a fourth" classifier.
`lint_urls` migrates to the unified resolver as part of this change; the module
is removed only once that import is gone. Its `_URL_PARAM_SECRET_NAMES`
supplement (`:68-73`) folds into the unified table with its existing
IDENTITY/secret distinction preserved.

## Part B — `parameter_specs`

```json
{
  "parameters": ["email", "password", "display", "user_count"],
  "parameter_specs": {
    "display":    {"sensitive": true},
    "user_count": {"sensitive": false}
  }
}
```

`dict[str, dict[str, Any]]`, optional. Only `sensitive: bool` is recognised.

### Resolution

`parameter_specs[name]["sensitive"]` if present and a real `bool`, else the
Part A heuristic. Top-level names only; sensitivity inherits down a branch.
**No path syntax; a nested leaf cannot be unmarked.**

Undeclared args still hit the heuristic — specs override per-name, never
replace, because substitution is placeholder-driven and an arg can exist
without being declared.

### Sink guard: one-directional

`sensitive: true` **tightens**; `sensitive: false` **never loosens**. Only
`OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` can unblock a sink. An unmark on a
CREDENTIAL name that would have mattered to the sink guard gets its own lint
finding and run warning: honoured for redaction, ignored for the sink guard.

### Screenshots decouple from the scrub tuple

Today `execution.py:291` reroutes screenshots on `if ... and sensitive_values:`
and `artifacts.py:301-307` suppresses automatic ones the same way — so marking
any parameter whose value clears the shape guard would silently disable
evidence capture, via a `path.unlink(missing_ok=True)` that cannot fire
because `next_run_dir` just created the directory empty.

`ResolvedPrivacy.capture_blocked` is decided by its own rule — a CREDENTIAL-tier
value is present — not by whether the scrub tuple is non-empty. Both sites
branch on that instead. Suppression records a `screenshot_suppressed` evidence
entry and surfaces in the run result; the no-op unlink is removed.

### Mark → value scrub, behind a shape guard

A marked value is redacted structurally **and** added to the substring scrub,
but only if it clears the guard:

- `str` leaves only. `{"user_id": 1}` currently contributes `"1"` and
  `{"session_active": True}` contributes `"True"`, which at exactly
  `_WORD_BOUNDED_BELOW = 4` substitutes unanchored anywhere.
- Minimum length `MIN_SCRUB_LEN`.
- Not in a small common-word list.

Values failing the guard get key-level redaction only; `macro_lint` reports
which were excluded and why.

### `parameters` derived from the actions

The `{{placeholder}}` set is the truth — it is what `_substitute_value:131`
resolves against. `script_export` generates CLI flags from that set, so an
omitted parameter can no longer produce a flagless script that crashes on
`KeyError`. `macro_lint` reports drift, and an orphan `parameter_specs` key
becomes checkable against the truth.

### Behaviour matrix

| Case | Behaviour |
|---|---|
| No `parameter_specs` | Heuristic for every parameter |
| Name absent from it | Heuristic for that name |
| Spec names something outside the derived placeholder set | Orphan; lint finding |
| Arg passed at runtime, declared nowhere | Heuristic |
| `sensitive` not a bool | Ignored; lint finding |
| `parameter_specs` a list, string, or null | Treated as absent; lint finding; `_coerce_parameter_specs` raises in strict mode |
| Unknown field inside a spec | Per `OCTOWRIGHT_PARAMETER_SPEC_UNKNOWN`; lint reports either way |
| Marked value fails the shape guard | Key-level redaction only; lint reports the exclusion |
| Unmark on a CREDENTIAL name | Honoured for redaction, ignored by the sink guard, warned in lint and the run result |
| Nested macro with its own specs | Resolved at `dispatch_macro_call` and composed into the in-flight policy |

## Published contract

| Surface | Carries |
|---|---|
| Macro document | `parameter_specs` |
| `MacroDetail` + `MCP-SHARED-CONTRACT.md` | `parameter_specs`, declared explicitly |
| `MacroListEntry` / `types.ts MacroSummary` | `sensitive_parameters: list[str]` — the resolved set |
| `MacroRunResult` | `warnings: NotRequired[list[str]]` (`total=True` today) |
| `MacroSequenceStep` | `warnings: list[str]` (already `total=False`, `mcp_types.py:71`) |
| **`run_macro_artifact` result** | `warnings` — r2 omitted this, so the signal never reached the durable-artifact path |
| `macro_lint` | every unmark, drift, orphan, scrub exclusion, and suppression |

`sensitive_parameters` covers **declared** parameters only; an undeclared
runtime arg cannot be precomputed, and the contract doc must say so.

## Configuration

| Var | Values | Default | Unparsable |
|---|---|---|---|
| `OCTOWRIGHT_PARAMETER_SPEC_WARN` | `credentials` \| `all` \| `off` | `credentials` | → default |
| `OCTOWRIGHT_PARAMETER_SPEC_UNKNOWN` | `preserve` \| `drop` | `preserve` | → default |

Both fall back to the default rather than off: a typo must not silently remove
a security warning nor destroy author data. Parsers live in `macros/privacy.py`
because `defaults.py` is at its LOC ceiling.

## Touch points

| File | Change |
|---|---|
| `macros/privacy.py` | per-token match table, `ResolvedPrivacy` + `compose`, `resolve_privacy`, shape guard, tripwire, both parsers |
| `macros/calls.py:54` | resolve the called macro's specs; compose; pass down |
| `macros/execution.py` | resolve at `:541`; thread `:67`, `:618`, `:668`; `capture_blocked` at `:291`; per-step resolve before the `try` at `:657`; scope the recorder wrap; populate `warnings` |
| `macros/artifacts.py` | resolve at `:158`; thread `:344`; `capture_blocked` + evidence record at `:301-307`; preserve `parameters` untouched at `:243`, `:482`, `:530`; tripwire the `verification.json` write at `:527` |
| `macros/substitution.py` | import the CREDENTIAL tier; drop `_CREDENTIAL_ARG_RE`; honour one-directional specs |
| `macros/lint_urls.py:58,151,264` | migrate off `artifacts/redaction.py` to the unified resolver |
| `artifacts/models.py:38`, `artifacts/reports.py:27,47` | **remove** redaction; call the tripwire at `:58,59,64,70,94` |
| `artifacts/redaction.py` | removed once `lint_urls` no longer imports it |
| `macros/storage.py:92,117` | `save_macro` carries the key; `list_macros` populates `sensitive_parameters` |
| `macros/dsl.py` | `_coerce_parameter_specs`; derive `parameters` from placeholders |
| `server/macros.py`, `macros/lint.py` | new param; new findings |
| `artifacts/script_export.py` | route `_safe_default` through the resolver; emit the match table; flags from placeholders; bump the classifier version |
| `mcp_types.py:51,62,71`, `types.ts`, `MCP-SHARED-CONTRACT.md`, `docs/env-vars.md` | contract + knobs |

`_safe_default` is named explicitly because it bakes literal values into the
generated `.py` on disk (`script_export.py:427-433` → `:390`) using the bare
heuristic — a marked `display` value would be written in cleartext into a file
that gets committed.

`ARG_PRIVACY_CLASSIFIER_VERSION` goes to 3, and the export manifest records it
plus a hash of `parameter_specs` so `macro_artifact_status` can report an
export as stale.

### Recorder scoping

`install_sensitive_recorder` (`privacy.py:202-205`, called from
`execution.py:544`) is never restored — the `finally` at `:603-612` only calls
`_finish_macro_run`. On a pooled session the wrappers stack once per run and
keep scrubbing later, unrelated recordings against a dead run's values. Scope
the wrap with a context manager restoring the prior recorder, and make the
installer idempotent: replace, never nest.

## Testing

TDD throughout.

- **Part 0**: policy resolved at each of the three sites; a nested macro's
  specs take effect; composition never narrows; `run_sequence` error path has a
  policy when `load_macro` raises; writers emit no redaction; the three
  non-owning manifest callers leave `parameters` byte-identical; tripwire
  fires on a deliberately un-threaded payload, writes the scrubbed artifact,
  sets the marker, and is silent otherwise.
- **Part A**: the union invariant — every name sensitive to *either* classifier
  today stays sensitive, including `supersecretkey`, `userpassword`, `apitoken`,
  `private_key`, `cookies`; `browser` and `author` stay insensitive; <!-- pragma: allowlist secret (key names under test, not credentials) -->
  plural handling does not break `access_key`.
- **Part B**: no specs; partial; orphan; non-bool; non-dict container; unknown
  field under both knob values; mark-then-sink-refused; unmark-does-not-unblock;
  nested inheritance; nested leaf cannot be unmarked; undeclared arg falls
  through.
- **Capture**: marking a non-credential parameter does **not** suppress
  screenshots; a CREDENTIAL value does, and records the evidence entry.
- **Shape guard**: non-string scalars never enter the scrub set; short and
  common-word marked values get key-level redaction only and are reported.
- **Export**: a marked value is never an argparse default; flags from
  placeholders; match table mirrored; parity with the runtime resolver.
- **Contract**: `sensitive_parameters` via `macro_list`; `warnings` in both the
  run result and the artifact-run result.

## Risks

**Part A is not purely additive — it is additive under a checked invariant.**
The union test is what makes that true rather than asserted. r2's unchecked
claim is exactly what the review caught.

**`lint_urls` migration** is in the critical path. URL secret linting changes
behaviour with the vocabulary, and it has its own supplement list that must
survive the merge.

**Writer removal.** An old manifest re-written through the merge path loses its
second scrub; the tripwire is the mitigation and needs a test on that path.

**Version skew.** An older daemon ignores `parameter_specs`: `sensitive: true`
is lost → silent under-redaction; `sensitive: false` is lost → over-redaction,
which is safe.

**Released code.** This targets 0.23.0, not a candidate. Anything that changes
existing redaction behaviour is a behaviour change to a shipped release.

## Deferred

- **`parameters` as a map** — only if the spec bag grows, as its own project.
- **Nested path syntax** for unmarking a leaf.
- **Rendered-value screenshot redaction** — `_dispatch_classified_screenshot`
  raises because no production handler exists. Building one is a project;
  `capture_blocked` makes the refusal correct and visible in the meantime.

## Open items

1. `MIN_SCRUB_LEN` value and the common-word list contents.
2. Whether `lint_urls`'s `_URL_PARAM_SECRET_NAMES` entries join CREDENTIAL or
   IDENTITY individually — it draws that distinction today and the merge must
   preserve it per name.
3. `ARG_PRIVACY_CLASSIFIER_VERSION` — the branch review argued for deleting it
   as machinery with no consumer; this spec bumps it and gives it one (export
   staleness). Confirm.

## Review provenance

- **r1**: `afriend-design/run-20260911T174636-f6cafbbc` — crossexam, **not
  converged**, 14 upheld, 1 deadlocked, 1 unjudged.
- **r2**: `afriend-design-r2/run-20260911T185536-055a7916` — crossexam,
  `--max-rounds 4`, **converged**, 13 upheld, 1 deadlocked.
- **branch**: `afriend-branch/run-20260911T174631-866e1228` — contributed the
  non-string-scalar scrub defect and the plural-form gap.

Independent friends `codex` and `agy`; `claude` advisory host self-review,
excluded from settlement.

r2 findings addressed here: `c-0001`/`c-0004` (per-invocation resolve),
`c-0002`/`c-0003` (match modes + union invariant), `c-0005` (manifest
ownership), `c-0006` (resolve before the `try`), `c-0007` (`lint_urls`),
`c-0008` (real write inventory), `c-0009` (`capture_blocked`), `c-0010`
(artifact-run warnings), `c-0011` (scrub-write-mark).

Declined, with the reviewer's rationale recorded: `c-0012` (cut both knobs) —
they are policy dials with defensible settings; `c-0013` (drop
`sensitive_parameters` from summaries) — the resolved verdict is what consumers
need and re-derivation is how three vocabularies happened; `c-0014` (keep
placeholder derivation local to export) — drift is the defect, not the export's
handling of it.
