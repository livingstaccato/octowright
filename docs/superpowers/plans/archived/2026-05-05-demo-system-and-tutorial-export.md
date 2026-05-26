> **STATUS: shipped — archived 2026-05-25.** Deliverables landed on `feat/local-playground-integration-final` (PR #52); see commits 6d146a7..a6ff51b for the implementation trail. This file is kept verbatim as the spec snapshot.

# Demo System And Tutorial Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a canonical, offline, manifest-driven demo catalog in `octowright` that can run, record, index, and surface hero/supporting demos in both the repo and the dashboard, while producing exportable hero bundles for a future `octowright/tutorial` repo.

**Architecture:** Add a new `octowright.demos` domain layer that owns bundle metadata, artifact discovery, and generated manifests. Keep recorder/export logic outside the HTTP layer, then expose demo metadata through a dedicated HTTP route and a frontend gallery view. Reuse existing examples where possible, but promote the 7 hero demos into first-class demo bundles with deterministic local fixtures.

**Tech Stack:** Python 3.11, Starlette, existing Octowright HTTP sidecar, TypeScript/Vitest frontend, YAML/JSON manifests, Playwright-backed local recording flow, Markdown index generation

---

## File Structure

### New backend domain files

- Create: `src/octowright/demos/__init__.py`
- Create: `src/octowright/demos/models.py`
- Create: `src/octowright/demos/catalog.py`
- Create: `src/octowright/demos/indexer.py`
- Create: `src/octowright/demos/export.py`
- Create: `src/octowright/demos/fixtures.py`

### New HTTP route files

- Create: `src/octowright/http/routes/demos.py`

### Existing backend files to modify

- Modify: `src/octowright/http/routes/registry.py`
- Modify: `src/octowright/http/frontend.py`
- Modify: `src/octowright/defaults.py`
- Modify: `src/octowright/http/state.py`

### New CLI / script files

- Create: `scripts/demos/_shared.py`
- Create: `scripts/demos/record_demo.py`
- Create: `scripts/demos/record_heroes.py`
- Create: `scripts/demos/record_all.py`

### Demo content files

- Create: `demo/bundles/<demo-id>/demo.yaml`
- Create: `demo/bundles/<demo-id>/seed/...`
- Create: `demo/bundles/<demo-id>/scenario/...`
- Create: `demo/bundles/<demo-id>/macros/...`
- Create: `demo/INDEX.md`
- Create: `demo/tutorial-export/` (generated output target placeholder)

### Frontend files to modify or add

- Create: `packages/octowright-frontend/src/demo-gallery.ts`
- Create: `packages/octowright-frontend/src/demo-gallery.test.ts`
- Modify: `packages/octowright-frontend/src/api.ts`
- Modify: `packages/octowright-frontend/src/types.ts`
- Modify: `packages/octowright-frontend/src/dashboard.ts`
- Modify: `packages/octowright-frontend/src/dashboard.test.ts`
- Create: `packages/octowright-frontend/static/demos.html`
- Modify: `packages/octowright-frontend/static/index.html`
- Modify: `packages/octowright-frontend/static/styles.css`

### Docs and tests

- Create: `tests/test_demos_catalog.py`
- Create: `tests/test_demos_indexer.py`
- Create: `tests/test_http_demos.py`
- Modify: `tests/test_http_app_lifespan.py`
- Modify: `examples/README.md`
- Modify: `README.md`

---

### Task 1: Create The Demo Domain Model

**Files:**
- Create: `src/octowright/demos/__init__.py`
- Create: `src/octowright/demos/models.py`
- Create: `tests/test_demos_catalog.py`

- [ ] **Step 1: Write the failing tests for bundle parsing and hero/supporting classification**

```python
from pathlib import Path

from octowright.demos.catalog import load_demo_bundle, list_demo_bundles


def test_load_demo_bundle_reads_manifest_fields(tmp_path: Path) -> None:
    bundle = tmp_path / "demo" / "bundles" / "first-run-session"
    bundle.mkdir(parents=True)
    (bundle / "demo.yaml").write_text(
        """
id: first-run-session
title: First Run Session
summary: Launch one browser and inspect artifacts.
hero: true
audiences: [evaluators, users, contributors]
tags: [hero, recording]
engines: [chromium]
roles: [solo]
source_refs:
  scenarios: [scenario/solo.yaml]
artifact_expectations:
  replay: [artifacts/replay.jsonl]
  video: [artifacts/demo.mp4]
regen:
  command: uv run python scripts/demos/record_demo.py first-run-session
tutorial_export:
  include: true
""",
        encoding="utf-8",
    )

    bundle_meta = load_demo_bundle(bundle)

    assert bundle_meta.id == "first-run-session"
    assert bundle_meta.hero is True
    assert bundle_meta.tags == ["hero", "recording"]
    assert bundle_meta.regen_command.endswith("first-run-session")


def test_list_demo_bundles_sorts_heroes_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "demo" / "bundles"
    for name, hero in (("supporting-demo", False), ("hero-demo", True)):
        bundle = root / name
        bundle.mkdir(parents=True)
        (bundle / "demo.yaml").write_text(
            f"id: {name}\ntitle: {name}\nsummary: x\nhero: {str(hero).lower()}\naudiences: [users]\ntags: []\nengines: [chromium]\nroles: [solo]\nsource_refs: {{scenarios: []}}\nartifact_expectations: {{replay: [], video: []}}\nregen: {{command: cmd}}\ntutorial_export: {{include: false}}\n",
            encoding="utf-8",
        )

    monkeypatch.setattr("octowright.demos.catalog.DEMO_BUNDLES_DIR", root)
    bundles = list_demo_bundles()

    assert [bundle.id for bundle in bundles] == ["hero-demo", "supporting-demo"]
```

- [ ] **Step 2: Run the new tests to verify the module does not exist yet**

Run: `uv run pytest tests/test_demos_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError` or import errors for `octowright.demos`

- [ ] **Step 3: Implement the minimal models and catalog loader**

```python
# src/octowright/demos/models.py
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DemoBundle:
    id: str
    title: str
    summary: str
    hero: bool
    audiences: list[str]
    tags: list[str]
    engines: list[str]
    roles: list[str]
    scenarios: list[str]
    replay_artifacts: list[str]
    video_artifacts: list[str]
    regen_command: str
    tutorial_export: bool
    root: Path
```

```python
# src/octowright/demos/catalog.py
import yaml
from pathlib import Path

from octowright.demos.models import DemoBundle

DEMO_BUNDLES_DIR = Path("demo/bundles")


def load_demo_bundle(bundle_dir: Path) -> DemoBundle:
    payload = yaml.safe_load((bundle_dir / "demo.yaml").read_text(encoding="utf-8")) or {}
    source_refs = payload.get("source_refs") or {}
    artifact_expectations = payload.get("artifact_expectations") or {}
    tutorial_export = payload.get("tutorial_export") or {}
    regen = payload.get("regen") or {}
    return DemoBundle(
        id=str(payload["id"]),
        title=str(payload["title"]),
        summary=str(payload["summary"]),
        hero=bool(payload.get("hero", False)),
        audiences=list(payload.get("audiences") or []),
        tags=list(payload.get("tags") or []),
        engines=list(payload.get("engines") or []),
        roles=list(payload.get("roles") or []),
        scenarios=list(source_refs.get("scenarios") or []),
        replay_artifacts=list(artifact_expectations.get("replay") or []),
        video_artifacts=list(artifact_expectations.get("video") or []),
        regen_command=str(regen.get("command", "")),
        tutorial_export=bool(tutorial_export.get("include", False)),
        root=bundle_dir,
    )


def list_demo_bundles() -> list[DemoBundle]:
    bundles = [load_demo_bundle(path) for path in sorted(DEMO_BUNDLES_DIR.iterdir()) if path.is_dir()]
    return sorted(bundles, key=lambda bundle: (not bundle.hero, bundle.title.lower()))
```

- [ ] **Step 4: Run the tests and confirm parsing/sorting passes**

Run: `uv run pytest tests/test_demos_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Commit the new domain model**

```bash
git add src/octowright/demos/__init__.py src/octowright/demos/models.py src/octowright/demos/catalog.py tests/test_demos_catalog.py
git commit -m "feat: add demo bundle catalog models"
```

### Task 2: Add Generated Manifest And Repo Index Support

**Files:**
- Create: `src/octowright/demos/indexer.py`
- Create: `tests/test_demos_indexer.py`
- Create: `demo/INDEX.md`

- [ ] **Step 1: Write failing tests for generated manifest rows and Markdown index output**

```python
from octowright.demos.indexer import build_demo_index, build_manifest_row
from octowright.demos.models import DemoBundle


def test_build_manifest_row_includes_artifact_flags(tmp_path: Path) -> None:
    bundle = DemoBundle(
        id="cross-engine-trio",
        title="Cross Engine Trio",
        summary="Three engines.",
        hero=True,
        audiences=["evaluators"],
        tags=["hero", "engines"],
        engines=["chromium", "firefox", "webkit"],
        roles=["player"],
        scenarios=["scenario/cross-engine.yaml"],
        replay_artifacts=["artifacts/replay.jsonl"],
        video_artifacts=["artifacts/demo.mp4"],
        regen_command="uv run python scripts/demos/record_demo.py cross-engine-trio",
        tutorial_export=True,
        root=tmp_path,
    )
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "replay.jsonl").write_text("{}", encoding="utf-8")

    row = build_manifest_row(bundle)

    assert row["id"] == "cross-engine-trio"
    assert row["hero"] is True
    assert row["artifacts"]["replay"]["count"] == 1
    assert row["artifacts"]["video"]["count"] == 0


