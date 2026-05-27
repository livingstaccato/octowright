# Automation Artifact Workbench Design

Date: 2026-05-27

## Purpose

Octowright already has macro artifacts, evidence bundles, digest tools, and import-safe macro CLI export. The next DX step is to turn these pieces into a coherent Automation Artifact Workbench: a shared product model for macros, recordings, and scenarios where every automation answers:

1. What is this automation supposed to prove?
2. What evidence did the latest run collect?
3. Can I replay, export, inspect, and monitor it from here?

The long-term product shape is integrated, but the first implementation phase is deliberately narrow: deterministic critical-point verification for macro artifacts. The design must keep extension points for export fidelity, scenario aggregation, recording artifacts, and optional LLM/image judging without building those follow-on features in phase 1.

## Current Baseline

Existing macro artifact capabilities:

- `macro_artifact_plan` validates macro args and writes/updates an artifact manifest.
- `macro_artifact_run` replays a macro and writes a run bundle with `result.json`, `evidence.json`, and `summary.md`.
- `macro_artifact_list` lists artifact manifests.
- `macro_digest` returns bounded macro or recording summaries.
- `macro_export_cli` writes an import-safe argparse script with redacted args and optional evidence output.
- `src/octowright/artifacts/` already owns shared path containment, redaction, evidence records, manifests, reports, digests, and CLI export.

Known gaps this design addresses or reserves:

- `critical_points` exists in manifests but is not operationalized.
- There is no deterministic verification result for artifact runs.
- Evidence capture is useful but coarse; common checks need label-based evidence references rather than manual evidence IDs.
- Scenario aggregation and export parity need the same artifact vocabulary later.
- Telemetry exists across Octowright but artifact verification needs explicit spans, metrics, and structured logs.

## Non-Goals

- Do not replace `macro_run`, `macro_artifact_run`, or `macro_export_cli`.
- Do not build a Webwright-style internal LLM agent loop.
- Do not require LLM/API keys for phase 1 verification.
- Do not implement scenario artifact aggregation in phase 1.
- Do not implement full macro export action parity or engine/profile fidelity in phase 1.
- Do not move existing macro JSON, recording JSONL, or artifact directories.
- Do not create separate artifact formats for macros, recordings, and scenarios.

## Product Model

The Workbench model has four reusable concepts:

| Concept | Meaning |
|---|---|
| Source | The automation definition: macro JSON in phase 1, later recording JSONL or scenario spec. |
| Run | One execution attempt with result, evidence, summary, and verification output. |
| Critical point | A user-facing claim the automation should prove, backed by deterministic checks and evidence. |
| Output | A human or machine-consumable result: summary, dashboard links, exported CLI, aggregate report, trace/video references. |

Phase 1 makes these concepts real for macro artifacts. Later phases reuse the same fields and report layout for scenarios and recordings.

## Architecture

Keep the existing shared artifact core and add focused verification modules:

| Module | Responsibility |
|---|---|
| `artifacts/models.py` | Extend/normalize critical-point and verification result shapes. |
| `artifacts/verification.py` | Deterministic verifier: evaluates checks against run result and evidence records. |
| `artifacts/reports.py` | Render verification and critical-point sections into `summary.md`; write `verification.json`. |
| `macros/artifacts.py` | Macro-specific orchestration: load macro artifact, set/get critical points, run macro, call verifier, return compact status. |
| `server/macros.py` | Thin MCP wrappers only. |
| `server/profiles.py` | Add new tools to the `macros` capability profile. |

Boundaries:

- Shared modules know nothing about macro replay internals.
- Macro orchestration knows how to find macro manifests and latest runs, but uses shared verifier/report writers.
- MCP wrappers remain small and should not contain verification logic.

## Critical Point Shape

Critical points are stored in `artifact.json` under the existing `critical_points` key.

Canonical shape:

```json
{
  "id": "CP1",
  "description": "Login form submits successfully",
  "status": "unknown",
  "checks": [
    {"type": "result_status", "status": "ok"},
    {"type": "screenshot_exists", "label": "after"}
  ],
  "evidence": [],
  "last_verified_run": null,
  "notes": null
}
```

Fields:

| Field | Meaning |
|---|---|
| `id` | Stable identifier. If omitted on set, generate `CP1`, `CP2`, etc. |
| `description` | Human-facing claim. Required. |
| `status` | `unknown`, `passed`, `failed`, or `blocked`. |
| `checks` | Deterministic check declarations. Empty means the CP is blocked until checks are added. |
| `evidence` | Evidence IDs or labels that support the latest verification. |
| `last_verified_run` | Latest run id this CP was evaluated against. |
| `notes` | Optional user/agent note. |

Existing manifests with partial critical-point objects should be normalized when read or updated. Unknown extra keys may be preserved under `metadata` if needed, but phase 1 should keep the stored shape predictable.

## Deterministic Checks

Initial check types:

