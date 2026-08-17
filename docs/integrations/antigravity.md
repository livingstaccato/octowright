# Antigravity (agy) Integration

Octowright ships first-class support for [Antigravity CLI](https://github.com/google-antigravity/antigravity-cli) (`agy`).

## Quick install

```bash
uv run octowright skill install --target antigravity
```

This copies the `octowright` skill into `~/.gemini/config/plugins/octowright/` — the
plugin store that `agy` shares with Gemini CLI. Override the store root with the
`ANTIGRAVITY_HOME` env var (default `~/.gemini/config`) if your `agy` installation uses a
non-default path.

## Verify the install

```bash
agy plugin validate ~/.gemini/config/plugins/octowright
```

Expected output:

```
[ok]    ~/.gemini/config/plugins/octowright
    ✔ skills      : 1 processed
    - agents      : skipped (not found)
    - commands    : skipped (not found)
    - mcpServers  : skipped (not found)
    - hooks       : skipped (not found)
```

## MCP server registration (automatic)

`octowright skill install --target antigravity` ships an `mcp_config.json` into the
plugin directory (`~/.gemini/config/plugins/octowright/mcp_config.json`) that registers:

```json
{
  "mcpServers": {
    "octowright": {
      "command": "uvx",
      "args": ["octowright", "serve"]
    }
  }
}
```

This uses `uvx` so no separate `pip install octowright` is needed — `uv` resolves and
runs the package on demand. If `uvx` isn't on your PATH, install `uv` first
(`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`).

If you prefer a development-checkout pinned to a specific local path, override
`mcp_config.json` after install with:

```json
{
  "mcpServers": {
    "octowright": {
      "command": "uv",
      "args": ["--directory", "/abs/path/to/octowright", "run", "octowright", "serve"]
    }
  }
}
```

## Plugin manifest in the repo

The `.antigravity-plugin/` directory at the repo root ships both `plugin.json` and
`mcp_config.json` so `agy plugin install /path/to/octowright` validates and
wires up the MCP server in one step:

```bash
agy plugin install /path/to/octowright
```

## Updating

```bash
uv run octowright skill install --target antigravity --force
```

The `--force` flag overwrites the previously installed skill files with the packaged version.

## Checking install status

```bash
uv run octowright skill status --target antigravity
```

Reports whether the skill is installed and whether its hash matches the packaged version (hash
mismatch means local drift from the shipped copy).

## Known limitations vs Claude Code / Codex

- **Shared store with Gemini CLI.** agy uses the same `~/.gemini/config/plugins/` directory
  that Gemini CLI manages. Changes to plugins there may affect both tools.
- **No hooks support.** The agy plugin manifest supports a `hooks.json` but Octowright
  does not ship any hooks for agy. Session-start guidance is delivered via the
  `octowright` skill instead.
- **`uvx` required for the default config.** The default `mcp_config.json` uses
  `uvx octowright serve` for portability. Without `uv` installed, edit `mcp_config.json`
  to point at your install path directly.