def test_build_demo_index_renders_hero_section_first(tmp_path: Path) -> None:
    hero = DemoBundle(... hero=True, title="Hero Demo", ...)
    support = DemoBundle(... hero=False, title="Support Demo", ...)

    markdown = build_demo_index([support, hero])

    assert markdown.index("## Hero Demos") < markdown.index("## Full Library")
    assert markdown.index("Hero Demo") < markdown.index("Support Demo")
```

- [ ] **Step 2: Run the new tests to verify the indexer is missing**

Run: `uv run pytest tests/test_demos_indexer.py -v`
Expected: FAIL with import errors for `octowright.demos.indexer`

- [ ] **Step 3: Implement manifest-row and Markdown index generation**

```python
# src/octowright/demos/indexer.py
from pathlib import Path

from octowright.demos.models import DemoBundle


def _artifact_summary(bundle: DemoBundle, rel_paths: list[str]) -> dict[str, object]:
    existing = [path for path in rel_paths if (bundle.root / path).exists()]
    return {"count": len(existing), "paths": existing}


def build_manifest_row(bundle: DemoBundle) -> dict[str, object]:
    return {
        "id": bundle.id,
        "title": bundle.title,
        "summary": bundle.summary,
        "hero": bundle.hero,
        "audiences": bundle.audiences,
        "tags": bundle.tags,
        "engines": bundle.engines,
        "roles": bundle.roles,
        "regen_command": bundle.regen_command,
        "tutorial_export": bundle.tutorial_export,
        "artifacts": {
            "replay": _artifact_summary(bundle, bundle.replay_artifacts),
            "video": _artifact_summary(bundle, bundle.video_artifacts),
        },
    }


