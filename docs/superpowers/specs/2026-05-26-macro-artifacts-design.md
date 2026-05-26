# Macro-First Automation Artifacts Design

Date: 2026-05-26

## Purpose

Octowright should absorb Webwright's artifact discipline without becoming Webwright. The goal is a cohesive artifact system that supports five DX improvements through shared primitives:

1. Parameterized CLI export for macros/scripts.
2. Evidence bundles for macro and scenario runs.
3. Critical-point specs for verification.
4. Compact digest tools for token savings.
5. A more polished reusable artifact path around existing script export.

The first public DX is macro-first because macros are the smallest reusable Octowright automation unit. The core must remain generic so recording-first and scenario-first entry points can reuse the same artifact store, evidence schema, reports, redaction, and digest logic later.

## Non-Goals

- Do not replace `macro_run` or change its return contract.
- Do not move existing macro JSON files or recording JSONL files.
- Do not require every macro to have a critical-point plan in the first release.
- Do not implement scenario artifact aggregation in the first release.
- Do not build an internal LLM agent loop like Webwright.
- Do not create independent artifact formats for macros, recordings, and scenarios.

## Architecture

Add a shared artifact core under `src/octowright/artifacts/` and keep macro-specific orchestration in `src/octowright/macros/artifacts.py`.

| Module | Responsibility |
|---|---|
| `artifacts/models.py` | Typed manifest, run result, evidence, critical-point, and digest shapes. |
| `artifacts/paths.py` | Safe artifact directory creation under `RECORDINGS_DIR/artifacts/`; run numbering. |
| `artifacts/redaction.py` | Shared sensitive-key/value redaction for args, reports, and MCP responses. |
| `artifacts/evidence.py` | Evidence record construction for screenshots, assertions, goldens, log excerpts, generic files, and digests. |
| `artifacts/reports.py` | Write `artifact.json`, `result.json`, `evidence.json`, `summary.md`, and later JUnit/aggregate reports. |
| `artifacts/digest.py` | Compact action/session summaries for token-saving tools. |
| `artifacts/script_export.py` | Higher-level reusable Python CLI export built on current export handlers where possible. |
| `macros/artifacts.py` | Macro-specific artifact planning, run orchestration, and CLI export calls. |

The artifact core is intentionally not macro-specific. Future scenario and recording tools use the same modules rather than introducing parallel formats.

## Artifact Directory Layout

Artifacts live under the recordings root to preserve the existing disk-write containment model.

```text
RECORDINGS_DIR/artifacts/
  macros/
    <macro-slug>/
      artifact.json
      exports/
        <macro-slug>.py
      runs/
        run_0001/
          result.json
          evidence.json
          summary.md
          screenshots/
          replay.jsonl
```

Path handling rules:

- All artifact paths must resolve under `RECORDINGS_DIR/artifacts/`.
- Macro names are slugged and then containment-checked.
- Run directories are monotonic: `run_0001`, `run_0002`, etc.
- User-supplied export paths must be contained under the recordings root unless a later explicit safe export policy is designed.

## Data Model

### Artifact Manifest

| Field | Meaning |
|---|---|
| `artifact_version` | Schema version, initially `1`. |
| `artifact_type` | `macro` for v1; later `recording` or `scenario`. |
| `name` | Macro, recording, or scenario name. |
| `source` | Source macro path, recording path, or scenario spec path. |
| `parameters` | Declared parameters and redacted resolved values. |
| `created_at` | ISO timestamp. |
| `updated_at` | ISO timestamp. |
| `latest_run` | Latest run id and path, if any. |
| `exports` | Generated script/CLI paths. |
| `critical_points` | Optional verification points. |
| `metadata` | Non-critical extra details. |

### Run Result

| Field | Meaning |
|---|---|
| `run_id` | `run_0001` style id. |
| `status` | `ok`, `failed`, or `partial`. |
| `started_at` / `ended_at` | Runtime timestamps. |
| `instance_id` | Browser instance used for replay. |
| `macro` | Macro name. |
| `args_used` | Redacted resolved args. |
| `executed` / `skipped` | Counts from macro replay. |
| `error` | Error string if failed. |
| `recording_path` | Backing JSONL evidence path. |
| `evidence_path` | `evidence.json` path. |

