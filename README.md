# octowright

An MCP server that lets Claude Code drive **many headed Playwright browsers in parallel**
with a **mix of engines** (Chromium, Firefox, WebKit), recording every action to a JSONL
log so a session can later be exported as a standalone Playwright script.

The existing official Playwright MCP plugin only supports one browser context and doesn't
let you pick the engine per launch. octowright fixes both and adds persistent profiles
so login state survives across runs.

## Install

```bash
cd ~/code/gh/provide-io/octowright
uv sync
uv run playwright install webkit firefox chromium
```

## Register with Claude Code

Add to `.mcp.json` in the repo you want to use it from (or `~/.claude.json` globally):

```json
{
  "mcpServers": {
    "octowright": {
      "command": "uv",
      "args": [
        "--directory",
        "/Users/tim/code/gh/provide-io/octowright",
        "run",
        "octowright",
        "serve"
      ]
    }
  }
}
```

Reload Claude; tools appear as `mcp__octowright__browser_launch`, etc.

## Tools

Every mutating tool takes an `instance_id` returned from `browser_launch`. Each call
appends a record to that instance's JSONL log.

| Tool | What |
|---|---|
| `browser_launch` | Launch a new headed browser. `kind` = `chromium` / `firefox` / `webkit`. Returns `instance_id`. |
| `browser_list` | List all live instances. |
| `browser_close` / `browser_close_all` | Close one / all. |
| `browser_navigate` | Navigate a specific instance. |
| `browser_click` / `browser_type` / `browser_fill` / `browser_press_key` | Input. |
| `browser_screenshot` | PNG to disk. |
| `browser_snapshot` | Accessibility tree. |
| `browser_evaluate` | Run JS in the page. |
| `browser_console_messages` | Collected console output since launch. |
| `browser_wait_for` | Wait for selector / text. |
| `browser_recording_path` | Path to the JSONL action log for this instance. |
| `browser_export_script` | Emit a Playwright Python (or TS) script that replays the log. |
| `profile_list` | List saved persistent profiles. |
| `profile_delete` | Wipe a saved profile (refuses if a live browser is using it). |
| `macro_save` | Save current recording as a named macro, with parameter substitution. |
| `macro_list` | List saved macros. |
| `macro_run` | Replay a macro against a live instance with args. |
| `macro_delete` | Delete a saved macro. |

## Persistent profiles (Discord, Slack, N-login-per-app)

By default `browser_launch` creates an ephemeral browser — cookies, localStorage, and
IndexedDB die on close. To **keep login state across runs**, pass a `profile` name:

```
browser_launch kind=webkit profile=disc-1 url=https://discord.com/login
```

Each `(kind, profile)` pair gets its own on-disk user-data-dir under `~/.config/undef/profiles/<kind>/<name>/`.
First launch opens a fresh browser; after you log in manually, closing the browser flushes
state to disk. The next launch with the same `profile` skips the login (Discord /
Slack / etc. treat it as a returning session).

**Cookie isolation:** each live browser has its own `BrowserContext`, so seven logged-in
Discord tabs you run in parallel never share cookies, localStorage, or IndexedDB — even
if they're all `kind=webkit`.

Example — seven Discord accounts on seven WebKit windows, reusable later:

```
# First time: open all seven, log each one in manually
browser_launch kind=webkit profile=disc-1 url=https://discord.com/login label=acct-1
browser_launch kind=webkit profile=disc-2 url=https://discord.com/login label=acct-2
...
browser_launch kind=webkit profile=disc-7 url=https://discord.com/login label=acct-7

# Close them — profiles flush to disk
browser_close_all

# Days later: re-launch and skip login entirely
browser_launch kind=webkit profile=disc-1 url=https://discord.com/app
...
```

`profile_list` enumerates saved profiles; `profile_delete` wipes one (refuses while a
live instance is using it). Exported replay scripts embed the absolute `user_data_dir`
path, so they work on the same machine but are **not portable across machines** when a
profile is involved.

## Macros — reusable parameterized action sequences

Turn a recorded browser session into a named, reusable macro. Capture a login flow
once, replay it with different credentials later. Example workflow:

```
# 1. Manually log into Discord on a live instance
browser_launch kind=webkit profile=disc-1 url=https://discord.com/login label=acct-1
# ... fill email, password, submit ...

# 2. Snapshot those actions as a macro, telling octowright which literal values
#    to treat as parameters:
macro_save instance_id=<id> name=discord-login \
           parameters={"email":"me@example.com","password":"hunter2"}

# 3. Days later, against a fresh instance, replay it with different creds:
browser_launch kind=webkit profile=disc-2 url=https://discord.com/login label=acct-2
macro_run instance_id=<new-id> name=discord-login \
          args={"email":"other@example.com","password":"correcthorsebatterystaple"}
```

`macro_list` enumerates saved macros; `macro_delete` removes one. Macros live at
`~/.config/undef/macros/<name>.json` (override with `OCTOWRIGHT_MACROS_DIR`).

Lifecycle actions (`launch`, `close`, `snapshot`) are dropped by default — macros
are the reusable middle of a flow, not the wrapper. Pass `include_launch=True` on
`macro_save` if you need the initial navigation baked in.

**Caveat:** JSONL macros break when the target site changes its DOM (Discord
rewrites its CSS classes frequently). Treat them as short-term automation, not
durable scripts — when a macro breaks, re-record rather than hand-patch.

## Defaults

Configurable via env vars:

- `OCTOWRIGHT_DEFAULT_URL` — fallback `url` when `browser_launch` omits it. Defaults to `https://warp.undef.games`.
- `OCTOWRIGHT_RECORDINGS` — where JSONL logs land. Defaults to `./recordings/` in this repo.
- `OCTOWRIGHT_PROFILES_DIR` — where persistent profiles live. Defaults to `~/.config/undef/profiles/`.
- `OCTOWRIGHT_VIEWPORT_W` / `OCTOWRIGHT_VIEWPORT_H` — default window size (1280×800).
- `OCTOWRIGHT_HEADLESS` — set to `1` to default to headless mode (default is headed).
- `OCTOWRIGHT_NAV_TIMEOUT_MS` / `OCTOWRIGHT_ACTION_TIMEOUT_MS` — per-navigation / per-action timeouts.
- `OCTOWRIGHT_MACROS_DIR` — where saved macros live. Defaults to `~/.config/undef/macros/`.

## Safari caveat

Playwright's `webkit` channel is the **bundled upstream WebKit engine**, not Apple's
Safari.app. It shares the engine family but is a separate binary (`playwright install
webkit`). Driving actual Safari.app with your cookies/profile requires Apple's
`safaridriver` and is not supported by Playwright today.

## Selftest

```bash
uv run octowright selftest
```

Prints the list of registered tools without needing a live MCP client.