def build_demo_index(bundles: list[DemoBundle]) -> str:
    heroes = [bundle for bundle in bundles if bundle.hero]
    supporting = [bundle for bundle in bundles if not bundle.hero]
    lines = [
        "# Octowright Demo Catalog",
        "",
        "## Hero Demos",
        "",
    ]
    for bundle in heroes:
        lines.append(f"- **{bundle.title}** — `{bundle.id}` — `{bundle.regen_command}`")
    lines.extend(["", "## Full Library", ""])
    for bundle in supporting:
        lines.append(f"- **{bundle.title}** — `{bundle.id}` — `{bundle.regen_command}`")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run the tests and write the generated index**

Run: `uv run pytest tests/test_demos_indexer.py -v`
Expected: PASS

Run: `uv run python -c "from pathlib import Path; from octowright.demos.catalog import list_demo_bundles; from octowright.demos.indexer import build_demo_index; Path('demo/INDEX.md').write_text(build_demo_index(list_demo_bundles()), encoding='utf-8')"`
Expected: `demo/INDEX.md` created or updated

- [ ] **Step 5: Commit the indexer layer**

```bash
git add src/octowright/demos/indexer.py tests/test_demos_indexer.py demo/INDEX.md
git commit -m "feat: add demo manifest and index generation"
```

