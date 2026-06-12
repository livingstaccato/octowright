#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Re-record a seed-based demo bundle with its files served over HTTP.

Seed-based bundles (e.g. ``verify-suite``) load a local stage page. Octowright's
navigation guard denies the ``file://`` scheme, so the seed can't be opened
directly from disk. This wrapper starts a static HTTP server rooted at the
bundle directory on an ephemeral port, points the runtime at it via
``OCTOWRIGHT_SEED_BASE_URL``, records the bundle, then stops the server.

Usage::

    uv run python scripts/demos/with_seed_server.py verify-suite
"""

from __future__ import annotations

import functools
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Make the recording-helpers module importable and put the repo root (which holds
# the out-of-wheel octowright_demos package) on sys.path.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ruff: noqa: E402
from _shared import (  # type: ignore[import-not-found]
    bundle_map,
    prepare_bundle,
    record_bundle,
    rewrite_index,
    write_tutorial_export,
)


class _QuietHandler(SimpleHTTPRequestHandler):
    # Suppress per-request stderr logging so the recording output stays readable.
    def log_message(self, *args: object) -> None:
        pass


def _serve(directory: Path) -> ThreadingHTTPServer:
    """Start a daemon-thread static server rooted at ``directory`` on a free port."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, name="seed-http", daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: uv run python scripts/demos/with_seed_server.py <demo-id>", file=sys.stderr)
        return 2

    demo_id = args[0]
    bundles = bundle_map()
    bundle = bundles.get(demo_id)
    if bundle is None:
        available = ", ".join(sorted(bundles)) or "none"
        print(f"unknown demo bundle: {demo_id}. available bundles: {available}", file=sys.stderr)
        return 1

    server = _serve(bundle.root)
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"serving seed dir {bundle.root} at {base_url}", flush=True)

    prior = os.environ.get("OCTOWRIGHT_SEED_BASE_URL")
    os.environ["OCTOWRIGHT_SEED_BASE_URL"] = base_url
    try:
        recording = record_bundle(bundle)
        rewrite_index(list(bundles.values()))
        prepare_bundle(bundle)
        export_path = write_tutorial_export(bundle)
    finally:
        if prior is None:
            os.environ.pop("OCTOWRIGHT_SEED_BASE_URL", None)
        else:
            os.environ["OCTOWRIGHT_SEED_BASE_URL"] = prior
        server.shutdown()

    print(f"recorded demo bundle: {bundle.id}")
    if export_path is None:
        print("tutorial export: not configured")
    else:
        print(f"tutorial export written: {export_path}")
    print(f"replay path: {recording['replay_path']}")
    print(f"video path: {recording['video_path']}")
    print(f"poster path: {recording['poster_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
