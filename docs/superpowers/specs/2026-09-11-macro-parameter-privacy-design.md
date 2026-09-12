# Macro parameter privacy: one resolver, threaded

Date: 2026-09-11
Status: design, revision 2. Not approved.

Revision 2 rewrites revision 1 after adversarial review found its central
mechanism unspecified: `parameter_specs` could not reach the redaction call
sites, so an author's declaration would have silently no-opped. Review
provenance is recorded at the end.

## Scope

Three parts. The first is architecture and is the reason the other two work at
all.

- **Part 0** — resolve sensitivity once per run; thread one policy object;
  writers stop redacting.
- **Part A** — one tiered vocabulary, replacing three classifiers that
  disagree. **Purely additive**: it closes holes and adds tiers. It removes
  nothing from the sensitive set.
- **Part B** — `parameter_specs`, a per-macro override.

## Problem

### Three classifiers, disagreeing

A macro author cannot declare anything about a parameter. `parameters`
persists as `list[str]` (`macros/storage.py:95`, `macros/dsl.py:56-73`).
Sensitivity is inferred entirely from the name, by three separate
implementations that disagree on 20 of 30 sampled names:

| Definition | Location | Gates |
|---|---|---|
| `is_sensitive_key` | `artifacts/redaction.py:14-39` | artifact manifests, run results |
| `is_sensitive_arg_key` | `macros/privacy.py:19-57` | `args_used`, exports, the value scrub |
| `is_credential_arg` | `macros/substitution.py:104-107` | `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS` |

Live holes, measured:

- `private_key` — caught by `redaction`, **missed by `privacy`**. Redacted in
  the manifest, raw in `args_used` and the exported script.
- `cookie`, `set_cookie` — same shape, same direction.
- `otp` — known only to `substitution`. Reaches `args_used` and manifests in
  cleartext.
- `passphrase`, `pw`, `pwd`, `authorization`, `access_key` — **not**
  sink-blocked, so `{{passphrase}}` may expand into a URL or `evaluate()`
  while `{{password}}` is refused.
- Plural forms bypass everything: `passwords`, `api_keys` classify False in
  all three.

Two value-shape detectors are a different mechanism and stay as they are:
`macros/lint_credentials.py` (vendor prefixes + entropy) and
`OCTOWRIGHT_REDACT_INPUTS` (DOM element type, at record time).

### Redaction is applied more than once, by different rules

Manifest `parameters` is redacted **three** times:

```
macros/artifacts.py:344   redact_args(args_used)      privacy vocabulary
artifacts/models.py:38    redact_mapping(parameters)  redaction vocabulary
artifacts/reports.py:27   redact_mapping(...)  again  redaction vocabulary
```

The last two run after the first and cannot see an author's declaration. Three
passes with two vocabularies is not defence in depth — defence in depth applies
the *same* policy twice. Applying a *different* policy twice means the last one
wins.

### The case no heuristic can catch

A secret an author puts under `display` is never classified. `redact_args` now
value-scrubs (`macros/privacy.py:154`), which closes the duplicated-value case
for correctly-named keys, but nothing classifies `display` in the first place.

## Decisions

| Decision | Chosen | Rejected, and why |
|---|---|---|
| Architecture | Resolve once, thread a policy object, writers write | Threading specs into all three redactors entrenches three passes that agree today and drift tomorrow |
| Writer-side redaction | Removed, replaced by a tripwire | A writer that redacts can disagree with the decision made upstream |
| Where the declaration lives | Sibling key beside `parameters` | Changing `parameters` to a map breaks six readers, needs a 339-file migration, and collides with `normalise_parameters`, where dict-valued `parameters` already means name→literal-value |
| Element union `list[str \| obj]` | No | Two spellings for one fact, forever, as an authoring choice |
| Container union `list \| map` | No | `load_macro:139` returns raw JSON and is the hot path, so "the edge" is not one place; `macro_compile` would rewrite `parameters` shape on disk as a side effect of an unrelated edit |
| Key name | `parameter_specs` | `parameter_policy` has better precedent (`dialog_policy`) but is wrong the day a descriptive field lands |
| Direction | Mark **and** unmark | Mark-only leaves the over-claim unfixable |
| CONTEXTUAL demotion | **Dropped** | Head-noun matching fails open on unenumerated combinations — `user_ssn` resolves to head `ssn`, not sensitive. The override is the right tool for `user_count`, not a cleverer heuristic |
| Sink guard | One-directional, loud | `sensitive: false` in an author-supplied data file must not unblock an operator-controlled security control |
| Mark → value scrub | Yes, behind a shape guard | Unguarded, a marked value of `main`/`admin`/a tenant slug destroys the failure bundle — the bug already documented at `privacy.py:66-71` |
| Published contract | The resolved verdict, plus the raw declaration on detail | Publishing only the declaration makes every consumer re-derive the answer, which is how three vocabularies happened |