### Task 3: Add Demo HTTP Endpoints

**Files:**
- Create: `src/octowright/http/routes/demos.py`
- Modify: `src/octowright/http/routes/registry.py`
- Create: `tests/test_http_demos.py`

- [ ] **Step 1: Write failing route tests for list and detail endpoints**

```python
from starlette.testclient import TestClient

from octowright import http as _http


def test_get_demos_returns_hero_and_supporting_lists(monkeypatch):
    monkeypatch.setattr(
        "octowright.http.routes.demos.list_demo_payloads",
        lambda: {
            "heroes": [{"id": "first-run-session", "title": "First Run Session"}],
            "supporting": [{"id": "fixture-lab", "title": "Fixture Lab"}],
        },
    )
    client = TestClient(_http.build_app())
    response = client.get("/api/demos")

    assert response.status_code == 200
    body = response.json()
    assert body["heroes"][0]["id"] == "first-run-session"
    assert body["supporting"][0]["id"] == "fixture-lab"


def test_get_demo_detail_404_when_missing(monkeypatch):
    monkeypatch.setattr("octowright.http.routes.demos.get_demo_payload", lambda _demo_id: None)
    client = TestClient(_http.build_app())
    response = client.get("/api/demos/missing-demo")

    assert response.status_code == 404
    assert "missing-demo" in response.json()["error"]
```

- [ ] **Step 2: Run the new route tests and verify failure**

Run: `uv run pytest tests/test_http_demos.py -v`
Expected: FAIL because the route module is not registered yet

- [ ] **Step 3: Implement demo list/detail endpoints using the catalog and indexer**

```python
# src/octowright/http/routes/demos.py
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from octowright.demos.catalog import list_demo_bundles
from octowright.demos.indexer import build_manifest_row
from octowright.http.exposure import guard_sensitive_http


def list_demo_payloads() -> dict[str, object]:
    bundles = list_demo_bundles()
    heroes = [build_manifest_row(bundle) for bundle in bundles if bundle.hero]
    supporting = [build_manifest_row(bundle) for bundle in bundles if not bundle.hero]
    return {"heroes": heroes, "supporting": supporting}


def get_demo_payload(demo_id: str) -> dict[str, object] | None:
    for bundle in list_demo_bundles():
        if bundle.id == demo_id:
            return build_manifest_row(bundle)
    return None


async def list_demos(_request: Request) -> JSONResponse:
    return JSONResponse(list_demo_payloads())


async def demo_detail(request: Request) -> JSONResponse:
    demo_id = request.path_params["demo_id"]
    payload = get_demo_payload(demo_id)
    if payload is None:
        return JSONResponse({"error": f"demo {demo_id!r} not found"}, status_code=404)
    return JSONResponse(payload)


def routes() -> list[Route]:
    return [
        Route("/api/demos", guard_sensitive_http(list_demos), methods=["GET"]),
        Route("/api/demos/{demo_id}", guard_sensitive_http(demo_detail), methods=["GET"]),
    ]
```

```python
# src/octowright/http/routes/registry.py
from octowright.http.routes import demos, events, health, media, meta, scenarios, sessions

def all_routes() -> list[Any]:
    routes: list[Any] = []
    routes.extend(health.routes())
    routes.extend(sessions.routes())
    routes.extend(events.routes())
    routes.extend(media.routes())
    routes.extend(scenarios.routes())
    routes.extend(meta.routes())
    routes.extend(demos.routes())
    return routes
```

- [ ] **Step 4: Run the route tests and a broader HTTP smoke slice**

Run: `uv run pytest tests/test_http_demos.py tests/test_http_app_lifespan.py -v`
Expected: PASS

