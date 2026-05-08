# Demo Media Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign Octowright's demo media pipeline so each demo can use an approved presentation mode with subtle overlays, better timing, and optional synced multi-video outputs without losing regenerability.

**Architecture:** Add declarative presentation metadata to demo bundles, interpret it through a small presentation/rendering layer, and extend the recorder to emit primary and supporting video assets from the same run. Retune the existing bundles onto those approved patterns and propagate the richer artifact metadata through tutorial/site exports.

**Tech Stack:** Python 3.13, dataclasses, YAML bundle manifests, ffmpeg helpers in `src/octowright/video.py`, pytest, Octowright demo recorder scripts

---

## File Structure

### Existing files to modify

- `src/octowright/demos/models.py`
  - Extend `DemoBundle` and recording metadata with declarative presentation config.
- `src/octowright/demos/catalog.py`
  - Parse the new `presentation` block from `demo.yaml`.
- `src/octowright/demos/rendering.py`
  - Replace hard-coded bundle-id composition logic with presentation-driven rendering.
- `src/octowright/demos/runtime.py`
  - Apply presentation timing rules and pass richer artifact inputs into rendering/export.
- `src/octowright/demos/export.py`
  - Propagate primary/supporting media relationships into tutorial export payloads.
- `src/octowright/video.py`
  - Reuse existing ffmpeg helpers and add only the minimal support needed for multi-output rendering.
- `src/octowright/video_overlay.py`
  - Restyle overlays to a subtler safe-area treatment.
- `scripts/demos/record_demo.py`
  - Surface presentation-driven asset generation clearly in CLI output if needed.
- `scripts/demos/record_heroes.py`
  - Keep orchestration behavior aligned with the new artifact set.
- `scripts/demos/record_all.py`
  - Keep orchestration behavior aligned with the new artifact set.
- `scripts/demos/regenerate_website_heroes.py`
  - Continue exporting the right primary hero assets after the pipeline changes.
- `demo/bundles/*/demo.yaml`
  - Add per-demo presentation metadata.
- `demo/bundles/*/seed/*`
  - Retune seed surfaces only where needed to support readability.
- `tests/test_demos_catalog.py`
  - Cover presentation parsing and validation.
- `tests/test_demos_runtime.py`
  - Cover timing and multi-output runtime behavior.
- `tests/test_demo_record_scripts.py`
  - Cover regenerated artifact expectations.
- `tests/test_demos_indexer.py`
  - Cover updated manifest/export fields if index payloads change.
- `tests/test_video.py`
  - Cover overlay styling and any new composition helper behavior.

### New files to create

- `src/octowright/demos/presentation.py`
  - Typed presentation config helpers and approved mode validation.
- `src/octowright/demos/presentation_profiles.py`
  - Approved layout presets and per-demo layout selection helpers.
- `tests/test_demos_presentation.py`
  - Focused unit tests for presentation config and mode decisions.

These new modules keep `rendering.py` and `models.py` focused and help maintain the no-files-over-500-LOC constraint.

### Proposed task order

1. Add declarative presentation schema and parser coverage.
2. Move rendering decisions onto presentation metadata and subtle overlay defaults.
3. Add timing controls and multi-output generation to runtime/rendering.
4. Propagate richer media metadata through export/index/script surfaces.
5. Retune current bundles onto approved presentation patterns and regenerate artifacts.
6. Run focused verification, then full verification.

### Task 1: Presentation Schema And Parsing

**Files:**
- Create: `src/octowright/demos/presentation.py`
- Modify: `src/octowright/demos/models.py`
- Modify: `src/octowright/demos/catalog.py`
- Create: `tests/test_demos_presentation.py`
- Modify: `tests/test_demos_catalog.py`

- [ ] **Step 1: Write the failing presentation parsing tests**