## Part 0 — resolve once, thread, tripwire

### The policy object

```python
@dataclass(frozen=True)
class ResolvedPrivacy:
    verdicts: Mapping[str, bool]       # resolved, per top-level arg name
    scrub_values: tuple[str, ...]      # values that cleared the shape guard
    sink_blocked: frozenset[str]       # names the sink guard refuses
    marker: str
    warnings: tuple[str, ...]          # e.g. an unmark ignored by the sink guard
```

Built once by `resolve_privacy(macro, args)` in `macros/privacy.py`, called at
the top of `_run_macro_impl` and `run_macro_artifact` — the two places where
both the macro (hence `parameter_specs`) and `args` are in scope.

### Threading

`redact_args` and `sensitive_arg_values` take the policy as a **required
positional parameter**. This is deliberate: every un-threaded call site fails
to typecheck, so the compiler enumerates the work rather than leaving it to a
reviewer. `mypy` is already in `make lint`.

Call sites to thread: `execution.py:67`, `:618`, `:668`;
`artifacts.py:158`, `:344`.

### Writers stop redacting

`redact_mapping` is removed from `models.new_manifest:38` and
`reports.write_artifact_manifest:27`. `reports.write_run_bundle:47` likewise
stops calling `redact_args`; its payload must arrive redacted. All six
`write_artifact_manifest` callers are in `macros/artifacts.py`, so the blast
radius is contained.

### The tripwire

`assert_no_sensitive(payload, policy)` re-scrubs with **the same policy** and
logs at error level plus increments `octowright_privacy_tripwire_total` if
anything changed. It runs at exactly the three writes the writers stopped
guarding: `write_artifact_manifest`, `write_run_bundle`, and the golden/capture
writes that share `reports._json_write`.

Same policy, so it cannot disagree. Idempotent, so it cannot corrupt. Logs, so
it cannot silently pass. It exists to catch an un-threaded path, and it covers
the one regression the writer removal introduces: an old manifest read back
through the merge path no longer gets a second scrub, and the tripwire is what
notices.

## Part A — unified vocabulary (additive)

One token set in `macros/privacy.py`, tiered by which consumers act on it.
**Every token matches anywhere in the key's token list, as today.** No name
that is sensitive now becomes insensitive.

**CREDENTIAL** — `password`, `passwd`, `pw`, `pwd`, `passphrase`, `secret`,
`token`, `otp`, `bearer`, `credential`, `auth`, `authorization`, `api_key`,
`apikey`, `access_key`, `private_key`, `cookie`, `set_cookie`

**IDENTITY** — `email`, `username`, `phone`

**CONTEXTUAL** — `user`, `peer`, `subject`, `session`, `contact`

Plurals are handled by matching each token **as written first, then
additionally** against a depluralized form — never by replacing the token.
Naive trailing-`s` stripping is wrong here and would break existing matches.
Several tokens already end in `s` without being plural — `access`, `address`,
`pass`, `process` — so stripping mangles them into non-words. For `access`
that is not merely cosmetic: it destroys the `access`+`key` pair, silently
un-classifying `access_key`. Match the written token first and treat the
depluralized form as an additional candidate, never a replacement. The pair
mechanism must depluralize both elements independently, since `api_keys`
tokenizes to `('api', 'keys')`.

Pairs (`api`+`key`, `access`+`key`, `session`+`id`) keep the existing
`SENSITIVE_KEY_PAIRS` mechanism.