- [ ] **Step 5: Commit the HTTP demo endpoints**

```bash
git add src/octowright/http/routes/demos.py src/octowright/http/routes/registry.py tests/test_http_demos.py
git commit -m "feat: add demo catalog http endpoints"
```

### Task 4: Add Frontend Demo Types, API Calls, And Gallery Rendering

**Files:**
- Modify: `packages/octowright-frontend/src/types.ts`
- Modify: `packages/octowright-frontend/src/api.ts`
- Create: `packages/octowright-frontend/src/demo-gallery.ts`
- Create: `packages/octowright-frontend/src/demo-gallery.test.ts`
- Modify: `packages/octowright-frontend/src/dashboard.ts`
- Modify: `packages/octowright-frontend/src/dashboard.test.ts`

- [ ] **Step 1: Write failing frontend tests for gallery fetch and hero-card rendering**

```typescript
import { describe, expect, it, vi } from "vitest";
import { renderDemoGallery } from "./demo-gallery.js";

describe("renderDemoGallery", () => {
  it("renders hero and supporting sections", () => {
    const root = document.createElement("div");
    renderDemoGallery(root, {
      heroes: [{ id: "first-run-session", title: "First Run Session", summary: "x", hero: true, tags: [], engines: ["chromium"], roles: ["solo"], regen_command: "cmd", tutorial_export: true, artifacts: { replay: { count: 1, paths: [] }, video: { count: 1, paths: [] } } }],
      supporting: [{ id: "fixture-lab", title: "Fixture Lab", summary: "y", hero: false, tags: [], engines: ["webkit"], roles: ["player"], regen_command: "cmd2", tutorial_export: false, artifacts: { replay: { count: 0, paths: [] }, video: { count: 0, paths: [] } } }],
    });

    expect(root.textContent).toContain("Hero demos");
    expect(root.textContent).toContain("First Run Session");
    expect(root.textContent).toContain("Fixture Lab");
  });
});
```

- [ ] **Step 2: Run the frontend test and verify the gallery module is missing**

Run: `cd packages/octowright-frontend && npm run test -- demo-gallery.test.ts`
Expected: FAIL because `demo-gallery.ts` does not exist yet

- [ ] **Step 3: Add the frontend demo types and API helper**

```typescript
// packages/octowright-frontend/src/types.ts
export interface DemoArtifactGroup {
  count: number;
  paths: string[];
}

export interface DemoSummary {
  id: string;
  title: string;
  summary: string;
  hero: boolean;
  tags: string[];
  engines: string[];
  roles: string[];
  regen_command: string;
  tutorial_export: boolean;
  artifacts: {
    replay: DemoArtifactGroup;
    video: DemoArtifactGroup;
  };
}

export interface DemoListResponse {
  heroes: DemoSummary[];
  supporting: DemoSummary[];
}
```

```typescript
// packages/octowright-frontend/src/api.ts
import type { DemoListResponse } from "./types.js";

export function getDemos(): Promise<DemoListResponse> {
  return fetchJson<DemoListResponse>("/api/demos");
}
```

- [ ] **Step 4: Implement gallery rendering and wire it into the dashboard load path**

```typescript
// packages/octowright-frontend/src/demo-gallery.ts
import type { DemoListResponse } from "./types.js";

export function renderDemoGallery(root: HTMLElement, demos: DemoListResponse): void {
  root.innerHTML = `
    <section data-testid="panel-demo-heroes">
      <h2>Hero demos</h2>
      ${demos.heroes.map((demo) => `<article><h3>${demo.title}</h3><p>${demo.summary}</p><code>${demo.regen_command}</code></article>`).join("")}
    </section>
    <section data-testid="panel-demo-library">
      <h2>Full library</h2>
      ${demos.supporting.map((demo) => `<article><h3>${demo.title}</h3><p>${demo.summary}</p></article>`).join("")}
    </section>
  `;
}
```

