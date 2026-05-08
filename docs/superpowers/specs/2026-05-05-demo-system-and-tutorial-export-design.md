# Demo System And Tutorial Export Design

**Date:** 2026-05-05
**Status:** Approved

## Problem

Octowright has useful example scenarios and macros, but it does not yet have a coherent demo system.

Current gaps:

- `examples/` behaves more like fixture material than a curated product demo catalog.
- Recorded artifacts exist at the session level, not as named reusable demo bundles.
- There is no first-class dashboard surface for browsing recorded demos.
- There is no single regeneration path that produces both runnable demos and website-grade collateral.
- There is no clean export shape for a companion `octowright/tutorial` repo.

The result is that evaluators, users, and contributors all have to assemble the story themselves.

## Goals

- Create a comprehensive runnable demo system inside `octowright`.
- Make the same demos usable for product evaluation, learning, and regression.
- Produce deterministic offline recordings suitable for website and marketing use.
- Support first-class video and structured replay artifacts from the same execution path.
- Introduce a curated 7-demo hero tour plus a broader indexed supporting library.
- Add two discovery surfaces:
  - a generated repo demo index
  - a dashboard `Demo Gallery`
- Make hero demos exportable to a separate `octowright/tutorial` repo without making that repo the source of truth.

## Non-Goals

- Do not move canonical demo logic into a separate tutorial repo.
- Do not require external network access for the core demo set.
- Do not make marketing-only recordings that cannot be rerun as product demos.
- Do not collapse every example into a hero-level production asset on the first pass.

## Audiences

This system must serve all three audiences equally:

- **Evaluators** need a fast, polished, credible product tour.
- **Existing users** need runnable tutorials and concrete orchestration patterns.
- **Contributors** need deterministic recordings and repeatable demo/regression fixtures.

## Decision Summary

The approved direction is a **dual-layer catalog**:

- Layer 1: a curated 7-demo hero tour for websites, walkthroughs, onboarding, and live demos
- Layer 2: a fuller feature-indexed library built from the same bundle model

The core demos stay in `octowright`. A separate `octowright/tutorial` repo may mirror the hero tier for onboarding and marketing packaging, but it must consume artifacts and metadata exported from `octowright` rather than authoring the canonical demo logic itself.

## Architecture

### 1. Demo Bundles As The Source Of Truth

Each demo will be represented by a bundle with explicit metadata, runnable source definitions, and generated artifacts.

Each bundle answers:

- what the demo is
- how to run it
- what artifacts it should emit
- where website/tutorial assets live
- how downstream consumers can identify it

The recommended model is manifest-driven rather than script-only. Recorder scripts still exist, but they should execute against explicit bundle metadata instead of acting as the catalog themselves.

### 2. Dual-Layer Catalog

The catalog has two views over the same underlying bundle set.

**Hero layer**

- 7 flagship demos
- polished narrative titles and summaries
- poster screenshots and website-grade videos
- prioritized ordering for repo/docs/dashboard/tutorial use

**Supporting library**

- broader set of scenario/macro/demo bundles
- feature-oriented indexing and filtering
- lower presentation burden than heroes, but still runnable and recordable

This prevents a split between “marketing demos” and “real demos.”

### 3. Discovery Surfaces

Two primary entry points will be added:

- **Generated repo index**
  - hero demos first
  - full library below
  - commands, tags, artifact links, and regeneration status
- **Dashboard Demo Gallery**
  - demo-centric, not session-centric
  - can browse recorded demos even when no live session exists
  - actions such as `Run`, `Open Replay`, `Watch Video`, and `Reveal Sources`

The current session/recording UI is not enough because it organizes around live instances and raw artifacts, not curated named demos.

### 4. Offline Determinism

The core demo set must run offline and remain deterministic.

That implies:

- stable local fixture pages rather than heavy reliance on `about:blank` injection for hero demos
- seeded local assets and mock APIs
- deterministic scenario behavior and visible UI state
- repeatable recording output suitable for website capture

`about:blank` injection may remain acceptable for tiny smoke/supporting demos, but not as the dominant hero-demo presentation mechanism.

## Hero Demo Set

The first pass will produce 7 flagship demos:

1. **`first-run-session`**
   Single-browser launch, visible interaction, recording capture, and artifact inspection.

2. **`macro-replay-loop`**
   Record a useful interaction once, replay it, and show that replay is inspectable and stable.

3. **`cross-engine-trio`**
   Chromium, Firefox, and WebKit launched against the same deterministic target.

4. **`role-based-duo`**
   Two participants with different roles and role-filtered macro broadcast.

5. **`fixture-lab`**
   Shared route mocks, dialog policy, and controlled deterministic app state.

6. **`verify-suite`**
   Scenario-driven verification with replay and report artifacts produced together.

7. **`seven-mix-orchestration`**
   The flagship multi-window, mixed-engine, coordinated scenario intended to anchor website and product storytelling.

This set is intentionally balanced across:

- multi-browser orchestration
- recording/replay/debugging workflows
- dashboard/storytelling value

## Bundle Shape

Each demo bundle should have a structure in this spirit:

```text
demo/
  bundles/
    <demo-id>/
      demo.yaml
      scenario/
      macros/
      seed/
      artifacts/
      manifest.json
```

### `demo.yaml`

Canonical authored metadata, including:

- `id`
- `title`
- `summary`
- `audiences`
- `tags`
- `hero: true|false`
- `engines`
- `roles`
- `source_refs`
- `artifact_expectations`
- `regen`
- `tutorial_export`