| Type | Required Fields | Behavior |
|---|---|---|
| `result_status` | `status` | Passes when `result.json.status` equals the expected status. |
| `evidence_exists` | `id` or `label` | Passes when `evidence.json.records` contains a matching evidence ID or label. |
| `screenshot_exists` | `label` | Passes when screenshot evidence with that label exists and the file is present. |
| `assertion_passed` | `id` or `label` | Passes when assertion evidence exists with `status=passed`. |
| `log_contains` | `text`; optional `label` or `id` | Passes when bounded, redacted evidence preview/log text contains the expected text. |

Check output shape:

```json
{
  "type": "screenshot_exists",
  "status": "passed",
  "message": "Found screenshot evidence label='after'.",
  "evidence": ["ev_002"]
}
```

Critical-point status rules:

- `passed`: every check passes.
- `failed`: at least one check fails.
- `blocked`: checks are missing, the run bundle is incomplete, or required args prevented a run.
- `unknown`: never verified.

Unknown check types should fail that check with a clear `unknown_check_type` message rather than crashing the entire verification run.

## Macro Artifact DX

Keep existing tools and add a compact verification layer:

| Tool | Behavior |
|---|---|
| `macro_artifact_critical_points_get(name)` | Return normalized critical points and manifest path. |
| `macro_artifact_critical_points_set(name, critical_points)` | Replace the macro artifact's critical points after validation/normalization. |
| `macro_artifact_verify(name, run_id=None)` | Evaluate critical points against a run bundle. Defaults to latest run. Writes `verification.json` and updates CP statuses in manifest. |
| `macro_artifact_status(name)` | Return compact artifact state: latest run, verification status, pass/fail counts, missing evidence hints, paths. |

Enhance existing tool:

| Tool | Change |
|---|---|
| `macro_artifact_run` | Add `verify: bool = True`. If critical points exist, run deterministic verification after replay and include verification paths/status in the response. If no critical points exist, return `verification_status="not_configured"` without changing existing run behavior. |

Existing `macro_artifact_plan`, `macro_artifact_list`, `macro_digest`, and `macro_export_cli` remain compatible.

## Data Flow

1. **Plan**
   - Agent calls `macro_artifact_plan(name, args)`.
   - Manifest is created/updated with source, params, paths, and readiness.

2. **Define critical points**
   - Agent calls `macro_artifact_critical_points_set`.
   - Critical points are normalized, validated, and written into `artifact.json`.

3. **Run**
   - Agent calls `macro_artifact_run(instance_id, name, args, verify=True)`.
   - Macro replay produces `result.json`, `evidence.json`, and `summary.md`.
   - If critical points exist, verifier reads the run bundle and writes `verification.json`.
   - Manifest updates latest run and CP status/last verified run.

4. **Inspect**
   - Agent calls `macro_artifact_status(name)` for compact state.
   - Dashboard or docs can link to summary/result/evidence/verification paths.

5. **Export**
   - `macro_export_cli` stays an output path. Future export parity work can include critical-point metadata in exported evidence logs, but phase 1 does not require that.

## Evidence Strategy

Phase 1 should reduce evidence bookkeeping:

- Existing before/after screenshots remain.
- Checks can reference evidence by `id` or by stable `label`.
- `macro_artifact_run` should preserve current `before` and `after` labels.
- If a macro contains explicit screenshot actions, those should continue to appear in the backing recording; a later phase may promote them into artifact evidence records.
- `verification.json` should record which evidence IDs satisfied each check.

Phase 1 should not add new macro-run capture arguments. Keep the first slice smaller and rely on before/after screenshots plus existing assertion/log evidence. If later user testing shows agents need named mid-run checkpoints, design that as a separate evidence-capture extension rather than slipping it into verification.

## Observability

Verification needs the same operational quality as browser, macro, scenario, and bridge flows.

### Spans

Add spans with bounded attributes:

| Span | Attributes | Emitted By |
|---|---|---|
| `octowright.artifact.verify` | `artifact_type`, `name`, `run_id`, `critical_points` | Top-level verification call. |
| `octowright.artifact.verify.check` | `artifact_type`, `check_type`, `status` | Per-check evaluation. |
| `octowright.macro.artifact.run` | `macro`, `run_id`, `verify` | Wrapper around macro artifact run orchestration. |

Avoid high-cardinality labels in spans where possible. Macro names may appear in spans, but metrics must cap macro labels as existing macro metrics do.

### Metrics

Add bounded metrics:

| Instrument | Type | Labels | Description |
|---|---|---|---|
| `octowright_artifact_verify_total` | counter | `artifact_type`, `status` | Verification runs by final status. |
| `octowright_artifact_verify_check_total` | counter | `artifact_type`, `check_type`, `status` | Individual check results. |
| `octowright_artifact_verify_duration_seconds` | histogram | `artifact_type`, `status` | End-to-end verification duration. |
| `octowright_macro_artifact_run_total` | counter | `macro`, `status`, `verified` | Macro artifact runs; macro label must use a bounded-cardinality helper. |

Reuse or generalize the existing macro label cap pattern from `macros/execution.py` so dynamic macro names cannot create unbounded time series.

### Structured Logs

