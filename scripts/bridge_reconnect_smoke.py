#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (C) 2026 provide.io llc
# SPDX-License-Identifier: Apache-2.0
# SPDX-Comment: Part of octowright.
#

from __future__ import annotations

import anyio
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


async def _call_status(session: ClientSession) -> str:
    result = await session.call_tool("octowright_status", {})
    return result.content[0].text


async def main() -> None:
    params = StdioServerParameters(command=".venv/bin/octowright", args=["serve"], cwd=".")
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        with anyio.fail_after(30):
            await session.initialize()
            tools = await session.list_tools()
            print(f"tools={len(tools.tools)}")
            first = await _call_status(session)
            print(f"first_status_bytes={len(first)}")
            second = await _call_status(session)
            print(f"second_status_bytes={len(second)}")


if __name__ == "__main__":
    anyio.run(main)
