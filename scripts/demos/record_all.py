# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

from _shared import list_demo_bundles, rewrite_index, write_many_tutorial_exports


def main() -> int:
    bundles = list_demo_bundles()
    rewrite_index(bundles)
    exports = write_many_tutorial_exports(bundles)
    print(f"prepared demo bundles: {len(exports)}")
    for bundle, export_path in exports:
        if export_path is None:
            print(f"- {bundle.id} -> no tutorial export configured")
        else:
            print(f"- {bundle.id} -> {export_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