| Consumer | Acts on |
|---|---|
| Output redaction — `args_used`, manifests, exports, value scrub | all three tiers |
| Sink guard — may a placeholder expand into a URL / `evaluate` | **CREDENTIAL only** |
| Loud-unmark warning | **CREDENTIAL only** |

The sink-guard split is what `macros/substitution.py:100-103` already argues:
`{{order_id}}` must keep working in a URL, and identity in a URL is the
legitimate parameterized-navigation case.

`macros/substitution.py` imports the CREDENTIAL tier instead of owning
`_CREDENTIAL_ARG_RE`. `artifacts/redaction.py` loses its only callers when the
writers stop redacting; whether it is deleted in this change or left as dead
code for a follow-up is open item 2.

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

Precedence: `parameter_specs[name]["sensitive"]` if present and a real `bool`,
else the Part A heuristic. Top-level names only; sensitivity inherits down a
branch. **No path syntax; a nested leaf cannot be unmarked.** Deliberate.

Undeclared args still hit the heuristic — specs override per-name, never
replace, because substitution is placeholder-driven and an arg can exist
without being declared.

### Sink guard: one-directional

- `sensitive: true` **tightens** — marking `display` blocks `{{display}}` from
  expanding into a navigation or `evaluate` sink.
- `sensitive: false` **never loosens** — only `OCTOWRIGHT_MACRO_CREDENTIAL_SINKS`,
  an operator control, can unblock a sink.
- An unmark on a CREDENTIAL-tier name that would have mattered to the sink
  guard produces its own lint finding and run warning: honoured for redaction,
  ignored for the sink guard.

An author-supplied data file must not be able to self-authorize exfiltration.

### Mark → value scrub, behind a shape guard

A marked value is redacted structurally **and** added to the substring scrub,
but only if it clears the guard:

- `str` leaves only. Non-string scalars are never added — `{"user_id": 1}`
  currently contributes the token `"1"`, and `{"session_active": True}`
  contributes `"True"`, which at exactly `_WORD_BOUNDED_BELOW = 4` substitutes
  unanchored anywhere.
- Minimum length `MIN_SCRUB_LEN`.
- Not in a small common-word list.

Values failing the guard get key-level redaction only. `macro_lint` reports
which marked values were excluded and why, so the author knows the
duplicate-in-error-text case is not covered for them.

### `parameters` is derived from the actions

The `{{placeholder}}` set in the action list is the truth — it is what
`_substitute_value:131` resolves against. `script_export` generates CLI flags
from that set rather than from `parameters`, so an omitted parameter can no
longer produce a flagless script that crashes on `KeyError`. `macro_lint`
reports drift between `parameters` and the placeholder set, and an orphan
`parameter_specs` key becomes checkable against the truth.

### Behaviour matrix

| Case | Behaviour |
|---|---|
| No `parameter_specs` key | Heuristic for every parameter. All 339 existing macros. |
| Key present, name absent from it | Heuristic for that name. Partial declaration is valid. |
| Spec names something not in the derived placeholder set | Orphan. Lint finding. |
| Arg passed at runtime, declared nowhere | Heuristic. |
| `sensitive` present but not a bool | Ignored, lint finding. |
| `parameter_specs` is a list, string, or null | Treated as absent, lint finding. `_coerce_parameter_specs` in `dsl.py` raises in strict mode. |
| Unknown field inside a spec | Per `OCTOWRIGHT_PARAMETER_SPEC_UNKNOWN`; lint reports either way. |
| Marked value fails the shape guard | Key-level redaction only; lint reports the exclusion. |
| Unmark on a CREDENTIAL name | Honoured for redaction, ignored by the sink guard, warned in both lint and the run result. |

## Published contract

Consumers get the **resolved verdict**, not the mechanism.

| Surface | Carries |
|---|---|
| Macro document | `parameter_specs` |
| `MacroDetail` type + `MCP-SHARED-CONTRACT.md` | `parameter_specs`, declared explicitly rather than riding along as an undocumented key |
| `MacroListEntry` / `types.ts MacroSummary` | `sensitive_parameters: list[str]` — the resolved set |
| `MacroRunResult` | `warnings: NotRequired[list[str]]` (`total=True` today, so it needs the marker) |
| `MacroSequenceStep` | `warnings: list[str]` (already `total=False` at `mcp_types.py:71`) |
| `macro_lint` | every unmark, drift, orphan and scrub exclusion, with reasons |

