# Getting Started

This guide takes you from a fresh clone to a verified-working install in under five
minutes. After this, see the topic guides ([personas](personas.md),
[macros](macros.md), [scenarios](scenarios.md)) for everything else.

## 1. Install

From the Octowright repo root:

```bash
uv sync
uv run playwright install webkit firefox chromium
```

`uv sync` resolves and installs the Python environment from `uv.lock`; the second
line downloads the actual browser binaries Playwright drives.

> Octowright uses `uv` exclusively — there are no `pip install` instructions
> because Octowright depends on `uv.lock` for reproducible dependency resolution.
>
> Engine management is currently CLI-only (Playwright install/list commands),
> not exposed as Octowright MCP tools.

## 2. Register with an MCP Client

Add Octowright to your MCP client configuration. For project-scoped clients, that
is often `.mcp.json`; for Claude Code's global config, use `~/.claude.json`:

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

Replace `<absolute-path-to-octowright>` with the expanded path on your machine.
Then reload your MCP client — the tools should appear as
`mcp__octowright__browser_launch`, `mcp__octowright__browser_click`, etc.

The shortcut:

```bash
uv run octowright init
```

This prints a ready-to-paste config snippet with the path filled in for your install,
and scaffolds sample persona / scenario / macro files under
Octowright's user config directory.

## 3. Verify the install

A four-call smoke test that exercises launch, drive, list, and close:

1. Ask your MCP client to call `browser_launch` with `kind=webkit` and `url=https://octowright.com`.
2. Call `browser_click_by` on the link text "More information".
3. Call `browser_list` and confirm exactly one live instance.
4. Call `browser_close` with the `instance_id` from step 1.

If all four succeed, the install, Playwright runtime, and MCP wiring are healthy.

If something fails, jump to [troubleshooting.md](troubleshooting.md) — most failures
are engine-binary install problems fixed by reinstalling Playwright browsers.

## CLI surface

Octowright ships a Click-based CLI. Useful subcommands:

| Command | Purpose |
|---|---|
| `octowright serve` | Run the MCP stdio server + the dashboard HTTP server (default). |
| `octowright init` | Scaffold standard config dirs and print a registration snippet. |
| `octowright selftest` | Print the registered MCP tools without needing a live MCP client. |
| `octowright persona ...` | Manage personas from the terminal (`list` / `show` / `create` / `delete`). |
| `octowright scenario ...` | Start, list, or run scenarios as tests. |
| `octowright skill ...` | Install/update the distributed skill pack and plugin manifests. |
| `octowright cleanup` | Prune old recording artefacts. |
| `octowright takeover` | Detect and disable competing Playwright MCP plugins. |
| `octowright test` | Run `[test]`-tagged macros and emit JUnit XML. |

## Slimming the LLM tool surface

The full MCP surface is currently 111 tools. When a workflow only needs a slice (driving
a browser, replaying macros, etc.), pass `--profile` to slim what the LLM
sees at connection time:

```bash
octowright serve --profile=core              # 20 tools — browser core + always-on diagnostics
octowright serve --profile=core,macros       # 29 tools — browser + macro replay + always-on diagnostics
octowright serve --profile=core,scenarios    # browser + multi-browser orchestration
```

The named profiles (`core`, `advanced`, `macros`, `scenarios`, `personas`)
live in `src/octowright/server/profiles.py`. Omit `--profile` (or use `all`)
to register every tool — that is the default.

Setting the equivalent env var (`OCTOWRIGHT_PROFILE=core,macros`) works too
and is what the daemon process inherits when `octowright serve` spawns one.

## Next steps

- Open the dashboard at `http://127.0.0.1:6286/` while `octowright serve` is running.
- Read [personas.md](personas.md) to learn the identity/profile model.
- Read [macros.md](macros.md) to record and replay flows.
- Read [scenarios.md](scenarios.md) to orchestrate multiple browsers as a unit.