### `scenario/`

Scenario definitions or references used to execute the demo.

### `macros/`

Demo-specific macros or links to shared macro definitions.

### `seed/`

Local offline HTML, CSS, JSON, or other fixtures needed for deterministic output.

### `artifacts/`

Generated outputs, including:

- replay artifact(s)
- website video(s)
- screenshots/posters
- optional reports such as JUnit XML

### `manifest.json`

Generated normalized metadata for dashboard and index consumers. This avoids teaching every consumer to parse authored bundle files directly.

## Artifact Model

The approved output model is dual-primary:

- **browser video**
- **structured replay artifact**

Each hero/supporting demo may additionally include:

- screenshots/posters
- source references
- run metadata
- optional reports

This includes and subsumes the earlier alternatives:

- website-grade video is still produced
- replay remains a first-class artifact rather than an afterthought
- each bundle still carries the full runnable source plus generated collateral

## Recording Pipeline

The pipeline should expose three operating modes:

- **record one**
  - regenerate one demo bundle end to end
- **record heroes**
  - regenerate only the 7 flagship demos
- **record all**
  - regenerate the entire catalog and rebuild indexes/manifests

The key architectural rule:

> Video capture and replay capture must come from the same runnable flow.

There must not be a separate marketing-only path.

## Repo And Dashboard Outputs

### Repo Index

A generated index should present:

- hero demos first
- supporting demos below
- tags and audience hints
- artifact links
- run/regenerate commands
- generation status and timestamps

This may live at a path like `demo/INDEX.md` or another dedicated generated location, but it must be clearly product-facing and not hidden inside test/docs internals.

### Dashboard Demo Gallery

The gallery should present demos as curated entities rather than raw recordings.

Each card should include:

- title
- summary
- hero/supporting badge
- engines/roles involved
- artifact availability
- actions:
  - `Run`
  - `Open Replay`
  - `Watch Video`
  - `Reveal Sources`

The gallery must work even when there are no live browser sessions.

## Relationship To `examples/`

`examples/` should remain available for lightweight scenario and macro material, but it should not remain the only or primary demo surface.

The new demo catalog should become the authoritative curated system. Existing examples may be:

- promoted into bundles
- referenced by bundles
- kept as low-level supporting material

This avoids mixing “small example fixture” concerns with “website-grade demo bundle” concerns.

## Tutorial Repo Split

The approved model for a separate `octowright/tutorial` repo is **hybrid ownership**.

### `octowright` owns

- canonical runnable demo definitions
- recording scripts and orchestration
- replay artifacts
- generated website/demo assets
- regeneration provenance and metadata

### `octowright/tutorial` owns

- tutorial prose
- onboarding flow
- cleaner marketing-friendly packaging
- mirrored hero assets and references

### Export Rule

Hero demos must be exportable from `octowright` into a stable bundle shape consumable by the tutorial repo.

That export should include:

- title and summary metadata
- poster image(s)
- website video
- replay artifact
- source links or source identifiers
- provenance about how the demo was generated

The tutorial repo is therefore a consumer of hero bundles, not their authoring home.

## API And Metadata Direction

The dashboard should consume generated demo metadata through a dedicated surface rather than scraping raw filesystem state ad hoc.

Likely needs:

- demo list endpoint
- demo detail endpoint
- artifact link/detail payloads
- run/regenerate actions for local use

Exact route shapes can be finalized in planning, but the design intent is explicit: demo browsing should be based on generated manifests, not inferred from generic session recordings.

## Error Handling

The demo system should fail clearly in three classes:

### Structural failures

- missing required bundle files
- missing declared artifacts
- invalid metadata

### Runtime failures

- demo cannot run
- expected artifacts are not produced
- replay/video generation fails

### Presentation failures

- hero demo missing poster image
- hero video missing expected dimensions or codec
- generated index/gallery metadata out of sync

The `record all` path should report partial failures without hiding successful demo output from other bundles.

## Verification

Verification should cover both correctness and presentation quality.

### Structural checks

- bundle metadata completeness
- path validity
- artifact expectation consistency

### Runtime checks

- demo execution succeeds
- replay artifact is readable
- required outputs are created

### Presentation checks

- hero screenshots exist
- hero video dimensions/codecs match website expectations

### Drift checks

- generated metadata matches bundle contents
- repo index stays synchronized with bundle manifests

## Why This Approach

This approach is preferred over a script-only or tutorial-repo-first design because it keeps product truth, demo truth, and marketing truth aligned.

If `octowright/tutorial` becomes canonical, demos will drift from the product.

If the main repo remains canonical but exports stable hero bundles, then:

- product demos stay honest
- contributors can rerun everything
- users can learn from the real assets
- tutorial and marketing surfaces can stay polished without forking behavior

## Open Planning Items

The implementation plan should resolve:

- exact on-disk bundle paths and naming
- exact API route shapes for gallery consumption
- how much existing `examples/` content is promoted vs referenced
- exact commands and scripts for `record one`, `record heroes`, and `record all`
- tutorial-export artifact format and sync mechanism
- whether generated videos are checked in directly or excluded while keeping everything needed to regenerate VOD

Regardless of that final binary-artifact decision, the repo must keep the complete authored source of regeneration:

- manifests
- scripts
- scenarios
- macros
- seed fixtures
- metadata needed to reproduce website/tutorial outputs

The last point is intentionally left as a planning concern because the user requirement is to keep everything needed to regenerate VOD, not necessarily to commit every generated binary artifact immediately.
