# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from octowright.browser_pool import BrowserPool
from octowright.recorder import tail_log
from octowright.runner import _write_junit
from octowright.scenarios import Participant, Scenario, load_python_scenario, load_yaml_scenario
from octowright.scenarios_pool import LiveScenario, ScenarioPool
from octowright.video import optimize_png
from octowright_demos.models import DemoBundle, DemoMacroRun
from octowright_demos.public_artifacts import sanitize_public_artifacts
from octowright_demos.rendering import render_bundle_video, write_artifact_manifest, write_exports


async def record_demo_bundle(bundle: DemoBundle) -> dict[str, Any]:
    artifacts_dir = bundle.root / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    scenario = _load_bundle_scenario(bundle)
    runnable = _prepare_scenario(bundle, scenario)

    pool = BrowserPool()
    scenario_pool = ScenarioPool()
    live: LiveScenario | None = None
    close_results: dict[str, dict[str, Any]] = {}

    async with _dashboard_sidecar_if_needed(runnable):
        with _macro_dir(bundle):
            try:
                live = await scenario_pool.start(spec=runnable, browser_pool=pool)
                run_started = asyncio.get_running_loop().time()
                await _apply_intro_hold(bundle)
                await _run_bundle_macros(bundle, scenario_pool, live, pool)
                if bundle.recording.verify_report:
                    await _run_verify_suite(bundle, live, pool)
                await _apply_outro_hold(bundle)
                await _apply_minimum_duration(bundle, started_at=run_started)
                await _capture_poster(bundle, live, pool)
            finally:
                if live is not None:
                    close_results = await _close_live_scenario(live, pool, scenario_pool)
                await pool.shutdown()

    replay_path = _primary_replay_path(bundle)
    merged_events = _write_merged_replay(live, replay_path)
    _write_supporting_artifacts(bundle, live, scenario, replay_path)
    sanitize_public_artifacts(bundle)
    write_exports(replay_path)
    video_path = _primary_video_path(bundle)
    poster_path = _primary_poster_path(bundle)
    render_summary = render_bundle_video(
        bundle,
        live,
        close_results,
        video_path=video_path,
        poster_path=poster_path,
    )
    write_artifact_manifest(
        bundle,
        live,
        replay_path=replay_path,
        video_path=video_path,
        poster_path=poster_path,
        event_count=merged_events,
        render_summary=render_summary,
    )
    result = {
        "bundle_id": bundle.id,
        "replay_path": str(replay_path),
        "video_path": str(video_path),
        "poster_path": str(_primary_poster_path(bundle)),
        "event_count": merged_events,
    }
    supporting_videos = render_summary.get("supporting_videos", [])
    if supporting_videos:
        result["supporting_videos"] = [
            {
                "id": item["id"],
                "path": item["path"],
                "poster_path": item["poster_path"],
            }
            for item in supporting_videos
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("path"), str)
            and isinstance(item.get("poster_path"), str)
        ]
    return result


def _load_bundle_scenario(bundle: DemoBundle) -> Scenario:
    ref = bundle.scenario_ref
    if ref is None:
        raise ValueError(f"demo bundle {bundle.id!r} is missing source_refs.scenarios")
    path = _repo_root(bundle) / ref
    if not path.exists():
        raise FileNotFoundError(f"scenario ref for bundle {bundle.id!r} does not exist: {path}")
    if path.suffix == ".py":
        return load_python_scenario(path)
    return load_yaml_scenario(path.read_text(encoding="utf-8"), path.stem)


_DEMO_VIEWPORT_W = 1920
_DEMO_VIEWPORT_H = 1080
_DEFAULT_PLAYGROUND_BASE = "http://127.0.0.1:7900"


