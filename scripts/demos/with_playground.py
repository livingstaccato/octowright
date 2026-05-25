#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Run a demo recording with the demo/playground/ server running alongside.

The two playground-targeted bundles (seven-mix-orchestration, role-based-duo)
hit ``http://127.0.0.1:7900/`` for shared state. This wrapper starts the
playground server, records the bundle, then stops the server cleanly.

Usage::

    uv run python scripts/demos/with_playground.py seven-mix-orchestration
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Make the recording-helpers module importable and put the playground server
# package on sys.path (it lives at demo/playground/, not under src/).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402
from _shared import bundle_map, prepare_bundle, rewrite_index, write_tutorial_export  # type: ignore[import-not-found]
from octowright_demos.runtime import record_demo_bundle

from demo.playground.server import PlaygroundServer


async def _record_with_playground(demo_id: str) -> int:
    bundles = bundle_map()
    bundle = bundles.get(demo_id)
    if bundle is None:
        available = ", ".join(sorted(bundles)) or "none"
        print(f"unknown demo bundle: {demo_id}. available bundles: {available}", file=sys.stderr)
        return 1

    server = PlaygroundServer()
    print(f"starting playground at {server.url}", flush=True)
    await server.start()
    try:
        prior_base = os.environ.get("OCTOWRIGHT_PLAYGROUND_BASE_URL")
        os.environ["OCTOWRIGHT_PLAYGROUND_BASE_URL"] = server.url
        try:
            recording = await record_demo_bundle(bundle)
        finally:
            if prior_base is None:
                os.environ.pop("OCTOWRIGHT_PLAYGROUND_BASE_URL", None)
            else:
                os.environ["OCTOWRIGHT_PLAYGROUND_BASE_URL"] = prior_base
    finally:
        await server.stop()

    rewrite_index(list(bundles.values()))
    tutorial_export = prepare_bundle(bundle)
    export_path = write_tutorial_export(bundle)
    print(f"recorded demo bundle: {bundle.id}")
    if export_path is None:
        print("tutorial export: not configured")
    else:
        print(f"tutorial export written: {export_path}")
    print(f"replay path: {recording['replay_path']}")
    print(f"video path: {recording['video_path']}")
    print(f"poster path: {recording['poster_path']}")
    print(
        "assets: replay={replay} video={video}".format(
            replay=len(tutorial_export["assets"]["replay"]),
            video=len(tutorial_export["assets"]["video"]),
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: uv run python scripts/demos/with_playground.py <demo-id>", file=sys.stderr)
        return 2
    return asyncio.run(_record_with_playground(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