### Evidence Records

| Type | Required Fields | Notes |
|---|---|---|
| `screenshot` | `id`, `path`, `label`, `ts` | Produced before/after run or at explicit capture points. |
| `assertion` | `id`, `tool`, `status`, `message` | For `expect_*`, macro checks, and future critical points. |
| `golden` | `id`, `name`, `status`, `diff_count` | Wraps existing golden results. |
| `log_excerpt` | `id`, `path`, `offset`, `length`, `preview` | Avoids copying full JSONL into MCP responses. |
| `artifact` | `id`, `path`, `kind`, `description` | Script export, JUnit, markdown summary, or other files. |
| `digest` | `id`, `summary`, `truncated`, `source_size` | Token-saving summary payload. |

### Critical Points

Critical points are optional in v1. When present, they have this shape:

```json
{
  "id": "CP1",
  "description": "User is logged in",
  "evidence": ["ev_001", "ev_002"],
  "status": "passed"
}
```

Critical points may be stored in `artifact.json` and rendered into `summary.md`. A future tool can read or write a `plan.md`, but v1 does not require one.

## Macro-First MCP DX

Add macro artifact tools while keeping existing macro tools unchanged.

| Tool | Behavior |
|---|---|
| `macro_artifact_plan(name, args=None)` | Validates the macro exists, resolves declared params, returns intended artifact paths and missing args without running a browser. |
| `macro_artifact_run(instance_id, name, args=None, capture=True, notes=None)` | Runs the macro through existing `run_macro`, writes a `runs/run_NNNN/` bundle, captures evidence, and returns compact paths. |
| `macro_export_cli(name, out_path=None, args=None, include_evidence=True)` | Exports an import-safe Python CLI around macro actions. |
| `macro_artifact_list(name=None, limit=20)` | Lists macro artifact manifests and recent runs. |
| `macro_digest(name=None, instance_id=None, since=None, max_chars=4000)` | Returns a compact macro or recording summary for model context. |

Example compact response:

```json
{
  "ok": true,
  "macro": "login",
  "run_id": "run_0001",
  "summary": "Ran 8 actions, skipped 1 lifecycle action, captured 2 evidence records.",
  "paths": {
    "run_dir": "...",
    "summary": ".../summary.md",
    "evidence": ".../evidence.json",
    "result": ".../result.json"
  }
}
```

The full details stay on disk. MCP responses should remain token-light.

## CLI DX

CLI aliases are useful but not required in the first implementation phase. When added, they should wrap the same macro artifact orchestration functions:

```bash
uv run octowright macro artifact run <name>
uv run octowright macro export-cli <name>
uv run octowright macro digest <name>
```

## Script Export Requirements

`macro_export_cli` should produce a Python script with these properties:

- One reusable function named from the macro/domain.
- `argparse` flags for every macro parameter.
- Defaults from provided `args` when present.
- Import-safe module: no browser launch, network I/O, or file writes at import time.
- Browser execution guarded by `if __name__ == "__main__"`.
- Redacted parameter echo in the action log.
- Non-zero exit on failed replay/assertion.
- Optional evidence output into a run directory.

Existing `src/octowright/export.py` should be reused where possible, but the CLI wrapper may need a new higher-level emitter because current export is recording-centric and `main()`-centric.

## DRY Boundaries

No feature should invent its own artifact folder, evidence schema, redaction, run numbering, or report writer.

| Shared Primitive | Used By V1 | Later Used By |
|---|---|---|
| `ArtifactStore` | Macro artifact dirs and manifests | Recording/scenario artifacts. |
| `next_run_dir()` | Macro artifact runs | Scenario participant runs. |
| `redact_mapping()` | Macro args/results | Scenario fixture params, recording digests. |
| `EvidenceBuilder` | Macro screenshots/log excerpts | Scenario/golden/assertion evidence. |
| `ReportWriter` | Macro reports | JUnit, scenario reports, recording summaries. |
| `DigestBuilder` | `macro_digest` | `browser_digest`, `recording_digest`. |
| `CliScriptExporter` | `macro_export_cli` | Future recording script export. |

Macro-specific code should only orchestrate:

```text
load macro
validate args
create artifact run
call existing run_macro
capture evidence
write shared reports
return compact result
```

Avoid macro-specific markdown renderers, scenario-specific evidence JSON, and duplicated redaction helpers.

## File Size Constraints

| File | Target |
|---|---|
| Any new module | Under 300 LOC preferred, hard cap 500 LOC. |
| `server/macros.py` | Keep thin; only MCP wrappers. |
| `macros/artifacts.py` | Thin macro orchestration only. |
| `artifacts/*.py` | Focused single-purpose modules. |

## Token-Saving Behavior

`macro_digest` and future digest tools must be bounded by default.

Rules:

- Default `max_chars` is 4000 for v1; implementation may define this by reusing an existing preview constant if that constant is also 4000.
- Return `truncated`, `source_size`, and `cap` when truncation occurs.
- Prefer summaries and artifact paths over embedding full JSONL, screenshots, or script bodies.
- Preserve full-fidelity content on disk.

## Security and Privacy

- All disk writes must be contained under the recordings root unless a specific safe export exception is designed later.
- Sensitive keys are redacted in MCP responses and artifact files. Include at least password, passwd, passphrase, secret, token, api_key, apikey, access_key, credential, pw, pwd, auth, email, and username.
- Macro action values for fill/type actions should continue to use existing runtime redaction behavior.
- Log excerpts should be bounded and should not duplicate entire recordings into summary files.

## Testing Strategy

Tests should lock the artifact contract before implementation.

| Step | Failing Test First | Implementation Target |
|---|---|---|
| 1 | Artifact paths stay under `RECORDINGS_DIR/artifacts` and reject traversal. | `artifacts/paths.py` |
| 2 | Run IDs increment deterministically: `run_0001`, `run_0002`. | `ArtifactStore.next_run_dir()` |
| 3 | Redaction catches password/token/email/username keys. | `artifacts/redaction.py` |
| 4 | Evidence records serialize stable JSON. | `artifacts/evidence.py` / models |
| 5 | Report writer creates `artifact.json`, `result.json`, `evidence.json`, `summary.md`. | `artifacts/reports.py` |
| 6 | `macro_artifact_plan` returns missing args without browser execution. | `macros/artifacts.py` |
| 7 | `macro_artifact_run` uses existing `run_macro`, writes bundle, returns compact paths. | `macros/artifacts.py` |
| 8 | Failure run writes `status=failed`, error text, and evidence path. | `macros/artifacts.py` |
| 9 | `macro_export_cli` writes import-safe CLI with `argparse` and no top-level browser launch. | `artifacts/script_export.py` |
| 10 | `macro_digest` truncates output and reports source size/truncated flag. | `artifacts/digest.py` |

Regression coverage:

| Existing Area | Regression Assertion |
|---|---|
| `macro_run` | Existing behavior unchanged. |
| `run_test_suite` | Existing macro test runner still passes. |
| `browser_export_script` | Existing export behavior unchanged. |
| Profiles | New tools are included in the `macros` profile. |
| Security | Artifact paths and export paths are recordings-root contained. |
| Secrets | Args/results redact sensitive values in MCP responses and files. |
| LOC policy | All files remain under 500 LOC. |

Verification commands:

```bash
uv run pytest tests/test_artifacts_*.py tests/test_macro_artifacts.py -v
uv run pytest tests/test_macros.py tests/test_macro_storage.py tests/test_runner.py tests/test_export.py -v
make lint
make test
```

## Rollout Phases

| Phase | Scope |
|---|---|
| Phase 1 | Core artifact store, evidence model, macro artifact plan/run/list. |
| Phase 2 | `macro_export_cli` reusable script output. |
| Phase 3 | `macro_digest` and future `browser_digest` / `recording_digest`. |
| Phase 4 | Scenario aggregation reuses artifact core. |

## Success Criteria

- A user can run a saved macro and receive a compact response pointing to a complete artifact bundle.
- The bundle contains stable JSON reports and a human-readable `summary.md`.
- The implementation adds no duplicate path, redaction, evidence, or report-writing logic outside the shared artifact core.
- Existing macro, runner, export, profile, and lint tests continue to pass.
- All new and modified Python files stay under 500 LOC.