```python
from pathlib import Path

import pytest

from octowright.demos.catalog import load_demo_bundle


def test_load_demo_bundle_parses_presentation_block(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "alpha"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text(
        """
id: alpha
title: Alpha
presentation:
  mode: sync-multi
  primary_asset: hero_video
  overlay:
    style: subtle
    placement: bottom-right
    enabled: true
  timing:
    intro_ms: 500
    outro_ms: 1800
    minimum_ms: 6000
  sync_groups:
    - id: engines
      roles: [player, monitor]
""".strip(),
        encoding="utf-8",
    )

    bundle = load_demo_bundle(bundle_dir)

    assert bundle.presentation.mode == "sync-multi"
    assert bundle.presentation.overlay.style == "subtle"
    assert bundle.presentation.timing.minimum_ms == 6000
    assert bundle.presentation.sync_groups[0].id == "engines"


def test_load_demo_bundle_rejects_unknown_presentation_mode(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "demo" / "bundles" / "broken"
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "demo.yaml").write_text("presentation:\n  mode: freestyle\n", encoding="utf-8")

    with pytest.raises(ValueError, match="presentation.mode"):
        load_demo_bundle(bundle_dir)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_demos_presentation.py tests/test_demos_catalog.py -q --no-cov`
Expected: FAIL with missing `presentation` fields/types on `DemoBundle` and missing parser support in `catalog.py`.

- [ ] **Step 3: Add minimal presentation dataclasses and parser support**

```python
@dataclass
class DemoOverlayConfig:
    enabled: bool = True
    style: str = "subtle"
    placement: str = "bottom-left"


@dataclass
class DemoTimingConfig:
    intro_ms: int = 0
    outro_ms: int = 1500
    minimum_ms: int = 4000


@dataclass
class DemoPresentationConfig:
    mode: str = "single-clean"
    primary_asset: str = "hero_video"
    overlay: DemoOverlayConfig = field(default_factory=DemoOverlayConfig)
    timing: DemoTimingConfig = field(default_factory=DemoTimingConfig)
    sync_groups: list[DemoSyncGroup] = field(default_factory=list)
```

```python
def _parse_presentation(raw: Any) -> DemoPresentationConfig:
    presentation = _as_dict("presentation", raw)
    mode = _optional_string("presentation.mode", presentation.get("mode")) or "single-clean"
    validate_presentation_mode(mode)
    return DemoPresentationConfig(
        mode=mode,
        primary_asset=_optional_string("presentation.primary_asset", presentation.get("primary_asset"))
        or "hero_video",
        overlay=_parse_overlay(presentation.get("overlay")),
        timing=_parse_timing(presentation.get("timing")),
        sync_groups=_parse_sync_groups(presentation.get("sync_groups")),
    )
```

- [ ] **Step 4: Run tests to verify parsing passes**

Run: `uv run pytest tests/test_demos_presentation.py tests/test_demos_catalog.py -q --no-cov`
Expected: PASS for new presentation parsing cases.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/demos/models.py src/octowright/demos/catalog.py src/octowright/demos/presentation.py tests/test_demos_presentation.py tests/test_demos_catalog.py
git commit -m "feat: add demo presentation metadata"
```

### Task 2: Presentation-Driven Rendering And Subtle Overlays

**Files:**
- Create: `src/octowright/demos/presentation_profiles.py`
- Modify: `src/octowright/demos/rendering.py`
- Modify: `src/octowright/video_overlay.py`
- Modify: `tests/test_video.py`
- Modify: `tests/test_demos_runtime.py`

- [ ] **Step 1: Write the failing rendering and overlay tests**

```python
def test_render_bundle_video_uses_presentation_mode_for_sync_multi(monkeypatch, tmp_path: Path) -> None:
    bundle = DemoBundle(id="alpha", title="Alpha", root=tmp_path)
    bundle.presentation = DemoPresentationConfig(mode="sync-multi")

    called: dict[str, object] = {}
    monkeypatch.setattr(
        "octowright.demos.rendering.render_sync_group_videos",
        lambda *args, **kwargs: called.setdefault("sync", True) or [],
    )

    summary = render_bundle_video(bundle, live, close_results, video_path=video_path, poster_path=poster_path)

    assert summary["mode"] == "sync-multi"
    assert called["sync"] is True


