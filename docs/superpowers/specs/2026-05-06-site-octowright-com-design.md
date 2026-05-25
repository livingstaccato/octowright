# site-octowright-com Design

Date: 2026-05-06

## Goal

Create `site-octowright-com` as a separate public-facing website repo for Octowright, based on the same structural model as `site-pyvider-com`, while using Octowright’s recorded demo assets as first-class proof.

The site must work for three audiences at once:

- first-time evaluators
- existing users who want demos and runnable paths
- contributors and technically deep users

The site should explain Octowright clearly, show convincing recorded proof early, and provide direct paths into demos, setup, docs, and source.

## Source-of-Truth Model

Octowright remains the canonical source for:

- runnable demo bundles
- recorded video/poster/replay artifacts
- tutorial export payloads
- deeper technical docs and architecture material

`site-octowright-com` is the presentation layer. It consumes exported files from Octowright and republishes them in a cleaner public site.

For the first version:

- demo assets are copied into the site repo as checked-in files
- no deployment-time fetching is required
- automation can be added later once the content model stabilizes

## Repository Model

`site-octowright-com` should be a separate git repo.

It should follow the same general structure and operating model as `site-pyvider-com`:

- Hugo-based site
- small set of intentional homepage partials
- content-driven page structure
- checked-in static assets
- simple deployment story

One major addition is required relative to `site-pyvider-com`:

- a data/content ingestion layer for Octowright demo exports

That ingestion layer should use checked-in exported payloads and media from Octowright, likely as:

- imported JSON payloads in a site data/content directory
- imported video/poster/replay/report assets under static files
- Hugo templates that render demo index and demo detail pages from those payloads

## Visual Direction

The chosen visual direction is split tone:

- editorial structure and typography similar to `site-pyvider-com`
- dramatic demo proof moments in the hero and demo gallery

The page should feel composed, premium, and product-literate, not like a dashboard or generic SaaS site.

Visual rules:

- Octowright banner/logo must be visibly used
- hero is brand-led first, demo-led second
- recorded demo media is allowed to be visually dramatic
- supporting sections remain restrained and readable
- avoid collapsing into a card-grid homepage

Primary brand assets to incorporate:

- `docs/images/brand/octowright-banner.png`
- `docs/images/brand/octowright-logo-512.png`

## Homepage Strategy

The homepage should use a hybrid model:

- explain the product with clarity
- prove it immediately with real recorded assets

It should not behave like a docs homepage.
It should not behave like a pure video gallery either.

### Hero

The hero should:

- use the Octowright banner as the dominant visual anchor
- include silent autoplay flagship media as supporting proof
- make the Octowright brand unmistakable in the first viewport

Initial flagship media choice:

- `seven-mix-orchestration`

This should be treated as the current homepage hero asset, while keeping room for a future custom-edited hero cut assembled from multiple demos.

Hero CTAs:

- primary: `Watch the flagship demo`
- secondary: `Browse all demos`

### Homepage Information Architecture

The homepage should contain these major sections, in this order:

1. Hero
2. Featured demos
3. How Octowright Works
4. Get Started
5. Why It’s Different
6. CLI / Workflow

### Featured Demos

The homepage should show only 3 featured demos, not the full gallery.

Reason:

- the homepage needs to stay selective and persuasive
- `/demos` should carry the broader library
- three demos are enough to show range without turning the landing page into a catalog

Recommended featured set:

1. `seven-mix-orchestration`
2. `cross-engine-trio`
3. `verify-suite`

These together communicate:

- flagship orchestration
- engine breadth
- technical rigor and artifacts

### How Octowright Works

This section should explain the core model:

- browsers
- personas
- scenarios
- recordings
- exported artifacts

It should include a prominent dashboard screenshot panel using an existing recorded dashboard image, not just abstract diagrams.

### Get Started

The homepage should show one canonical quickstart path, not user-type tabs.

That quickstart should end with both:

- launching the dashboard locally
- running one recorded demo bundle

This creates a complete first-run loop instead of stopping after install or startup alone.

### Why It’s Different

This section should position Octowright around its differentiators:

- multi-browser orchestration
- replay artifacts
- deterministic demo generation
- scenario-based automation
- tutorial/export value

### CLI / Workflow

Octowright should not copy the Pyvider homepage code-example section directly.

Instead, that slot should become a CLI/workflow section showing the real user path:

- install
- `octowright serve`
- launch or run a demo/scenario
- inspect recordings and artifacts

## Top-Level Navigation

Initial top-level nav should be:

- `Demos`
- `Get Started`
- `Docs`
- `GitHub`

Reason:

- `Demos` reflects the proof-first funnel
- `Get Started` is the shortest path for a user ready to try it
- `Docs` remains the expected reference destination
- `GitHub` is important for a technical audience

`Tutorial` should not be top-level in the first version.

Instead:

- tutorial paths live under demos and per-demo destinations
- they can be promoted later if the tutorial surface becomes large enough

## Page Map

Initial page map should be:

- `/`
  homepage
- `/demos/`
  technical-first hybrid gallery
- `/demos/<slug>/`
  per-demo detail pages
- `/get-started/`
  canonical quickstart and install path
- `/docs/`
  curated docs landing page
- optional small curated docs pages under `/docs/...`
- outbound `GitHub`

This keeps the site compact and intentional, like `site-pyvider-com`, instead of trying to absorb the entire Octowright docs tree immediately.

## Demos Destination

`/demos` should be a hybrid page leaning technical-first.

It still needs polish, but its center of gravity should be operational and artifact-aware.

Each demo entry should surface:

- title
- summary
- poster/video
- replay/export/report/roster awareness where available
- tutorial/export path
- relationship to the canonical Octowright bundle

The `/demos` page structure should be:

- hero demos first
- broader library below
- each entry linking to a dedicated demo detail page

## Demo Detail Pages

Per-demo pages should be generated from Octowright tutorial export payloads.

Each page should include:

- demo title
- summary
- embedded video or poster
- short explanation of what the demo proves
- artifact links:
  - replay JSONL
  - exported scripts
  - manifest
  - report or roster where applicable
- tutorial/run path back toward Octowright
- source metadata showing that the site artifact came from the canonical Octowright bundle

These pages should help all three audiences:

- evaluators can watch and understand
- users can run and inspect
- contributors can trace the artifact back to its source bundle

## Docs Model

Docs should use a hybrid model.

`site-octowright-com` should own a small set of curated, high-signal pages such as:

- `Get Started`
- a concise `Docs` landing page
- possibly one or two polished workflow pages

Deeper technical reference should continue to live in the main Octowright repo for now.

This avoids immediate duplication and drift while still giving the site a polished front-door docs experience.

## Asset Sync Model

The first version should use checked-in copied assets.

Flow:

1. Octowright generates `demo/tutorial-export/`
2. selected exported payloads and assets are copied into `site-octowright-com`
3. the copied files are committed in the site repo
4. Hugo templates render local data and media

This keeps the first version:

- deterministic
- reviewable
- easy to deploy

Later, a sync script can automate the copy/update process.

## Relationship To Existing Octowright Work

The site should explicitly leverage the current Octowright demo/export pipeline:

- hero media generated from real recorded demos
- demo detail pages backed by tutorial export payloads
- technical artifact links driven by checked-in outputs

This is important because the site should reflect real product truth, not marketing-only mock assets.

## Non-Goals For First Version

The first version should not:

- fetch assets dynamically at runtime
- attempt to migrate the full Octowright docs tree into the site repo
- expose every demo on the homepage
- promote tutorials to top-level navigation immediately
- invent a separate demo source-of-truth outside Octowright

## Recommended Implementation Direction

Use the same overall site pattern as `site-pyvider-com`, but adapt the content architecture for Octowright:

- brand-led homepage hero
- supporting flagship demo media in the first viewport
- 3 curated homepage demos
- technical-first `/demos`
- curated `Get Started` and `Docs`
- imported checked-in exported assets from Octowright

This preserves the proven structural discipline of Pyvider’s site while using Octowright’s strongest differentiator: real recorded browser-demo proof.