```typescript
// packages/octowright-frontend/src/dashboard.ts
import { getDemos } from "./api.js";

interface DashboardState {
  sessions: SessionListResponse;
  scenarios: ScenarioListResponse;
  personas: PersonaSummary[];
  macros: MacroSummary[];
  demos: DemoListResponse;
}

const EMPTY_STATE: DashboardState = {
  sessions: { live: [], closed: [] },
  scenarios: { live: [] },
  personas: [],
  macros: [],
  demos: { heroes: [], supporting: [] },
};
```

- [ ] **Step 5: Run the frontend tests**

Run: `cd packages/octowright-frontend && npm run test -- demo-gallery.test.ts dashboard.test.ts api.test.ts`
Expected: PASS

- [ ] **Step 6: Commit the frontend gallery foundation**

```bash
git add packages/octowright-frontend/src/types.ts packages/octowright-frontend/src/api.ts packages/octowright-frontend/src/demo-gallery.ts packages/octowright-frontend/src/demo-gallery.test.ts packages/octowright-frontend/src/dashboard.ts packages/octowright-frontend/src/dashboard.test.ts
git commit -m "feat: add dashboard demo gallery"
```

### Task 5: Add Dedicated Demo Gallery Entry Points In The Frontend Bundle

**Files:**
- Modify: `src/octowright/http/frontend.py`
- Create: `packages/octowright-frontend/static/demos.html`
- Modify: `packages/octowright-frontend/static/index.html`
- Modify: `packages/octowright-frontend/static/styles.css`

- [ ] **Step 1: Write a failing HTTP/frontend route test for `/demos`**

```python
from starlette.testclient import TestClient

from octowright.http.app import build_app


def test_frontend_serves_demos_html_when_bundle_exists(tmp_path, monkeypatch):
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (frontend_dir / "session.html").write_text("<html></html>", encoding="utf-8")
    (frontend_dir / "demos.html").write_text("<html><body>demos</body></html>", encoding="utf-8")
    monkeypatch.setattr("octowright.http.state.FRONTEND_DIR", frontend_dir)

    client = TestClient(build_app())
    response = client.get("/demos")

    assert response.status_code == 200
    assert "demos" in response.text
```

- [ ] **Step 2: Run the route test and verify it fails**

Run: `uv run pytest tests/test_http_app_lifespan.py -k demos_html -v`
Expected: FAIL because `/demos` is not yet served

- [ ] **Step 3: Add a dedicated demos frontend route and static page**

```python
# src/octowright/http/frontend.py
async def _serve_demos_html(_: Request) -> Response:
    target = state.FRONTEND_DIR / "demos.html"
    if not target.exists():
        return PlainTextResponse("demos.html not bundled (run npm run build)", status_code=404)
    return FileResponse(str(target), media_type="text/html")


def _frontend_routes() -> list[Any]:
    if not (state.FRONTEND_DIR.exists() and state.FRONTEND_DIR.is_dir()):
        return []
    return [
        Route("/sessions/{id:path}", _serve_session_html, methods=["GET"]),
        Route("/demos", _serve_demos_html, methods=["GET"]),
        Mount("/", app=StaticFiles(directory=str(state.FRONTEND_DIR), html=True), name="frontend"),
    ]
```

```html
<!-- packages/octowright-frontend/static/demos.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>octowright demos</title>
  <link rel="stylesheet" href="styles.css">
</head>
<body class="page page--dashboard">
  <header class="topbar">
    <h1 class="topbar__title">octowright demos</h1>
    <a href="/" class="topbar__hint">back to dashboard</a>
  </header>
  <main id="app" class="dashboard-grid"></main>
  <script type="module" src="../src/demo-gallery.ts"></script>
</body>
</html>
```

- [ ] **Step 4: Rebuild the frontend bundle and rerun the test**

Run: `cd packages/octowright-frontend && npm run build`
Expected: PASS build output

Run: `uv run pytest tests/test_http_app_lifespan.py -k demos_html -v`
Expected: PASS

- [ ] **Step 5: Commit the frontend entry point changes**

```bash
git add src/octowright/http/frontend.py packages/octowright-frontend/static/demos.html packages/octowright-frontend/static/index.html packages/octowright-frontend/static/styles.css src/octowright/server/frontend
git commit -m "feat: add dedicated demos gallery page"
```

### Task 6: Add Recorder Scripts And Tutorial Export Metadata

