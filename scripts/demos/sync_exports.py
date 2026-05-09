# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

"""Regenerate `demo/tutorial-export/` from `demo/bundles/` without re-recording.

The tutorial-export tree is a verbatim mirror of bundle artifacts plus rendered
JSON manifests. It is consumed by site-octowright-com's sync script. Bundle
artifacts are the source-of-truth — they require browser sessions to regenerate
and live in git. The export tree is derived in seconds via `shutil.copytree`
and `json.dumps`, so it is gitignored; this script regenerates it on demand.
"""

from __future__ import annotations

import _shared
from _shared import list_demo_bundles, sync_tutorial_exports


def main() -> int:
    bundles = list_demo_bundles()
    payloads = sync_tutorial_exports(bundles)
    print(f"tutorial export root: {_shared.TUTORIAL_EXPORT_ROOT}")
    print(f"synced {len(payloads)} hero payload(s) for {len(bundles)} bundle(s)")
    for payload_path in payloads:
        print(f"- {payload_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
