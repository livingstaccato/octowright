# Antigravity (agy) Integration

Octowright ships first-class support for [Antigravity CLI](https://github.com/antigravity-dev/antigravity-cli) (`agy`).

## Quick install

```bash
uv run octowright skill install --target antigravity
```

This copies the `using-octowright` skill into `~/.gemini/config/plugins/octowright/` — the
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

## Register the MCP server with agy

`agy` reads MCP server configuration from `~/.gemini/config/mcp_config.json` (the same
file Gemini CLI uses). Add the Octowright server entry:

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

Replace `/abs/path/to/octowright` with the actual path to your Octowright checkout.

## Plugin manifest in the repo

The `.antigravity-plugin/plugin.json` at the repository root lets `agy plugin validate`
treat the repo itself as a valid plugin source. You can also install directly from the repo:

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

- **No MCP server auto-registration.** The install step only copies the skill files and
  `plugin.json`. You must manually add the `mcpServers` block to
  `~/.gemini/config/mcp_config.json` (see above).
- **Shared store with Gemini CLI.** agy uses the same `~/.gemini/config/plugins/` directory
  that Gemini CLI manages. Changes to plugins there may affect both tools.
- **No hooks support yet.** The agy plugin manifest supports a `hooks.json` but Octowright
  does not currently ship any hooks for agy. Session-start guidance is delivered via the
  `using-octowright` skill instead.