def _prepare_scenario(bundle: DemoBundle, scenario: Scenario) -> Scenario:
    participants: list[Participant] = []
    for index, participant in enumerate(scenario.participants):
        # Demo recordings always run at 1920x1080 so the resulting videos
        # land on a 1080p canvas (single-pane bundles) or feed a 1080p
        # composite (multi-pane bundles) without scale-up softness.
        # A scenario can still pin a different size by setting viewport_w/h.
        viewport_w = participant.viewport_w if participant.viewport_w else _DEMO_VIEWPORT_W
        viewport_h = participant.viewport_h if participant.viewport_h else _DEMO_VIEWPORT_H

        # Preserve explicit external URLs (http://, https://) so participants
        # pointing at the dashboard sidecar (or any live service) keep their
        # target URL instead of being rewritten to a bundle seed.
        if participant.url and (participant.url.startswith("http://") or participant.url.startswith("https://")):
            rewritten_url = _rewrite_playground_url(participant.url)
            participants.append(
                replace(
                    participant,
                    url=rewritten_url,
                    viewport_w=viewport_w,
                    viewport_h=viewport_h,
                    record_video=True,
                )
            )
            continue
        role_seed = bundle.recording.role_seeds.get(participant.role)
        target_url = _seed_url(bundle, role_seed, participant=participant, slot=index) if role_seed else None
        if target_url is None and bundle.recording.default_seed:
            target_url = _seed_url(bundle, bundle.recording.default_seed, participant=participant, slot=index)
        participants.append(
            replace(
                participant,
                url=target_url or participant.url,
                viewport_w=viewport_w,
                viewport_h=viewport_h,
                record_video=True,
            )
        )
    return Scenario(
        name=scenario.name,
        participants=participants,
        description=scenario.description,
        fixtures=dict(scenario.fixtures),
        teardown_macro=scenario.teardown_macro,
        verify=dict(scenario.verify),
    )


def _rewrite_playground_url(url: str) -> str:
    base_override = os.getenv("OCTOWRIGHT_PLAYGROUND_BASE_URL", "").strip()
    if not base_override:
        return url

    parsed_url = urlparse(url)
    parsed_default = urlparse(_DEFAULT_PLAYGROUND_BASE)
    parsed_override = urlparse(base_override)
    if (
        parsed_url.scheme != parsed_default.scheme
        or parsed_url.hostname != parsed_default.hostname
        or parsed_url.port != parsed_default.port
    ):
        return url
    return urlunparse(
        (
            parsed_override.scheme,
            parsed_override.netloc,
            parsed_url.path,
            parsed_url.params,
            parsed_url.query,
            parsed_url.fragment,
        )
    )


def _seed_url(
    bundle: DemoBundle, rel_path: str, *, participant: Participant | None = None, slot: int | None = None
) -> str:
    # Octowright's navigation guard denies the file:// scheme, so a seed can't be
    # opened directly from disk. When a static seed server is running
    # (scripts/demos/with_seed_server.py sets OCTOWRIGHT_SEED_BASE_URL rooted at
    # the bundle dir) build an http:// URL against it; otherwise fall back to the
    # file:// URI for offline path computations and tests.
    seed_base = os.getenv("OCTOWRIGHT_SEED_BASE_URL", "").strip()
    if seed_base:
        base = f"{seed_base.rstrip('/')}/{rel_path.lstrip('/')}"
    else:
        base = (bundle.root / rel_path).resolve().as_uri()
    if participant is None:
        return base
    query = urlencode(
        {
            "persona": participant.persona,
            "role": participant.role,
            "kind": participant.kind,
            "slot": slot if slot is not None else 0,
        }
    )
    return f"{base}?{query}"


async def _run_bundle_macros(
    bundle: DemoBundle,
    scenario_pool: ScenarioPool,
    live: LiveScenario,
    browser_pool: BrowserPool,
) -> None:
    for macro_run in bundle.recording.macros:
        outcome = await scenario_pool.run_macro(
            scenario_id=live.scenario_id,
            macro=macro_run.name,
            browser_pool=browser_pool,
            role=macro_run.role,
            args=macro_run.args,
        )
        failures = [item for item in outcome["results"] if not item["ok"]]
        if failures:
            raise RuntimeError(f"demo bundle {bundle.id!r} macro {macro_run.name!r} failed: {failures}")


async def _apply_intro_hold(bundle: DemoBundle) -> None:
    if bundle.presentation.timing.intro_ms > 0:
        await asyncio.sleep(bundle.presentation.timing.intro_ms / 1000)


async def _apply_outro_hold(bundle: DemoBundle) -> None:
    if bundle.presentation.timing.outro_ms > 0:
        await asyncio.sleep(bundle.presentation.timing.outro_ms / 1000)