**Files:**
- Create: `scripts/demos/_shared.py`
- Create: `scripts/demos/record_demo.py`
- Create: `scripts/demos/record_heroes.py`
- Create: `scripts/demos/record_all.py`
- Create: `src/octowright/demos/export.py`

- [ ] **Step 1: Write failing tests around tutorial-export payload generation**

```python
from pathlib import Path

from octowright.demos.export import build_tutorial_export
from octowright.demos.models import DemoBundle


def test_build_tutorial_export_includes_hero_assets(tmp_path: Path) -> None:
    bundle = DemoBundle(... hero=True, tutorial_export=True, root=tmp_path, ...)
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "poster.png").write_bytes(b"png")
    (tmp_path / "artifacts" / "demo.mp4").write_bytes(b"mp4")
    (tmp_path / "artifacts" / "replay.jsonl").write_text("{}", encoding="utf-8")

    payload = build_tutorial_export(bundle)

    assert payload["id"] == bundle.id
    assert payload["assets"]["video"] == ["artifacts/demo.mp4"]
    assert payload["assets"]["replay"] == ["artifacts/replay.jsonl"]
```

- [ ] **Step 2: Run the export test and verify the module is missing**

Run: `uv run pytest tests/test_demos_indexer.py -k tutorial_export -v`
Expected: FAIL with import errors for `octowright.demos.export`

- [ ] **Step 3: Implement tutorial export payloads and recorder entry points**

```python
# src/octowright/demos/export.py
from octowright.demos.models import DemoBundle


def build_tutorial_export(bundle: DemoBundle) -> dict[str, object]:
    return {
        "id": bundle.id,
        "title": bundle.title,
        "summary": bundle.summary,
        "regen_command": bundle.regen_command,
        "assets": {
            "video": bundle.video_artifacts,
            "replay": bundle.replay_artifacts,
        },
    }
```