Log key lifecycle events:

- `octowright.artifact.verify.start`
- `octowright.artifact.verify.complete`
- `octowright.artifact.verify.check_failed`
- `octowright.macro.artifact.run.complete`
- `octowright.macro.artifact.critical_points.updated`

Logs should include:

- `artifact_type`
- `name`
- `run_id` when applicable
- `critical_point_id` when applicable
- `check_type` when applicable
- `status`
- `path` only for contained artifact paths

Do not log raw sensitive args or raw log snippets. Use existing redaction helpers for any previews.

### Status Surfaces

`macro_artifact_status` should be the user-facing compact observability surface. Future `octowright_status()["artifacts"]` can expose aggregate counts, but phase 1 can keep this per-artifact to avoid scanning all artifact directories on every status call.

## Error Handling

| Condition | Behavior |
|---|---|
| Missing macro | Existing `load_macro` failure surfaces. |
| Missing args | `macro_artifact_plan` and status report `ready=false`; verification reports blocked if no run exists. |
| No critical points | `macro_artifact_run` returns `verification_status="not_configured"`. |
| Missing latest run | `macro_artifact_verify` returns blocked with a clear message. |
| Missing evidence file | Relevant check fails with `missing_evidence_file`. |
| Unknown check type | That check fails with `unknown_check_type`; verification continues. |
| Malformed manifest | Follow current list/plan behavior: skip unsafe malformed manifests where listing, overwrite safely where planning/updating. |
| Symlink/path escape | Reject or ignore according to existing artifact path containment policy. |
| Summary write failure | Verification should fail loudly; partial reports are worse than an explicit error. |

## Compatibility

- Existing macro artifact directory layout remains unchanged.
- Existing `artifact.json` fields remain valid.
- Existing `macro_artifact_run` callers continue working.
- Existing tests for plan/run/export/digest continue passing.
- New tools are added to the `macros` profile.
- No runtime dependency on model APIs is introduced.

## Future Phases

### Phase 2: Export Fidelity and Parity

- Preserve engine, viewport, headed mode, and optional profile/session semantics in exported scripts where safe.
- Expand exported CLI action coverage to match live macro replay, including conditionals, `macro_call`, semantic locator actions, pages, frames, routes, dialog policy, and uploads.
- Include critical-point metadata and verification-friendly evidence output in exported scripts.

### Phase 3: Scenario Artifact Aggregation

- Add scenario artifact manifests under the same artifact root.
- Each participant gets a child run/evidence bundle.
- Aggregate status rolls up role-level and participant-level verification.
- Scenario summary links to participant recordings, traces, screenshots, and verification.

### Phase 4: Optional LLM/Image Judge Plugin

- Define a verifier plugin interface.
- Keep deterministic checks as the default and only built-in verifier.
- Add optional image/verdict judging later behind explicit configuration and no default API dependency.

## Testing Strategy

New tests:

| Area | Assertions |
|---|---|
| Critical-point normalization | IDs generated/preserved; invalid objects rejected; partial old manifests normalized. |
| Verification core | Each check type passes/fails/blocks correctly; unknown checks fail without crashing. |
| Verification reports | `verification.json` shape is stable; `summary.md` includes CP status and evidence links. |
| Macro tools | set/get/status/verify work on contained artifact manifests. |
| Run integration | `macro_artifact_run(..., verify=True)` writes verification output when CPs exist and reports `not_configured` when absent. |
| Manifest merge | Existing `created_at`, `exports`, `latest_run`, and metadata are preserved correctly. |
| Security | Symlink/path escape behavior remains contained. |
| Redaction | Args and log previews stay redacted in responses, logs, and files. |
| Profiles | New tools are present in the `macros` profile. |
| Telemetry | Spans/metrics/log helper calls are emitted with bounded labels and no sensitive values. |

Regression tests:

```bash
uv run pytest tests/test_artifacts_*.py tests/test_macro_artifacts.py -v
uv run pytest tests/test_macros.py tests/test_macro_storage.py tests/test_runner.py tests/test_export.py -v
uv run pytest tests/test_profiles.py -v
make lint
make test
```

## Rollout

| Phase | Scope |
|---|---|
| 1A | Critical-point model, verifier core, report writer updates. |
| 1B | Macro MCP tools: get/set/verify/status. |
| 1C | Integrate `verify=True` into `macro_artifact_run`. |
| 1D | Observability: spans, metrics, structured logs, status response fields. |
| 2 | Export fidelity/parity. |
| 3 | Scenario artifact aggregation. |
| 4 | Optional LLM/image judge plugin. |

## Success Criteria

- A user can define critical points for a macro artifact.
- A macro artifact run produces result, evidence, summary, and deterministic verification output.
- `macro_artifact_status` gives a compact, useful answer about readiness, latest run, verification status, and missing evidence.
- Existing macro artifact/export behavior remains compatible.
- Observability is complete enough to debug verification in logs/traces/metrics without reading raw artifact files.
- The design leaves scenario aggregation and export parity with clear extension points, not parallel systems.