async def _apply_minimum_duration(bundle: DemoBundle, *, started_at: float) -> None:
    minimum_seconds = bundle.presentation.timing.minimum_ms / 1000
    if minimum_seconds <= 0:
        return
    elapsed = asyncio.get_running_loop().time() - started_at
    remaining = minimum_seconds - elapsed
    if remaining > 0:
        await asyncio.sleep(remaining)


async def _run_verify_suite(bundle: DemoBundle, live: LiveScenario, pool: BrowserPool) -> None:
    report_path = bundle.root / bundle.recording.verify_report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for participant in live.participants:
        macro = live.spec.verify.get(participant["role"])
        if not macro:
            continue
        start = asyncio.get_running_loop().time()
        error: str | None = None
        try:
            session = pool.get(participant["instance_id"])
            await _run_macro_direct(session, DemoMacroRun(name=macro))
            ok = True
        except Exception as exc:
            ok = False
            error = repr(exc)
        duration = asyncio.get_running_loop().time() - start
        results.append(
            {
                "name": f"{participant['role']}:{participant['persona']}",
                "ok": ok,
                "error": error,
                "duration": duration,
            }
        )
    _write_junit(results, report_path, kind="scenario")
    if any(not item["ok"] for item in results):
        raise RuntimeError(f"demo bundle {bundle.id!r} verify suite failed")


async def _capture_poster(bundle: DemoBundle, live: LiveScenario, pool: BrowserPool) -> None:
    session = pool.get(_primary_participant(live, bundle)["instance_id"])
    target = _primary_poster_path(bundle)
    target.parent.mkdir(parents=True, exist_ok=True)
    await session.page.screenshot(path=str(target), scale="css")
    if target.stat().st_size > 500_000:
        optimize_png(target)


