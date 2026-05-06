# Demo Media Redesign Design

Date: 2026-05-06

## Goal

Redesign Octowright's recorded demo media so the catalog feels deliberate and website-ready instead of mechanically exported. The current problems are:

- some demos end before the viewer can understand the outcome
- some composite dimensions are too cramped or awkward to present well
- burned-in text can block the important product surface
- some complex scenarios may need a different medium than one flattened composite

The redesign should keep the pipeline fully regenerable from Octowright's canonical demo bundles. It should improve presentation without introducing a manual editing workflow or a separate marketing-only asset path.

## Non-Goals

- building a separate app-hosted demo browser again
- hand-editing videos outside the Octowright pipeline
- forcing one global aspect-ratio rule across every demo
- creating arbitrary freeform rendering logic per bundle with no approved boundaries

## Design Summary

The media pipeline should move from a small set of composition behaviors to a curated presentation system. Each demo bundle should declare an approved presentation mode plus per-demo presentation direction: delivery shape, overlay policy, timing holds, pane priority, and whether the demo should emit a composite, synced multi-video outputs, or both.

This is intentionally custom per demo, but not unconstrained. Demos should choose from approved presentation patterns so the catalog still looks like one product.

## Presentation Model

Each demo gets an explicit presentation mode. Initial approved modes:

- `single-clean`
  - One browser view.
  - Minimal or no overlay.
  - Appropriate when the browser surface itself is the story.
- `hero-composite`
  - Curated multi-pane composition for fast viewing and website placement.
  - Appropriate when the scenario benefits from a single framed narrative.
- `sync-multi`
  - Multiple synchronized videos or panes generated from the same run.
  - Appropriate when one flattened composite would make the important UI unreadable.
- `artifact-first`
  - Recording whose visual surface is secondary to replay/export/report artifacts.
  - Appropriate when the demo primarily proves determinism, replay, or testing outputs.

Custom per demo means selecting and configuring one of these approved patterns, not inventing ad hoc post-processing behavior for every bundle.

## Overlay Policy

Overlays should become conservative by default.

Rules:

- overlays are metadata, not the main visual event
- overlays should be translucent, low-contrast, and edge-anchored
- overlays should use safe areas defined by presentation mode or per-demo configuration
- overlays should be small enough not to dominate the frame
- overlays should be removable entirely for demos where even subtle text interferes

Allowed overlay content should be short and utilitarian:

- demo title
- engine name
- role name
- short phase label

Longer explanations belong in the surrounding website/tutorial chrome, not burned into the video. The default must not be an opaque top banner.

## Timing Policy

Demos should end on visual completion, not merely when the script stops.

Each demo should be able to declare:

- intro hold
- action phase
- milestone waits where needed
- outro hold
- minimum duration floor

Rules:

- very short demos should usually gain a brief intro and a more generous outro
- end states must linger long enough for the viewer to read and understand them
- milestone waits are allowed only where they improve comprehension of an important transition
- timing changes should clarify the scenario, not pad it artificially

## Composite Versus Synced Multi-Video

The output mode should be driven by readability pressure.

Use a composite when:

- the important action remains readable at the resulting pane sizes
- the combined composition tells a cleaner story than separate players
- the website needs a compact hero asset

Use synced multi-video when:

- the composite would shrink critical UI too far
- multiple panes matter equally
- the viewer benefits from following distinct engines or roles independently

Use both when:

- a fast hero overview and a deep inspection path are both valuable

There is intentionally no global default that says every complex demo must have both outputs. This remains a per-demo judgment made inside approved patterns.

## Declarative Presentation Metadata

The renderer should be driven by explicit bundle metadata instead of one-off postprocessing scripts.

Each bundle should gain a dedicated presentation/media block that can express:

- approved presentation mode
- primary deliverable versus supporting deliverables
- canvas strategy or layout preset
- overlay style and placement policy
- intro/outro timing
- milestone waits
- pane priorities and labels
- whether synced multi-video companions are emitted
- site/tutorial export hints for which asset is primary

This must remain declarative. Regeneration should still come from the Octowright pipeline, not manual asset assembly.

## First-Pass Treatment Map

The first implementation pass should explicitly retune the existing demo set instead of waiting for generic renderer improvements to fix them automatically.

### `first-run-session`

- mode: `single-clean`
- stronger canvas
- slightly longer outro
- overlay optional or disabled

### `macro-replay-loop`

- mode: `artifact-first`
- clearer final linger
- minimal metadata
- no prominent title treatment

### `cross-engine-trio`

- move away from the current `1920x360` feel
- either a taller curated composite or a `sync-multi` companion
- emphasis on preserving browser readability across engines

### `role-based-duo`

- move away from the current `1920x540` thin-strip feel
- use a composite with more vertical breathing room or paired synced panes
- emphasize readable role differentiation

### `fixture-lab`

- mode: `artifact-first` or `single-clean`
- choose based on whether deterministic setup or visible browser proof is the actual story

### `verify-suite`

- mode: `artifact-first`
- readable end state
- stronger relationship between the video and its exported artifacts

### `seven-mix-orchestration`

- mode: `hero-composite`
- deliberately art-directed flagship output
- optional synced panes if the composite still hides too much of the important action

## Output Model

The artifact model should distinguish between website-facing hero assets and deeper inspection assets.

Per demo, the pipeline may emit:

- primary hero video
- optional synced pane videos
- primary poster
- optional per-pane posters
- artifact manifest describing relationships between outputs
- tutorial/site export metadata describing which asset should autoplay by default and which are secondary

This lets downstream consumers such as `site-octowright-com` choose the right asset intentionally instead of guessing from file names alone.

## Success Criteria

The redesign is successful when:

- no demo feels accidentally shaped
- overlays never block the primary action
- demos that were too short now feel complete
- complex demos preserve readability, even if that requires multiple synchronized outputs
- the catalog still feels coherent because outputs come from approved presentation patterns
- all assets remain regenerable from canonical Octowright demo bundles

## Implementation Implications

The implementation plan should cover:

- schema evolution for per-demo presentation metadata
- renderer changes for approved presentation modes
- overlay restyling and safe-zone placement
- timing controls for intro/outro/milestones
- sync-group generation for demos that need multi-video outputs
- export metadata updates for tutorial and website consumers
- per-demo retuning of the current bundle catalog

## Open Design Decision Resolved

The redesign deliberately does not impose one canonical dimension standard on every demo. The user preference is scenario-led presentation with custom but approved treatments. Consistency should come from quality and bounded patterns, not from forcing every scenario into the same shape.