`sensitive_parameters` is a subset of `parameters`, so it is bounded and
stable, and it comes from the same resolver so it cannot disagree with
runtime. It covers **declared** parameters only; an undeclared runtime arg
cannot be precomputed, and the contract doc must say so.

`warnings` exists because `MacroRunResult` is `total=True` with six fixed keys
today, so a per-run warning could only reach a daemon log the MCP client never
sees — the signal that justified having no hard floor would be invisible
exactly when it matters.

## Configuration

| Var | Values | Default | Unparsable |
|---|---|---|---|
| `OCTOWRIGHT_PARAMETER_SPEC_WARN` | `credentials` \| `all` \| `off` | `credentials` | → default |
| `OCTOWRIGHT_PARAMETER_SPEC_UNKNOWN` | `preserve` \| `drop` | `preserve` | → default |

Both fall back to the default rather than off: a typo must not silently remove
a security warning, nor silently destroy author data. Same reasoning as
`OCTOWRIGHT_NETWORK_BODY_MAX_BYTES`.

`defaults.py` is at its LOC ceiling, so both parsers live in
`macros/privacy.py`, following the
`session/core_network_mixin.network_body_max_bytes` precedent.

## Touch points

| File | Change |
|---|---|
| `macros/privacy.py` | tiers, `ResolvedPrivacy`, `resolve_privacy`, shape guard, tripwire, both env parsers |
| `macros/execution.py` | resolve at `_run_macro_impl`; thread at `:67`, `:618`, `:668`; scope the recorder wrap; populate `warnings` |
| `macros/artifacts.py` | resolve in `run_macro_artifact`; thread at `:158`, `:344` |
| `macros/substitution.py` | import the CREDENTIAL tier; drop `_CREDENTIAL_ARG_RE`; honour one-directional specs |
| `artifacts/models.py:38` | **remove** `redact_mapping` |
| `artifacts/reports.py:27,47` | **remove** both redaction passes; call the tripwire |
| `artifacts/redaction.py` | loses its callers; deletion is open item 2 |
| `macros/storage.py:92` | `save_macro` carries the key |
| `macros/storage.py:117` | `list_macros` populates `sensitive_parameters` |
| `macros/dsl.py` | `_coerce_parameter_specs`; derive `parameters` from placeholders |
| `server/macros.py` | `macro_save` gains an optional param |
| `macros/lint.py` | orphan, drift, non-bool, unknown-field, scrub-exclusion, unmark findings |
| `artifacts/script_export.py` | route `_safe_default` through the resolver; emit the three tier sets; generate flags from placeholders; bump the classifier version |
| `mcp_types.py:51,62` | `sensitive_parameters`; `warnings: NotRequired[...]` |
| `types.ts`, `MCP-SHARED-CONTRACT.md`, `docs/env-vars.md` | contract + knobs |

`_safe_default` is named explicitly because it bakes literal parameter values
into the generated `.py` on disk (`script_export.py:427-433` → `:390`) using
the bare heuristic — a marked `display` value would be written in cleartext
into a file that gets committed.

`ARG_PRIVACY_CLASSIFIER_VERSION` goes to 3, and the export manifest records
the classifier version plus a hash of `parameter_specs` so
`macro_artifact_status` can report an export as stale.

### Recorder scoping

`install_sensitive_recorder` sets `session.recorder = SensitiveRecorder(...)`
(`privacy.py:202-205`) from `execution.py:544`, and the `finally` at `:603-612`
never restores it. On a pooled session the wrappers stack once per run and keep
scrubbing every later, unrelated recording against a dead run's values. Part B
makes this worse, since a marked common-valued parameter would corrupt
everything that instance does afterwards.

Fix: scope the wrap to the run with a context manager restoring the prior
recorder in `finally`, and make the installer idempotent — replace, never nest.

## Testing

TDD throughout.

