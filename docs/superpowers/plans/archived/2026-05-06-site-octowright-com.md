> **STATUS: shipped — archived 2026-05-25.** Deliverables landed on `feat/local-playground-integration-final` (PR #52); see commits 6d146a7..a6ff51b for the implementation trail. This file is kept verbatim as the spec snapshot.

# site-octowright-com Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `site-octowright-com` as a separate Hugo site repo, structurally based on `site-pyvider-com`, using checked-in Octowright tutorial-export assets for the homepage hero, demos gallery, demo detail pages, and curated getting-started/docs content.

**Architecture:** Keep Octowright as the source of truth for demo artifacts and technical docs, and make `site-octowright-com` a thin presentation layer. Start with checked-in copied assets and a local sync script, then render homepage sections and demo pages from imported JSON/data rather than hand-maintaining duplicate content.

**Tech Stack:** Hugo, checked-in static assets, JSON data files, small Python sync script, pytest for site-data smoke checks, Hugo build for final verification.

---

## File Structure

### New repo to create

- Create: `../site-octowright-com/`

### Expected top-level files in the new repo

- Create: `../site-octowright-com/hugo.toml`
- Create: `../site-octowright-com/README.md`
- Create: `../site-octowright-com/.gitignore`
- Create: `../site-octowright-com/content/_index.md`
- Create: `../site-octowright-com/content/get-started.md`
- Create: `../site-octowright-com/content/docs.md`
- Create: `../site-octowright-com/content/demos/_index.md`
- Create: `../site-octowright-com/layouts/index.html`
- Create: `../site-octowright-com/layouts/_default/baseof.html`
- Create: `../site-octowright-com/layouts/_default/list.html`
- Create: `../site-octowright-com/layouts/_default/single.html`
- Create: `../site-octowright-com/layouts/partials/head.html`
- Create: `../site-octowright-com/layouts/partials/nav.html`
- Create: `../site-octowright-com/layouts/partials/footer.html`
- Create: `../site-octowright-com/layouts/partials/hero.html`
- Create: `../site-octowright-com/layouts/partials/featured-demos.html`
- Create: `../site-octowright-com/layouts/partials/how-it-works.html`
- Create: `../site-octowright-com/layouts/partials/get-started-strip.html`
- Create: `../site-octowright-com/layouts/partials/why-different.html`
- Create: `../site-octowright-com/layouts/partials/cli-workflow.html`
- Create: `../site-octowright-com/layouts/demos/list.html`
- Create: `../site-octowright-com/layouts/demos/single.html`
- Create: `../site-octowright-com/static/css/site.css`
- Create: `../site-octowright-com/static/img/`
- Create: `../site-octowright-com/static/demo-assets/`
- Create: `../site-octowright-com/data/site/home.json`
- Create: `../site-octowright-com/data/demos/index.json`
- Create: `../site-octowright-com/data/demos/heroes/*.json`
- Create: `../site-octowright-com/scripts/sync_octowright_exports.py`
- Create: `../site-octowright-com/tests/test_sync_octowright_exports.py`

### Source files to read while implementing

- Read: `../site-pyvider-com/hugo.toml`
- Read: `../site-pyvider-com/layouts/index.html`
- Read: `../site-pyvider-com/layouts/partials/*.html`
- Read: `../site-pyvider-com/static/css/site.css`
- Read: `demo/tutorial-export/index.json`
- Read: `demo/tutorial-export/manifest.json`
- Read: `docs/images/brand/octowright-banner.png`
- Read: `docs/images/brand/octowright-logo-512.png`

---

### Task 1: Scaffold site-octowright-com from the Pyvider structure

**Files:**
- Create: `../site-octowright-com/hugo.toml`
- Create: `../site-octowright-com/.gitignore`
- Create: `../site-octowright-com/README.md`
- Create: `../site-octowright-com/layouts/_default/baseof.html`
- Create: `../site-octowright-com/layouts/_default/list.html`
- Create: `../site-octowright-com/layouts/_default/single.html`
- Create: `../site-octowright-com/layouts/index.html`
- Create: `../site-octowright-com/layouts/partials/head.html`
- Create: `../site-octowright-com/layouts/partials/nav.html`
- Create: `../site-octowright-com/layouts/partials/footer.html`
- Create: `../site-octowright-com/static/css/site.css`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Write the failing scaffold smoke test**

```python
from pathlib import Path


def test_site_scaffold_files_exist() -> None:
    root = Path("../site-octowright-com")
    expected = [
        "hugo.toml",
        "layouts/index.html",
        "layouts/_default/baseof.html",
        "layouts/partials/nav.html",
        "static/css/site.css",
    ]
    missing = [path for path in expected if not (root / path).exists()]
    assert missing == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_site_scaffold_files_exist -v`

Expected: FAIL with missing paths under `../site-octowright-com`

- [ ] **Step 3: Create the initial Hugo scaffold**

```toml
# ../site-octowright-com/hugo.toml
baseURL = "https://octowright.com/"
languageCode = "en-us"
title = "Octowright — Recorded multi-browser orchestration"
enableGitInfo = false
enableRobotsTXT = true

[params]
  description = "Launch multiple browsers, record every action, and turn runnable scenarios into website-ready demos."
  github = "https://github.com/livingstaccato/octowright"
```

```html
<!-- ../site-octowright-com/layouts/index.html -->
{{ define "main" }}
  {{ partial "hero.html" . }}
  {{ partial "featured-demos.html" . }}
  {{ partial "how-it-works.html" . }}
  {{ partial "get-started-strip.html" . }}
  {{ partial "why-different.html" . }}
  {{ partial "cli-workflow.html" . }}
{{ end }}
```

```html
<!-- ../site-octowright-com/layouts/_default/baseof.html -->
<!doctype html>
<html lang="en">
  <head>{{ partial "head.html" . }}</head>
  <body>
    {{ partial "nav.html" . }}
    <main>{{ block "main" . }}{{ end }}</main>
    {{ partial "footer.html" . }}
  </body>
</html>
```

- [ ] **Step 4: Run the scaffold smoke test**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_site_scaffold_files_exist -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add .
git -C ../site-octowright-com commit -m "feat: scaffold site octowright com"
```

### Task 2: Add the export sync path from Octowright tutorial-export into the site repo

**Files:**
- Create: `../site-octowright-com/scripts/sync_octowright_exports.py`
- Create: `../site-octowright-com/data/demos/`
- Create: `../site-octowright-com/static/demo-assets/`
- Modify: `../site-octowright-com/README.md`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Write the failing sync test**

```python
import json
from pathlib import Path

from scripts.sync_octowright_exports import sync_exports


def test_sync_exports_copies_index_hero_payloads_and_assets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "site"
    (source / "heroes").mkdir(parents=True)
    (source / "artifacts" / "seven-mix-orchestration").mkdir(parents=True)
    (source / "index.json").write_text('{"heroes":[{"id":"seven-mix-orchestration","payload":"heroes/seven-mix-orchestration.json","artifacts_dir":"artifacts/seven-mix-orchestration"}]}')
    (source / "heroes" / "seven-mix-orchestration.json").write_text('{"title":"Seven Mix Orchestration"}')
    (source / "artifacts" / "seven-mix-orchestration" / "poster.png").write_bytes(b"poster")

    sync_exports(source_root=source, site_root=target)

    assert json.loads((target / "data" / "demos" / "index.json").read_text())["heroes"][0]["id"] == "seven-mix-orchestration"
    assert (target / "data" / "demos" / "heroes" / "seven-mix-orchestration.json").exists()
    assert (target / "static" / "demo-assets" / "seven-mix-orchestration" / "poster.png").read_bytes() == b"poster"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_sync_exports_copies_index_hero_payloads_and_assets -v`

Expected: FAIL with import or file-not-found errors

- [ ] **Step 3: Implement the minimal sync script**

```python
# ../site-octowright-com/scripts/sync_octowright_exports.py
from __future__ import annotations

import shutil
from pathlib import Path


def sync_exports(source_root: Path, site_root: Path) -> None:
    data_root = site_root / "data" / "demos"
    static_root = site_root / "static" / "demo-assets"
    shutil.rmtree(data_root, ignore_errors=True)
    shutil.rmtree(static_root, ignore_errors=True)
    shutil.copytree(source_root / "heroes", data_root / "heroes")
    shutil.copytree(source_root / "artifacts", static_root)
    shutil.copy2(source_root / "index.json", data_root / "index.json")
    manifest = source_root / "manifest.json"
    if manifest.exists():
        data_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(manifest, data_root / "manifest.json")


if __name__ == "__main__":
    repo = Path(__file__).resolve().parents[2]
    sync_exports(
        source_root=repo.parent / "octowright" / "demo" / "tutorial-export",
        site_root=repo,
    )
```

- [ ] **Step 4: Run the sync test**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_sync_exports_copies_index_hero_payloads_and_assets -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add scripts/sync_octowright_exports.py data static README.md tests/test_sync_octowright_exports.py
git -C ../site-octowright-com commit -m "feat: sync octowright tutorial exports into site"
```

### Task 3: Build the homepage with banner-led hero and 3-featured demo strip

**Files:**
- Create: `../site-octowright-com/content/_index.md`
- Create: `../site-octowright-com/data/site/home.json`
- Create: `../site-octowright-com/layouts/partials/hero.html`
- Create: `../site-octowright-com/layouts/partials/featured-demos.html`
- Modify: `../site-octowright-com/static/css/site.css`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Write the failing homepage content test**

```python
from pathlib import Path


def test_homepage_partials_reference_banner_and_featured_demos() -> None:
    root = Path("../site-octowright-com")
    hero = (root / "layouts/partials/hero.html").read_text(encoding="utf-8")
    featured = (root / "layouts/partials/featured-demos.html").read_text(encoding="utf-8")
    assert "octowright-banner.png" in hero
    assert "Watch the flagship demo" in hero
    assert "Browse all demos" in hero
    assert "seven-mix-orchestration" in featured
    assert "cross-engine-trio" in featured
    assert "verify-suite" in featured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_homepage_partials_reference_banner_and_featured_demos -v`

Expected: FAIL because the partials do not yet contain the required references

- [ ] **Step 3: Implement the hero and featured-demo partials**

```html
<!-- ../site-octowright-com/layouts/partials/hero.html -->
<section class="hero">
  <div class="hero-copy">
    <img src="/img/octowright-banner.png" alt="Octowright banner" class="hero-brand">
    <h1>Recorded multi-browser orchestration for real browser workflows.</h1>
    <p>Launch multiple browsers, run deterministic scenarios, and turn the resulting artifacts into demos, tutorials, and debugging proof.</p>
    <div class="hero-actions">
      <a href="/demos/seven-mix-orchestration/" class="button primary">Watch the flagship demo</a>
      <a href="/demos/" class="button secondary">Browse all demos</a>
    </div>
  </div>
  <div class="hero-proof">
    <video autoplay muted loop playsinline poster="/demo-assets/seven-mix-orchestration/poster.png">
      <source src="/demo-assets/seven-mix-orchestration/demo.mp4" type="video/mp4">
    </video>
  </div>
</section>
```

```html
<!-- ../site-octowright-com/layouts/partials/featured-demos.html -->
{{ $featured := slice "seven-mix-orchestration" "cross-engine-trio" "verify-suite" }}
<section class="featured-demos">
  <h2>Featured demos</h2>
  <div class="demo-grid">
    {{ range site.Data.demos.index.heroes }}
      {{ if in $featured .id }}
        <article class="demo-card">
          <a href="{{ printf "/demos/%s/" .id }}">
            <img src="{{ printf "/demo-assets/%s/poster.png" .id }}" alt="{{ .title }} poster">
            <h3>{{ .title }}</h3>
            <p>{{ .summary }}</p>
          </a>
        </article>
      {{ end }}
    {{ end }}
  </div>
</section>
```

- [ ] **Step 4: Run the homepage test**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_homepage_partials_reference_banner_and_featured_demos -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add content/_index.md data/site/home.json layouts/partials/hero.html layouts/partials/featured-demos.html static/css/site.css
git -C ../site-octowright-com commit -m "feat: add octowright homepage hero and featured demos"
```

### Task 4: Add How It Works, Get Started, Why It’s Different, and CLI/Workflow homepage sections

**Files:**
- Create: `../site-octowright-com/layouts/partials/how-it-works.html`
- Create: `../site-octowright-com/layouts/partials/get-started-strip.html`
- Create: `../site-octowright-com/layouts/partials/why-different.html`
- Create: `../site-octowright-com/layouts/partials/cli-workflow.html`
- Modify: `../site-octowright-com/static/css/site.css`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Write the failing homepage-section test**

```python
from pathlib import Path


def test_homepage_sections_include_dashboard_and_cli_path() -> None:
    root = Path("../site-octowright-com")
    how_it_works = (root / "layouts/partials/how-it-works.html").read_text(encoding="utf-8")
    cli = (root / "layouts/partials/cli-workflow.html").read_text(encoding="utf-8")
    get_started = (root / "layouts/partials/get-started-strip.html").read_text(encoding="utf-8")
    assert "octowright-dashboard-selftest-fixed.png" in how_it_works
    assert "octowright serve" in cli
    assert "scripts/demos/regenerate_website_heroes.py" not in cli
    assert "uv run python scripts/demos/record_demo.py first-run-session" in get_started
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_homepage_sections_include_dashboard_and_cli_path -v`

Expected: FAIL because the section partials do not yet exist or lack the required content

- [ ] **Step 3: Implement the section partials**

```html
<!-- ../site-octowright-com/layouts/partials/get-started-strip.html -->
<section class="get-started-strip">
  <h2>Get started</h2>
  <pre><code>uv sync
uv run octowright serve
uv run python scripts/demos/record_demo.py first-run-session</code></pre>
  <p>This canonical path ends with both launching the dashboard and running one recorded demo bundle.</p>
</section>
```

```html
<!-- ../site-octowright-com/layouts/partials/how-it-works.html -->
<section class="how-it-works">
  <div class="copy">
    <h2>How Octowright works</h2>
    <p>Browsers, personas, scenarios, recordings, and exported artifacts work together so the same run can be watched, replayed, and inspected.</p>
  </div>
  <div class="dashboard-shot">
    <img src="/img/octowright-dashboard-selftest-fixed.png" alt="Octowright dashboard screenshot">
  </div>
</section>
```

- [ ] **Step 4: Run the section test**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_homepage_sections_include_dashboard_and_cli_path -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add layouts/partials/how-it-works.html layouts/partials/get-started-strip.html layouts/partials/why-different.html layouts/partials/cli-workflow.html static/css/site.css
git -C ../site-octowright-com commit -m "feat: add homepage explainer and workflow sections"
```

### Task 5: Build the Demos index and demo detail pages from imported JSON

**Files:**
- Create: `../site-octowright-com/content/demos/_index.md`
- Create: `../site-octowright-com/layouts/demos/list.html`
- Create: `../site-octowright-com/layouts/demos/single.html`
- Modify: `../site-octowright-com/scripts/sync_octowright_exports.py`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Write the failing demo-page data test**

```python
import json
from pathlib import Path


def test_synced_demo_payload_contains_expected_detail_fields() -> None:
    payload = json.loads(
        Path("../site-octowright-com/data/demos/heroes/seven-mix-orchestration.json").read_text(encoding="utf-8")
    )
    assert payload["title"] == "Seven Mix Orchestration"
    assert "artifact_manifest" in payload
    assert "assets" in payload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_synced_demo_payload_contains_expected_detail_fields -v`

Expected: FAIL until the sync has been run into the site repo

- [ ] **Step 3: Implement demo list/detail templates and sync invocation**

```html
<!-- ../site-octowright-com/layouts/demos/list.html -->
{{ define "main" }}
<section class="demo-index">
  <h1>Demo library</h1>
  <p>Hero demos first, broader library below. Every entry links to a detail page with artifacts and runnable context.</p>
  <div class="demo-grid">
    {{ range site.Data.demos.index.heroes }}
      <article class="demo-card">
        <a href="{{ printf "/demos/%s/" .id }}">
          <img src="{{ printf "/demo-assets/%s/poster.png" .id }}" alt="{{ .title }} poster">
          <h2>{{ .title }}</h2>
          <p>{{ .summary }}</p>
        </a>
      </article>
    {{ end }}
  </div>
</section>
{{ end }}
```

```html
<!-- ../site-octowright-com/layouts/demos/single.html -->
{{ $slug := .File.ContentBaseName }}
{{ $demo := index site.Data.demos.heroes $slug }}
{{ define "main" }}
<article class="demo-detail">
  <h1>{{ $demo.title }}</h1>
  <p>{{ $demo.summary }}</p>
  <video controls poster="{{ printf "/demo-assets/%s/poster.png" $slug }}">
    <source src="{{ printf "/demo-assets/%s/demo.mp4" $slug }}" type="video/mp4">
  </video>
  <ul class="artifact-links">
    <li><a href="{{ printf "/demo-assets/%s/replay.jsonl" $slug }}">Replay JSONL</a></li>
    <li><a href="{{ printf "/demo-assets/%s/replay.py" $slug }}">Python export</a></li>
    <li><a href="{{ printf "/demo-assets/%s/manifest.json" $slug }}">Artifact manifest</a></li>
  </ul>
</article>
{{ end }}
```

- [ ] **Step 4: Run the sync script and demo-data test**

Run: `python ../site-octowright-com/scripts/sync_octowright_exports.py && uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_synced_demo_payload_contains_expected_detail_fields -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add content/demos/_index.md layouts/demos/list.html layouts/demos/single.html data/demos static/demo-assets
git -C ../site-octowright-com commit -m "feat: render demo index and detail pages from tutorial exports"
```

### Task 6: Add curated Get Started and Docs pages with Hugo build verification

**Files:**
- Create: `../site-octowright-com/content/get-started.md`
- Create: `../site-octowright-com/content/docs.md`
- Modify: `../site-octowright-com/layouts/partials/nav.html`
- Modify: `../site-octowright-com/README.md`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Write the failing curated-page test**

```python
from pathlib import Path


def test_curated_pages_exist_for_get_started_and_docs() -> None:
    root = Path("../site-octowright-com/content")
    assert (root / "get-started.md").exists()
    assert (root / "docs.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py::test_curated_pages_exist_for_get_started_and_docs -v`

Expected: FAIL because the content pages do not yet exist

- [ ] **Step 3: Add the curated pages**

```markdown
<!-- ../site-octowright-com/content/get-started.md -->
---
title: "Get Started"
---

# Get Started

1. Install dependencies with `uv sync`
2. Launch the dashboard with `uv run octowright serve`
3. Generate a recorded demo with `uv run python scripts/demos/record_demo.py first-run-session`
4. Continue into `/demos/` or the main Octowright docs
```

```markdown
<!-- ../site-octowright-com/content/docs.md -->
---
title: "Docs"
---

# Docs

Use this page as the curated front door, then link outward to the deeper Octowright documentation for architecture, API surface, and operational details.
```

- [ ] **Step 4: Run Hugo build verification**

Run: `cd ../site-octowright-com && hugo --minify`

Expected: PASS and generate `public/` without template errors

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add content/get-started.md content/docs.md layouts/partials/nav.html README.md
git -C ../site-octowright-com commit -m "feat: add curated get started and docs pages"
```

### Task 7: Final polish, asset import, and release-ready verification

**Files:**
- Modify: `../site-octowright-com/static/img/*`
- Modify: `../site-octowright-com/static/css/site.css`
- Modify: `../site-octowright-com/README.md`
- Test: `../site-octowright-com/tests/test_sync_octowright_exports.py`

- [ ] **Step 1: Import brand and screenshot assets**

Copy:

```bash
cp docs/images/brand/octowright-banner.png ../site-octowright-com/static/img/octowright-banner.png
cp docs/images/brand/octowright-logo-512.png ../site-octowright-com/static/img/octowright-logo-512.png
cp recordings/20260505T181456Z-chromium-0c1d8ccb1f21-octowright-dashboard-selftest-fixed.png ../site-octowright-com/static/img/octowright-dashboard-selftest-fixed.png
```

- [ ] **Step 2: Run the full site smoke test file**

Run: `uv run pytest ../site-octowright-com/tests/test_sync_octowright_exports.py -q`

Expected: PASS

- [ ] **Step 3: Run sync and Hugo build together**

Run: `cd ../site-octowright-com && python scripts/sync_octowright_exports.py && hugo --minify`

Expected: PASS with demo pages and homepage rendering correctly

- [ ] **Step 4: Review the built site locally**

Run: `cd ../site-octowright-com && hugo server -D --port 1778`

Expected: local site available on `http://localhost:1778`

Manual checks:

- hero shows Octowright banner prominently
- hero video autoplays silently
- homepage shows exactly 3 featured demos
- `/demos/` shows hero-first technical gallery
- `Get Started` and `Docs` are reachable from nav

- [ ] **Step 5: Commit**

```bash
git -C ../site-octowright-com add .
git -C ../site-octowright-com commit -m "feat: finalize site octowright com first public version"
```

---

## Self-Review

### Spec coverage

- Separate repo based on `site-pyvider-com`: covered by Tasks 1 and 7
- Brand-led hero using Octowright banner: covered by Task 3 and Task 7
- Homepage with 3 featured demos: covered by Task 3
- How it Works with dashboard screenshot: covered by Task 4 and Task 7
- Canonical quickstart ending in dashboard + recorded demo: covered by Task 4 and Task 6
- `/demos` as technical-first hybrid gallery: covered by Task 5
- Curated docs/get-started pages: covered by Task 6
- Checked-in asset copy flow from Octowright tutorial exports: covered by Task 2 and Task 5

No uncovered spec requirements remain for the first implementation.

### Placeholder scan

No `TODO`, `TBD`, or “implement later” placeholders are left in the task steps.

### Type consistency

- Export sync uses `index.json`, `heroes/*.json`, and `artifacts/*` consistently
- Homepage hero and demos reference the same expected synced asset structure under `/demo-assets/<slug>/`
- Curated pages and nav model match the approved page map

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-06-site-octowright-com.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