def test_render_overlay_image_uses_translucent_safe_area_defaults(tmp_path: Path) -> None:
    path = render_overlay_image(
        tmp_path / "overlay.ppm",
        title="Alpha",
        subtitle="Quiet metadata",
        panes=[],
        canvas_width=1920,
        canvas_height=1080,
    )
    assert path.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_video.py tests/test_demos_runtime.py -q --no-cov`
Expected: FAIL because rendering still keys off hard-coded bundle IDs and overlays still assume the older title-bar behavior.

- [ ] **Step 3: Move composition selection into presentation profiles and restyle overlays**

```python
def select_render_plan(bundle: DemoBundle) -> RenderPlan:
    if bundle.presentation.mode == "single-clean":
        return RenderPlan(kind="single")
    if bundle.presentation.mode == "hero-composite":
        return resolve_composite_plan(bundle)
    if bundle.presentation.mode == "sync-multi":
        return RenderPlan(kind="sync-multi")
    return RenderPlan(kind="artifact-first")
```

```python
overlay_box = OverlayBox(
    anchor="bottom-left",
    background_rgba=(12, 16, 24, 120),
    title_rgba=(245, 247, 250, 220),
    subtitle_rgba=(203, 213, 225, 190),
    padding=24,
    margin=28,
)
```

- [ ] **Step 4: Run tests to verify the renderer now follows presentation metadata**

Run: `uv run pytest tests/test_video.py tests/test_demos_runtime.py -q --no-cov`
Expected: PASS for presentation-mode selection and subtle overlay behavior.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/demos/presentation_profiles.py src/octowright/demos/rendering.py src/octowright/video_overlay.py tests/test_video.py tests/test_demos_runtime.py
git commit -m "feat: drive demo rendering from presentation modes"
```

### Task 3: Timing Controls And Multi-Output Media Generation

**Files:**
- Modify: `src/octowright/demos/runtime.py`
- Modify: `src/octowright/demos/rendering.py`
- Modify: `src/octowright/video.py`
- Modify: `tests/test_demos_runtime.py`
- Modify: `tests/test_video.py`

- [ ] **Step 1: Write the failing timing and supporting-output tests**

```python
@pytest.mark.asyncio
async def test_record_demo_bundle_applies_intro_and_outro_holds(monkeypatch, tmp_path: Path) -> None:
    bundle = _write_bundle_layout(tmp_path)
    bundle.presentation.timing.intro_ms = 250
    bundle.presentation.timing.outro_ms = 1250

    sleeps: list[float] = []
    monkeypatch.setattr("octowright.demos.runtime.asyncio.sleep", lambda seconds: sleeps.append(seconds))

    await record_demo_bundle(bundle)

    assert sleeps == [0.25, 1.25]


@pytest.mark.asyncio
async def test_record_demo_bundle_writes_supporting_sync_assets(monkeypatch, tmp_path: Path) -> None:
    bundle = _write_duo_bundle_layout(tmp_path)
    bundle.presentation.mode = "sync-multi"

    result = await record_demo_bundle(bundle)

    assert "supporting_videos" in result
    assert result["supporting_videos"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_demos_runtime.py tests/test_video.py -q --no-cov`
Expected: FAIL because runtime has no timing hooks and the renderer only emits one primary `demo.mp4`.

- [ ] **Step 3: Add timing hooks and supporting asset generation**

```python
async def _apply_intro_hold(bundle: DemoBundle) -> None:
    if bundle.presentation.timing.intro_ms > 0:
        await asyncio.sleep(bundle.presentation.timing.intro_ms / 1000)


async def _apply_outro_hold(bundle: DemoBundle) -> None:
    if bundle.presentation.timing.outro_ms > 0:
        await asyncio.sleep(bundle.presentation.timing.outro_ms / 1000)
```

```python
render_result = render_bundle_video(...)
supporting_videos = render_result.get("supporting_videos", [])
result["supporting_videos"] = [item["path"] for item in supporting_videos]
```

```python
def render_sync_group_videos(... ) -> list[dict[str, Any]]:
    return [
        {"id": pane.id, "path": str(output_path), "poster_path": str(poster_path)}
        for pane in sync_group_panes
    ]
```

- [ ] **Step 4: Run tests to verify timing and supporting assets work**