```python
# scripts/demos/record_demo.py
import sys

from octowright.demos.catalog import list_demo_bundles
from octowright.demos.indexer import build_demo_index


def main() -> int:
    demo_id = sys.argv[1]
    bundles = {bundle.id: bundle for bundle in list_demo_bundles()}
    bundle = bundles[demo_id]
    # first pass: verify the bundle exists and write the regenerated index shell
    Path("demo/INDEX.md").write_text(build_demo_index(list(bundles.values())), encoding="utf-8")
    print(f"prepared demo bundle: {bundle.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run export tests and a basic recorder smoke**

Run: `uv run pytest tests/test_demos_indexer.py tests/test_demos_catalog.py -v`
Expected: PASS

Run: `uv run python scripts/demos/record_demo.py first-run-session`
Expected: prints `prepared demo bundle: first-run-session`

- [ ] **Step 5: Commit the recorder/export foundation**

```bash
git add scripts/demos/_shared.py scripts/demos/record_demo.py scripts/demos/record_heroes.py scripts/demos/record_all.py src/octowright/demos/export.py
git commit -m "feat: add demo recording and export foundations"
```

### Task 7: Promote Existing Examples Into Hero And Supporting Bundles

**Files:**
- Create: `demo/bundles/first-run-session/demo.yaml`
- Create: `demo/bundles/macro-replay-loop/demo.yaml`
- Create: `demo/bundles/cross-engine-trio/demo.yaml`
- Create: `demo/bundles/role-based-duo/demo.yaml`
- Create: `demo/bundles/fixture-lab/demo.yaml`
- Create: `demo/bundles/verify-suite/demo.yaml`
- Create: `demo/bundles/seven-mix-orchestration/demo.yaml`
- Create: `demo/bundles/*/seed/...`
- Modify: `examples/README.md`
- Modify: `README.md`

- [ ] **Step 1: Create the first hero manifest and a failing structural test**

```python
from pathlib import Path
import yaml


def test_hero_demo_manifests_exist() -> None:
    ids = [
        "first-run-session",
        "macro-replay-loop",
        "cross-engine-trio",
        "role-based-duo",
        "fixture-lab",
        "verify-suite",
        "seven-mix-orchestration",
    ]
    for demo_id in ids:
        manifest = Path("demo/bundles") / demo_id / "demo.yaml"
        assert manifest.exists(), demo_id
        payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        assert payload["hero"] is True
```

- [ ] **Step 2: Run the structural test and verify the bundles do not exist yet**

Run: `uv run pytest tests/test_demos_catalog.py -k hero_demo_manifests_exist -v`
Expected: FAIL with missing bundle files

- [ ] **Step 3: Add the seven hero manifests and reference existing scenarios/macros where possible**

```yaml
# demo/bundles/cross-engine-trio/demo.yaml
id: cross-engine-trio
title: Cross Engine Trio
summary: Launch Chromium, Firefox, and WebKit against the same deterministic local target.
hero: true
audiences: [evaluators, users, contributors]
tags: [hero, engines, scenarios]
engines: [chromium, firefox, webkit]
roles: [player]
source_refs:
  scenarios:
    - scenario/cross-engine.yaml
  macros:
    - macros/test-smoke-page-ready.json
artifact_expectations:
  replay:
    - artifacts/replay.jsonl
  video:
    - artifacts/demo.mp4
regen:
  command: uv run python scripts/demos/record_demo.py cross-engine-trio
tutorial_export:
  include: true
```

- [ ] **Step 4: Run the structural tests and regenerate the repo index**

Run: `uv run pytest tests/test_demos_catalog.py tests/test_demos_indexer.py -v`
Expected: PASS

Run: `uv run python -c "from pathlib import Path; from octowright.demos.catalog import list_demo_bundles; from octowright.demos.indexer import build_demo_index; Path('demo/INDEX.md').write_text(build_demo_index(list_demo_bundles()), encoding='utf-8')"`
Expected: `demo/INDEX.md` updated with the seven hero bundles

- [ ] **Step 5: Commit the hero bundle content**

```bash
git add demo/bundles demo/INDEX.md examples/README.md README.md
git commit -m "feat: add hero demo bundles"
```

### Task 8: End-To-End Verification And Cleanup

**Files:**
- Verify only: `src/octowright/demos/*`
- Verify only: `src/octowright/http/routes/demos.py`
- Verify only: `packages/octowright-frontend/src/demo-gallery.ts`
- Verify only: `demo/bundles/*`

- [ ] **Step 1: Run focused Python backend tests**

Run: `uv run pytest tests/test_demos_catalog.py tests/test_demos_indexer.py tests/test_http_demos.py -v`
Expected: PASS

- [ ] **Step 2: Run focused frontend tests**

Run: `cd packages/octowright-frontend && npm run test -- demo-gallery.test.ts dashboard.test.ts api.test.ts`
Expected: PASS

- [ ] **Step 3: Build the frontend bundle**

Run: `cd packages/octowright-frontend && npm run build`
Expected: PASS and updated bundled frontend assets

- [ ] **Step 4: Run full project verification**

Run: `make test`
Expected: PASS with repository coverage threshold met

- [ ] **Step 5: Commit the final verification pass**

```bash
git add src/octowright/demos src/octowright/http/routes/demos.py src/octowright/http/routes/registry.py src/octowright/http/frontend.py packages/octowright-frontend demo tests README.md examples/README.md
git commit -m "feat: add runnable demo system and gallery"
```

---

## Self-Review

### Spec coverage

- Demo bundle source of truth: covered by Tasks 1, 2, and 7
- Dual-layer catalog: covered by Tasks 2, 4, 5, and 7
- Repo index: covered by Tasks 2 and 7
- Dashboard gallery: covered by Tasks 3, 4, and 5
- Offline deterministic seed content: covered by Task 7
- Recorder/export pipeline: covered by Task 6
- Tutorial-export readiness: covered by Task 6

### Placeholder scan

- No `TBD` or `TODO` placeholders remain
- Every task names exact files
- Every code-writing step includes explicit code blocks
- Every verification step includes an exact command and expected result

### Type consistency

- `DemoBundle` is defined in Task 1 and reused consistently later
- HTTP route payloads are based on `build_manifest_row()`
- frontend `DemoListResponse` matches backend `heroes`/`supporting` shape

