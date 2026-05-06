# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import _shared
from _shared import list_demo_bundles, record_bundle, rewrite_index, sync_tutorial_exports, write_many_tutorial_exports


def main() -> int:
    all_bundles = list_demo_bundles()
    heroes = [bundle for bundle in all_bundles if bundle.hero]
    for bundle in heroes:
        record_bundle(bundle)
    rewrite_index(all_bundles)
    write_many_tutorial_exports(heroes)
    payloads = sync_tutorial_exports(heroes, heroes_only=True)
    print(f"website hero bundles regenerated: {len(heroes)}")
    print(f"tutorial export root: {_shared.TUTORIAL_EXPORT_ROOT}")
    print(f"synced payloads: {len(payloads)}")
    for payload_path in payloads:
        print(f"- {payload_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