Run: `uv run pytest tests/test_demos_runtime.py tests/test_video.py -q --no-cov`
Expected: PASS with intro/outro holds invoked and supporting assets recorded in the runtime result.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/demos/runtime.py src/octowright/demos/rendering.py src/octowright/video.py tests/test_demos_runtime.py tests/test_video.py
git commit -m "feat: add demo timing controls and sync outputs"
```

### Task 4: Manifest, Export, And Script Surface Updates

**Files:**
- Modify: `src/octowright/demos/rendering.py`
- Modify: `src/octowright/demos/export.py`
- Modify: `src/octowright/demos/indexer.py`
- Modify: `scripts/demos/record_demo.py`
- Modify: `tests/test_demos_indexer.py`
- Modify: `tests/test_demo_record_scripts.py`

- [ ] **Step 1: Write the failing metadata propagation tests**

```python
def test_write_artifact_manifest_records_primary_and_supporting_assets(tmp_path: Path) -> None:
    manifest = json.loads((tmp_path / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"]["video"]["path"] == "artifacts/demo.mp4"
    assert manifest["artifacts"]["supporting_videos"][0]["id"] == "monitor"


def test_record_demo_script_reports_supporting_outputs(tmp_path: Path) -> None:
    result = run_record_demo_cli(tmp_path, "role-based-duo")
    assert "supporting videos" in result.output.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_demos_indexer.py tests/test_demo_record_scripts.py -q --no-cov`
Expected: FAIL because manifest/export payloads only describe a single video/poster pair.

- [ ] **Step 3: Extend manifest and export payloads**

```python
payload["artifacts"]["supporting_videos"] = [
    {
        "id": item["id"],
        "path": item["path"],
        "poster_path": item["poster_path"],
        "role": item["role"],
        "kind": item["kind"],
    }
    for item in supporting_videos
]
payload["presentation"] = {
    "mode": bundle.presentation.mode,
    "primary_asset": bundle.presentation.primary_asset,
}
```

```python
export_payload["media"] = {
    "primary": manifest["artifacts"]["video"],
    "supporting": manifest["artifacts"].get("supporting_videos", []),
    "presentation": manifest.get("presentation", {}),
}
```

- [ ] **Step 4: Run tests to verify metadata propagation passes**

Run: `uv run pytest tests/test_demos_indexer.py tests/test_demo_record_scripts.py -q --no-cov`
Expected: PASS with manifest/export/script outputs aware of supporting media assets.

- [ ] **Step 5: Commit**

```bash
git add src/octowright/demos/rendering.py src/octowright/demos/export.py src/octowright/demos/indexer.py scripts/demos/record_demo.py tests/test_demos_indexer.py tests/test_demo_record_scripts.py
git commit -m "feat: export richer demo media metadata"
```

### Task 5: Retune Demo Bundles And Regenerate Artifacts

**Files:**
- Modify: `demo/bundles/first-run-session/demo.yaml`
- Modify: `demo/bundles/macro-replay-loop/demo.yaml`
- Modify: `demo/bundles/cross-engine-trio/demo.yaml`
- Modify: `demo/bundles/role-based-duo/demo.yaml`
- Modify: `demo/bundles/fixture-lab/demo.yaml`
- Modify: `demo/bundles/verify-suite/demo.yaml`
- Modify: `demo/bundles/seven-mix-orchestration/demo.yaml`
- Modify: `demo/bundles/macro-replay-loop/seed/login-card.html`
- Modify: `demo/bundles/seven-mix-orchestration/seed/orchestration-stage.html`
- Modify: `demo/bundles/*/artifacts/*`
- Modify: `demo/bundles/*/exports/*`
- Modify: `demo/tutorial-export/**/*`

- [ ] **Step 1: Write the failing artifact expectation tests**

```python
def test_cross_engine_trio_manifest_no_longer_uses_360_pixel_tall_master() -> None:
    manifest = json.loads(Path("demo/bundles/cross-engine-trio/artifacts/manifest.json").read_text(encoding="utf-8"))
    assert manifest["composition"]["canvas_height"] > 360


def test_role_based_duo_manifest_declares_supporting_media_or_taller_composite() -> None:
    manifest = json.loads(Path("demo/bundles/role-based-duo/artifacts/manifest.json").read_text(encoding="utf-8"))
    assert manifest["presentation"]["mode"] in {"hero-composite", "sync-multi"}
```

- [ ] **Step 2: Run tests to verify they fail against the current checked-in artifacts**

Run: `uv run pytest tests/test_demo_record_scripts.py tests/test_demos_indexer.py -q --no-cov`
Expected: FAIL because current artifacts and manifests still reflect the older dimensions and output model.

- [ ] **Step 3: Update bundle manifests and regenerate media**

```yaml
presentation:
  mode: hero-composite
  primary_asset: hero_video
  overlay:
    enabled: true
    style: subtle
    placement: bottom-left
  timing:
    intro_ms: 400
    outro_ms: 1800
    minimum_ms: 7000
  sync_groups:
    - id: roles
      roles:
        - player
        - monitor
```

Run:

```bash
OCTOWRIGHT_HEADLESS=1 uv run python scripts/demos/record_demo.py cross-engine-trio
OCTOWRIGHT_HEADLESS=1 uv run python scripts/demos/record_demo.py role-based-duo
OCTOWRIGHT_HEADLESS=1 uv run python scripts/demos/record_demo.py seven-mix-orchestration
OCTOWRIGHT_HEADLESS=1 uv run python scripts/demos/record_all.py
```

Expected: regenerated `artifacts/manifest.json`, `demo.mp4`, posters, and any supporting sync assets match the new presentation metadata.

- [ ] **Step 4: Run focused tests to verify regenerated artifacts**

Run: `uv run pytest tests/test_demo_record_scripts.py tests/test_demos_indexer.py tests/test_demos_runtime.py -q --no-cov`
Expected: PASS with updated manifests and artifact expectations.

- [ ] **Step 5: Commit**

```bash
git add demo/bundles demo/tutorial-export tests/test_demo_record_scripts.py tests/test_demos_indexer.py tests/test_demos_runtime.py
git commit -m "feat: retune demo bundle media treatments"
```

### Task 6: Final Verification And Cleanup

**Files:**
- Modify only if verification exposes defects in prior tasks.

- [ ] **Step 1: Run the focused backend/media verification suite**

Run: `uv run pytest tests/test_demos_presentation.py tests/test_demos_catalog.py tests/test_demos_runtime.py tests/test_demo_record_scripts.py tests/test_demos_indexer.py tests/test_video.py -q --no-cov`
Expected: PASS for all targeted demo media coverage.

- [ ] **Step 2: Regenerate the website hero export set**

Run: `OCTOWRIGHT_HEADLESS=1 uv run python scripts/demos/regenerate_website_heroes.py`
Expected: PASS with updated tutorial/site export media and no missing asset errors.

- [ ] **Step 3: Run the full project test suite**

Run: `make test`
Expected: PASS with coverage at or above the enforced repository threshold.

- [ ] **Step 4: Inspect the diff for accidental spillover**

Run: `git status --short`
Expected: only intended demo pipeline, artifact, and test changes are present; `.superpowers/` and `docs/superpowers/plans/` remain uncommitted if that is still the standing repo policy.

- [ ] **Step 5: Commit final fixes if verification required any**

```bash
git add src/octowright/demos src/octowright/video.py src/octowright/video_overlay.py scripts/demos tests demo/bundles demo/tutorial-export
git commit -m "fix: complete demo media redesign verification"
```

## Self-Review

### Spec coverage

- Presentation schema and approved modes: covered by Task 1.
- Subtle overlay policy: covered by Task 2.
- Timing controls: covered by Task 3.
- Composite versus synced multi-video outputs: covered by Task 3 and Task 5.
- Export metadata for downstream consumers: covered by Task 4.
- Per-demo retuning of the current catalog: covered by Task 5.
- Verification and regeneration: covered by Task 6.

### Placeholder scan

- No `TODO`, `TBD`, or “similar to” placeholders remain.
- Every task includes exact file paths, commands, and representative code/config snippets.

### Type consistency

- `DemoPresentationConfig`, `DemoOverlayConfig`, and `DemoTimingConfig` are introduced in Task 1 and referenced consistently later.
- `supporting_videos` is used consistently across runtime, manifest, and export tasks.

