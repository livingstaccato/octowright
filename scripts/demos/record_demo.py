# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import sys

from _shared import bundle_map, prepare_bundle, record_bundle, rewrite_index, write_tutorial_export


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: uv run python scripts/demos/record_demo.py <demo-id>", file=sys.stderr)
        return 2

    demo_id = args[0]
    bundles = bundle_map()
    bundle = bundles.get(demo_id)
    if bundle is None:
        available = ", ".join(sorted(bundles)) or "none"
        print(f"unknown demo bundle: {demo_id}. available bundles: {available}", file=sys.stderr)
        return 1

    recording = record_bundle(bundle)
    rewrite_index(list(bundles.values()))
    tutorial_export = prepare_bundle(bundle)
    export_path = write_tutorial_export(bundle)
    print(f"recorded demo bundle: {bundle.id}")
    if export_path is None:
        print("tutorial export: not configured")
    else:
        print(f"tutorial export written: {export_path}")
    print(f"assets: replay={len(tutorial_export['assets']['replay'])} video={len(tutorial_export['assets']['video'])}")
    print(f"replay path: {recording['replay_path']}")
    print(f"video path: {recording['video_path']}")
    print(f"poster path: {recording['poster_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
