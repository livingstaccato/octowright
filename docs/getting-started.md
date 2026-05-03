# Getting Started

## Install

```bash
cd /path/to/octowright
uv sync
uv run playwright install webkit firefox chromium
```

## Register with Claude Code

Add to your `.mcp.json` (project) or `~/.claude.json` (global):

```json
{
  "mcpServers": {
    "octowright": {
      "command": "uv",
      "args": ["--directory", "<absolute-path-to-octowright>", "run", "octowright", "serve"]
    }
  }
}
```

You can also run:

```bash
uv run octowright init
```

That prints a ready-to-paste config snippet and scaffolds sample persona/scenario/macro files.

## First Validation Flow

1. Ask your MCP client to call `browser_launch` with `kind=webkit` and `url=https://example.com`.
2. Call `browser_click_by` on "More information".
3. Call `browser_list` and confirm one live instance.
4. Call `browser_close` on the `instance_id`.

If those succeed, installation, browser runtime, and MCP wiring are healthy.

## CLI Surface

- `octowright serve`
- `octowright init`
- `octowright selftest`
- `octowright persona ...`
- `octowright scenario ...`
- `octowright skill ...`
- `octowright cleanup`
- `octowright takeover`
- `octowright test`