async def _close_live_scenario(
    live: LiveScenario,
    pool: BrowserPool,
    scenario_pool: ScenarioPool,
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    scenario_pool._live.pop(live.scenario_id, None)
    for participant in live.participants:
        instance_id = participant["instance_id"]
        results[instance_id] = await pool.close(instance_id)
    return results


def _write_merged_replay(live: LiveScenario | None, replay_path: Path) -> int:
    if live is None:
        raise RuntimeError("cannot write replay without a live scenario")
    replay_path.parent.mkdir(parents=True, exist_ok=True)
    merged: list[dict[str, Any]] = []
    for participant in live.participants:
        log_path = Path(participant["log_path"])
        events, _, _ = tail_log(log_path, 0)
        for event in events:
            merged.append(
                {
                    **event,
                    "instance_id": participant["instance_id"],
                    "persona": participant["persona"],
                    "role": participant["role"],
                    "kind": participant["kind"],
                }
            )
    merged.sort(key=lambda item: item.get("ts", ""))
    with replay_path.open("w", encoding="utf-8") as handle:
        for event in merged:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return len(merged)


def _write_supporting_artifacts(
    bundle: DemoBundle,
    live: LiveScenario | None,
    scenario: Scenario,
    replay_path: Path,
) -> None:
    extras = set(bundle.recording.extras)
    if "replay-roundtrip" in extras:
        roundtrip_path = _match_replay_artifact(bundle, "roundtrip")
        shutil.copyfile(replay_path, roundtrip_path)
    if "participant-roster" in extras:
        if live is None:
            raise RuntimeError("cannot write participant roster without live scenario")
        roster_path = _match_replay_artifact(bundle, "participant-roster")
        payload = {
            "scenario_id": live.scenario_id,
            "participants": live.participants,
        }
        roster_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if "mock-routes" in extras:
        mock_routes_path = _match_replay_artifact(bundle, "mock-routes")
        payload = {
            "mock_routes": list(scenario.fixtures.get("mock_routes", [])),
            "dialog_policy": scenario.fixtures.get("dialog_policy"),
        }
        mock_routes_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _primary_replay_path(bundle: DemoBundle) -> Path:
    for rel_path in bundle.replay_artifacts:
        if rel_path.endswith("replay.jsonl"):
            return bundle.root / rel_path
    if bundle.replay_artifacts:
        return bundle.root / bundle.replay_artifacts[0]
    raise ValueError(f"demo bundle {bundle.id!r} has no replay artifact declaration")


def _primary_participant(live: LiveScenario, bundle: DemoBundle) -> dict[str, Any]:
    primary_role = bundle.recording.primary_role
    if primary_role is None:
        return live.participants[0]
    for participant in live.participants:
        if participant["role"] == primary_role:
            return participant
    raise RuntimeError(f"demo bundle {bundle.id!r} primary role {primary_role!r} was not launched")


def _match_replay_artifact(bundle: DemoBundle, slug: str) -> Path:
    matches = [bundle.root / path for path in bundle.replay_artifacts if slug in path]
    if not matches:
        raise ValueError(f"demo bundle {bundle.id!r} has no replay artifact containing {slug!r}")
    return matches[0]


def _primary_video_path(bundle: DemoBundle) -> Path:
    for rel_path in bundle.video_artifacts:
        if rel_path.endswith(".mp4"):
            return bundle.root / rel_path
    raise ValueError(f"demo bundle {bundle.id!r} has no mp4 video artifact declaration")


def _primary_poster_path(bundle: DemoBundle) -> Path:
    for rel_path in bundle.video_artifacts:
        if rel_path.endswith(".png"):
            return bundle.root / rel_path
    raise ValueError(f"demo bundle {bundle.id!r} has no poster artifact declaration")


@contextlib.contextmanager
def _macro_dir(bundle: DemoBundle):
    import octowright.macros.storage as macro_storage

    temp_dir = Path(tempfile.mkdtemp(prefix=f"octowright-demo-macros-{bundle.id}-"))
    original = macro_storage.MACROS_DIR
    try:
        for ref in bundle.macro_refs:
            source = _repo_root(bundle) / ref
            shutil.copy2(source, temp_dir / source.name)
        macro_storage.MACROS_DIR = temp_dir
        yield
    finally:
        macro_storage.MACROS_DIR = original
        shutil.rmtree(temp_dir, ignore_errors=True)


def _repo_root(bundle: DemoBundle) -> Path:
    try:
        demo_dir = bundle.root.parents[1]
        if demo_dir.name == "bundles":
            return bundle.root.parents[2]
    except IndexError:
        pass
    return Path.cwd()


async def _run_macro_direct(session: Any, macro_run: DemoMacroRun) -> None:
    from octowright import macros as macro_module

    await macro_module.run_macro(session=session, name=macro_run.name, args=macro_run.args)


def _scenario_needs_dashboard(scenario: Scenario) -> bool:
    """True if any participant points at the local dashboard sidecar."""
    from octowright.defaults import HTTP_HOST, HTTP_PORT

    needles = (f"127.0.0.1:{HTTP_PORT}", f"localhost:{HTTP_PORT}", f"{HTTP_HOST}:{HTTP_PORT}")
    for participant in scenario.participants:
        if not participant.url:
            continue
        if any(needle in participant.url for needle in needles):
            return True
    return False


@contextlib.asynccontextmanager
async def _dashboard_sidecar_if_needed(scenario: Scenario):
    """Start the Octowright HTTP dashboard in-process when a participant
    references the local dashboard URL. Tears down on exit.

    Yields immediately if no participant references the dashboard.
    """
    if not _scenario_needs_dashboard(scenario):
        yield
        return

    import uvicorn

    from octowright.defaults import HTTP_HOST, HTTP_PORT
    from octowright.http.app import build_app

    app = build_app(mcp_leader=False)
    app.state.octowright_http_host = HTTP_HOST
    config = uvicorn.Config(
        app=app,
        host=HTTP_HOST,
        port=HTTP_PORT,
        log_level="warning",
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    serve_task = asyncio.create_task(server.serve())

    # Wait until uvicorn reports started, with a hard timeout so a stuck
    # server can't block the recording forever.
    for _ in range(60):
        if server.started:
            break
        await asyncio.sleep(0.1)
    if not server.started:
        serve_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await serve_task
        raise RuntimeError(f"dashboard sidecar failed to start on {HTTP_HOST}:{HTTP_PORT}")

    try:
        yield
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(serve_task, timeout=5.0)
