# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

import asyncio
import json
from pathlib import Path

from octowright.server.macro_semantic import macro_explain


async def main():
    macro_path = Path("examples/macros/discord-style-login.json")
    with open(macro_path) as f:
        macro_data = json.load(f)

    actions = macro_data["actions"]
    result = await macro_explain(actions)

    print("--- Summary ---")
    print(result["summary"])
    print("\n--- Intent ---")
    print(result["intent"])


if __name__ == "__main__":
    asyncio.run(main())