- **Part 0**: policy resolved once per run; every threaded call site typechecks
  only when threaded; writers emit no redaction; tripwire fires on a
  deliberately un-threaded payload and is silent otherwise; old manifest via
  the merge path.
- **Part A**: each tier's membership; the four closed holes (`private_key`,
  `cookie`, `set_cookie`, `otp`); plural forms; sink guard acts on CREDENTIAL
  only; **no name that is sensitive today becomes insensitive**.
- **Part B**: no specs (the 339-macro path); partial; orphan; non-bool; non-dict
  container; unknown field under both knob values; mark-then-sink-refused for
  an ordinary name; unmark-does-not-unblock for a CREDENTIAL name; nested
  inheritance; nested leaf cannot be unmarked; undeclared arg falls through.
- **Shape guard**: non-string scalars never enter the scrub set; a numeric or
  boolean classified arg leaves an unrelated diagnostic untouched; short and
  common-word marked values get key-level redaction only and are reported.
- **Export**: a marked parameter's value is never embedded as an argparse
  default (`test_export_never_embeds_any_runtime_sensitive_default` is the
  template); flags generated from placeholders; tiers mirrored into the
  generated module; parity with the runtime resolver.
- **Contract**: `sensitive_parameters` surfaces through `macro_list`;
  `warnings` appears in the run result, not merely in a log line.
- **Knobs**: each value plus unparsable → default.

## Risks

**Part A is additive, so the redaction regression risk of revision 1 is gone.**
Nothing that is redacted today stops being redacted. The corpus sweep revision 1
relied on is no longer load-bearing, which matters because it structurally
could not have worked: the classifier also runs on arbitrary runtime arg names
and nested mapping keys that never appear in a macro file.

**Writer removal.** An old manifest read back through the merge path loses its
second scrub. The tripwire is the mitigation and needs a test on that path.

**Version skew.** An older daemon ignores `parameter_specs` and falls back to
the heuristic. `sensitive: true` is lost → silent under-redaction;
`sensitive: false` is lost → over-redaction, which is safe. One-directional and
real; mitigated only by the heuristic floor.

**Export staleness.** An export generated before this change carries the old
classifier and no specs. The version stamp plus spec hash in the manifest is
what makes that visible; without it, adding `sensitive: true` and re-running
lint looks like a fix while the CI job keeps writing the secret.

## Deferred

- **`parameters` as a map.** Revisit only if the spec bag grows, as its own
  project with a converter and a version bump.
- **Nested path syntax** for unmarking a leaf.

## Open items

1. `MIN_SCRUB_LEN` value and the common-word list contents.
2. Whether deleting `artifacts/redaction.py` outright is in scope, or whether
   it stays as dead code until a follow-up.
3. `ARG_PRIVACY_CLASSIFIER_VERSION` — codex/scope argued (branch review
   `c-0010`) that the constant should be deleted rather than bumped, since
   nothing branches on it. This spec bumps it and additionally uses it for
   export staleness, which gives it a consumer. Confirm that is wanted.

## Review provenance

Revision 1 was reviewed by `afriend` in `crossexam` mode with `codex` and `agy`
as independent friends and `claude` as advisory host self-review. Result:
**incomplete, not converged** — 14 upheld, 1 deadlocked, 1 unjudged.

Run: `afriend-design/run-20260911T174636-f6cafbbc`

Findings addressed here: `c-0001` (drift/export flags), `c-0002`/`c-0003`
(threading — the rewrite), `c-0004` (sink-guard direction), `c-0005` (manifest
composition), `c-0006` (shape guard), `c-0007` (recorder scoping), `c-0008`
(warnings channel), `c-0009` (classifier version, export staleness), `c-0010`
(`_safe_default`, tier mirroring), `c-0011` (resolved by dropping the CONTEXTUAL
demotion), `c-0012` (`_coerce_parameter_specs`), `c-0013`/`c-0016` (resolved
verdict on the summary, raw declaration on detail).

Declined: `c-0014` (cut the warn knob) — the knob is a stated requirement.
`c-0015` (narrow the spec shape) — made configurable instead, defaulting to
preserve.

The branch review (`afriend-branch/run-20260911T174631-866e1228`) contributed
the non-string-scalar scrub defect and the plural-form gap.
