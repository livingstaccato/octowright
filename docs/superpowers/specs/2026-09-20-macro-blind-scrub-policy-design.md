# Macro Blind-Scrub Policy Design

## Problem

Octowright classifies macro argument names in three tiers: credentials,
identity, and contextual data. Today those tiers collapse into one tuple of
strings. Every string is then blindly replaced in diagnostics, artifacts,
screenshots, generated scripts, and every later JSONL row written by the
session. The session ledger is intentionally permanent because a credential
typed during one run can remain visible after the run ends.

That permanence is correct for credentials but destructive for ordinary
identity and contextual values. A run with `session="1"` rewrites an unrelated
selector such as `li:nth-child(1)` and query strings such as `?page=1`; a run
with `user="admin"` rewrites unrelated prose. Saved macros and exported scripts
then consume corrupted recording data. This is issue #247.

## Goals

- Preserve aggressive, session-lifetime blind scrubbing for credentials of
  every length.
- Make replay integrity the default for identity and contextual arguments.
- Keep structural redaction of classified argument fields unconditional.
- Give operators explicit choices for maximum privacy or refusal.
- Apply one policy consistently across runtime, recordings, artifacts,
  screenshots, and standalone generated scripts.
- Update every affected user-facing document and architecture diagram.

## Non-goals

- Do not change which argument names belong to the credential, identity, or
  contextual classifier vocabularies.
- Do not add a minimum-length heuristic or a language-specific common-word
  list. Neither can prove that a match came from a macro argument.
- Do not redesign the JSONL schema or attempt provenance tracking for every
  string emitted by a browser.
- Do not weaken the credential sink guard.
- Do not rewrite already generated scripts or existing recordings.

## Operator policy

Add `OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY` with three strict values:

- `credentials` is the default. Credential-tier values enter blind scrubbers
  and the permanent session ledger. Identity and contextual values are still
  redacted where their classified key supplies provenance, but are not blindly
  replaced across arbitrary strings.
- `all` preserves the pre-fix behavior. Every classified value enters blind
  scrubbers and the permanent session ledger. Documentation must state that
  short or common values can corrupt replay data.
- `reject` refuses a macro invocation that supplies an identity or contextual
  value. Credential-only invocations continue normally. The error identifies
  argument paths and tiers but never includes values.

Parsing trims whitespace and ignores case. An unset variable resolves to
`credentials`. Any other value raises a configuration error rather than
silently choosing a privacy posture.

This policy controls blind value matching only. Key-provenance operations such
as redacting `args_used`, manifests, and returned argument dictionaries continue
to redact all three tiers under every mode.

## Classification model

The current boolean collector loses the tier needed to apply this policy.
Introduce one internal classified-argument representation carrying:

- the leaf value used by blind scrubbers;
- the argument path used in value-free rejection messages; and
- the effective tier: `credential`, `identity`, or `contextual`.

Classification continues to recurse through mappings and sequences. A nested
key can raise the inherited tier but never lower it: credential outranks
identity, which outranks contextual. Identifier-shaped mapping keys remain
structural and are not collected as values, preserving the existing fix for
diagnostic corruption. Non-field-name mapping keys under a classified branch
remain data and inherit that branch's tier.

The existing `is_credential_key` and `is_sensitive_arg_key` contracts remain.
The all-classified `sensitive_arg_values` API also remains available for code
that explicitly needs classification rather than policy admission. A new
policy-aware selector is the only API used to feed blind scrubbers and ledgers.

## Runtime data flow

At the start of a top-level run, artifact run, or nested `macro_call`:

1. Classify supplied arguments with paths and tiers.
2. Resolve `OCTOWRIGHT_MACRO_BLIND_SCRUB_POLICY`.
3. Under `reject`, fail before substitution, recorder installation,
   screenshots, artifact writes, or browser activity if a non-credential leaf
   exists.
4. Select credential values under `credentials`, or every classified value
   under `all`.
5. Add only selected values to the run ledger and permanent session ledger.
6. Use the same selected set for diagnostics, screenshot protection, artifact
   reports, and other blind scrubbers.

`PrivacyLedger` and `SensitiveRecorder` remain string-based. Their lifecycle,
de-duplication, longest-first ordering, and session permanence do not change;
only policy-admitted values reach them.

Structural argument redaction runs from classification, not the admitted blind
scrub set. For example, under the default policy
`{"user": "admin", "note": "Administrator"}` becomes
`{"user": "<redacted>", "note": "Administrator"}` rather than rewriting the
unrelated note.

## Generated scripts and compatibility

Generated Python scripts embed the same tier classifier, strict environment
resolver, rejection rule, and blind-scrub selection. Bump the embedded privacy
classifier version so parity tests detect drift. The policy is resolved when a
standalone script runs, allowing deployment-specific configuration without a
dependency on the Octowright package.

Scripts generated by older Octowright versions retain their embedded legacy
behavior. Users must regenerate them to receive the new default. Existing JSONL
recordings are not repaired automatically.

## Failure behavior

`reject` errors list stable argument paths and tier names, sorted for
deterministic output. They never render `repr(value)`, interpolated values, or a
container holding those values. A nested-call rejection is raised before the
child macro dispatches. An invalid environment value names the variable and the
three accepted tokens.

Credential values continue to be scrubbed at any length and in their raw,
JSON-escaped, and URL-encoded spellings. The existing credential sink guard
continues to reject credentials expanded into URL and code sinks independently
of this policy.

## Documentation and diagrams

Update all affected documentation in the same change:

- `docs/macros.md`: replace the short/common-value workaround with the default
  behavior, all three modes, nested-call/session-ledger semantics, screenshot
  implications, generated-script compatibility, and honest limits.
- `docs/env-vars.md`: document the new variable, default, parser, strict invalid
  handling, and privacy/integrity tradeoffs.
- `docs/architecture/macro-lifecycle.puml`: show classification, policy
  resolution, rejection, and policy-selected run/session ledgers before action
  dispatch.
- `docs/architecture/artifact-flow.puml`: show the policy-selected scrubber at
  the JSONL/artifact boundary and distinguish structural redaction from blind
  scrubbing.
- Regenerate and commit the matching SVGs with `make diagrams`.
- Update `CHANGELOG.md` for issue #247 and the new configuration default.
- Run a repository-wide reference scan after editing so README material,
  examples, agent documentation, and architecture indexes are updated if they
  describe the changed behavior.

## Verification

Tests must be written before production changes and must cover:

- the issue reproduction: `session="1"` and `user="admin"` leave unrelated
  selectors, URLs, and prose byte-identical under the default;
- `password="1"` remains scrubbed from immediate and later session rows,
  including encoded spellings;
- `all` preserves legacy blind scrubbing;
- `reject` fails before side effects for top-level runs, nested calls, and
  artifact runs, with value-free deterministic errors;
- case/whitespace normalization and invalid configuration values;
- unconditional structural redaction under all three modes;
- screenshot and diagnostic classification under each mode;
- runtime/generated-script parity and the classifier-version bump; and
  session-ledger accumulation for admitted values only.

After focused tests pass, run formatting/linting, type checking, diagram
generation checks, `make audit`, and the complete test suite. The release is not
ready until all gates pass or a separately documented release decision changes
their scope.
