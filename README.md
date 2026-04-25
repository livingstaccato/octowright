# octowright

An MCP server that lets Claude Code drive **many headed Playwright browsers in parallel**
with a **mix of engines** (Chromium, Firefox, WebKit), recording every action to a JSONL
log so a session can later be exported as a standalone Playwright script.

The existing official Playwright MCP plugin only supports one browser context and doesn't
let you pick the engine per launch. octowright fixes both and adds persistent profiles
so login state survives across runs.

For the full picture of how the pieces fit together — pool, sessions, personas,
scenarios, macros, the live-event tail and the test runner — see
[docs/architecture/](docs/architecture/) (PlantUML sources rendered to SVG).

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

## Your first 5 minutes

Once installed and registered, ask Claude to walk through these in order. Each step
builds on the previous one and shows you what octowright actually does.

**1. Open a browser.** Ask Claude: *"launch a webkit browser at example.com"*. Claude
calls `browser_launch kind=webkit url=https://example.com`. A real WebKit window opens
on your desktop. The result includes the `instance_id` — Claude tracks it for you.

**2. Drive it.** Ask: *"click the 'More information' link"*. Claude calls
`browser_click_by text="More information"`. The window navigates. Every action lands
in a JSONL recording on disk.

**3. List what's open.** Ask: *"what browsers are running?"*. `browser_list` returns a
one-line summary like `1 browser: 8a3f.../webkit @ iana.org/help/example-domains`.

**4. Save the session as a macro.** Ask: *"save the last few clicks as a macro called
example-tour"*. Claude calls `macro_save`. Now `macro_run name=example-tour` replays it.

**5. Close the browser, then launch a *named* one.** Ask: *"close that browser, then
launch a chromium browser with profile=demo at github.com"*. The window opens, you log
in (or whatever). When you close it, the cookies/localStorage flush to
`~/.config/undef/profiles/demo/chromium/`. Re-launch with the same profile and you're
already logged in.

That's the whole tool: parallel browsers, recordings, named macros, persistent
profiles. **Personas** are profiles with metadata (display name, default URL,
credential references); **scenarios** are pre-declared groups of personas you can
spin up with one call. Both are covered later.

## Concepts: how the pieces relate

Four layers, each building on the one below:

**1. Browser.** A single live Playwright browser — one engine (chromium /
firefox / webkit), one window. Identified by an `instance_id`. Every action you
run against it gets appended to a JSONL recording, and a separate
`BrowserContext` gives it its own cookie jar (so seven parallel Discord tabs
never share auth, even when they all run on WebKit).

**2. Profile.** A directory on disk (`~/.config/undef/profiles/<persona>/<kind>/`)
that stores cookies, localStorage, IndexedDB, and service-worker state
between browser runs. When you pass `profile=dante` to `browser_launch`, the
browser uses a **persistent context** pointed at that directory — close the
browser, re-launch tomorrow, and you're still logged in. Profiles are scoped
per engine; dante on WebKit and dante on Firefox are two distinct profile
dirs under the same persona.

**3. Persona.** A *named identity* that owns profiles across one or more
engines, plus metadata: display name, `default_url`, `default_macros` to run
at launch, `credentials` (references to env vars or shell commands —
secrets themselves are never stored on disk), and an `app` dict for
free-form domain metadata. Think of a persona as "dante — my Discord power
user across all three engines", and a profile as one engine-specific piece
of that identity. You launch a persona with `browser_launch persona=dante`;
the resolver (`browser_suggest_for_url`) works out which persona to reuse
when the URL is ambiguous. See the [Personas](#personas--identity-layer-over-engine-profiles)
section for the full `profile.yaml` shape.

**4. Scenario.** A *pre-declared group of personas to launch together*, each
with a `role` (`player`, `monitor`, `spectator`). Declared in
`~/.config/undef/scenarios/<name>.yaml` (or a Python `build()` function for
dynamic rosters). `scenario_start name=discord-raid` launches all seven
participants in parallel, applies shared fixtures (dialog policy, mock
routes), runs each participant's startup macros. You can then broadcast a
macro across all participants (`scenario_run_macro`), role-filter
(`role=player`), or drive a single participant by its `instance_id`. See
the [Scenarios](#scenarios--coordinated-multi-browser-orchestration)
section for the full spec shape.

**When to reach for which.** A single browser for one-shot exploration. A
named profile when you want login state to survive. A persona when that
identity is worth metadata and credential references. A scenario when you
need N coordinated browsers as a single unit.

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
| `persona_list` | List personas with engines, metadata, last-used. |
| `persona_get` | Full profile.yaml for a persona (with credential references). |
| `persona_create` | Scaffold a new persona + stub profile.yaml. |
| `persona_delete` | Delete a persona (all engines + metadata); refuses if live. |
| `persona_credentials_check` | Pre-flight: resolve every credential reference without launching a browser. |
| `migrate_profiles` | One-shot: migrate legacy `profiles/<kind>/<name>/` layout. |
| `scenario_list` | List scenario specs on disk. |
| `scenario_start` | Start a scenario; browsers stay open. |
| `scenario_status` | List live scenarios. |
| `scenario_stop` | Teardown and close. |
| `scenario_run_macro` | Broadcast a macro across scenario participants. |
| `scenario_participants` | List participants of a live scenario (role-filter). |
| `scenario_run_as_test` | Run verify macros; pass/fail + JUnit XML. |

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

## Personas — identity layer over engine profiles

Every browser profile belongs to a **persona**: a named identity with metadata,
credential references, and optional default URL + startup macros. A persona can
have browser profiles for multiple engines (WebKit, Firefox, Chromium); each
engine profile is a child directory.

    ~/.config/undef/profiles/
    ├── dante/
    │   ├── profile.yaml     # persona metadata
    │   ├── webkit/          # dante's WebKit browser state
    │   └── chromium/        # dante's Chromium browser state
    └── tim/
        ├── profile.yaml
        └── webkit/

`profile.yaml` declares display name, default URL + macros, credential
references (read from env vars or shell commands at use time; never stored),
and free-form app metadata:

```yaml
name: dante
display_name: Dante Alighieri
default_url: https://discord.com/app
default_macros: [discord-login]
credentials:
  email_env: DANTE_EMAIL
  password_cmd: "op read op://Personal/dante/password"
app:
  discord_user_id: "1234"
  role: player
```

MCP tools: `persona_list` / `persona_get` / `persona_create` / `persona_delete` /
`persona_credentials_check`.
CLI: `octowright persona list|show|create|delete`.

**Credentials pre-flight.** Before launching a scenario whose startup macros
need logins, call `persona_credentials_check name=dante` to verify every
`*_env` / `*_cmd` reference actually resolves. The report lists each
credential, its source (env var or shell command) and the reference itself,
plus per-field `ok`/`error` — the resolved secret is never included. Use
this to avoid the classic "logged in 6 of 7 windows, then discovered the
env var was unset on #7" failure mode.

Legacy `profiles/<kind>/<name>/` layouts are auto-migrated on first use; or run
`octowright migrate-profiles` to force the migration.

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

### Conditional / branching actions

For sites that ship multiple DOM versions of the same flow, three action types
let one macro cover all of them. Hand-author these by editing the JSON; record
the linear baseline first, then wrap fragile steps:

- **`if_selector`** — predicate on selector presence; runs `then` or `else`.
  ```json
  {"action": "if_selector", "selector": ".cookie-banner", "present": true,
   "then": [{"action": "click", "selector": ".accept-cookies"}]}
  ```
- **`try`** — best-effort sub-sequence that SUPPRESSES errors. Use for
  optional steps like dismissing a one-off banner that may or may not exist.
  ```json
  {"action": "try", "actions": [
      {"action": "click", "selector": "#optional-popup-close"}
  ]}
  ```
- **`try_each`** — branches in order; succeeds on the first whose every
  action completes; raises if all fail. The "v1 OR v2 OR v3" hammer.
  ```json
  {"action": "try_each", "branches": [
      [{"action": "click", "selector": "[aria-label='Close']"}],
      [{"action": "click", "selector": "button.dismiss"}],
      [{"action": "press_key", "key": "Escape"}]
  ]}
  ```

These nest freely — `if_selector` inside `try_each` inside `try` works as you
would expect. See `examples/macros/conditional-discord-modal-dismiss.json` for
a real-world pattern.

## Scenarios — coordinated multi-browser orchestration

A scenario is a named group of browser instances launched together. Spin up N
players + a monitoring window + a main-site window with one call; each
instance is a regular `BrowserSession` you can drive per-participant (via
`instance_id`) using all the normal `browser_*` tools.

Declare scenarios in `~/.config/undef/scenarios/<name>.yaml`:

```yaml
name: discord-raid
description: 7 players + 1 monitor + 1 main-site spectator
participants:
  - persona: dante
    kind: webkit
    role: player
  - persona: ops
    kind: firefox
    role: monitor
    url: https://warp.undef.games/monitor
fixtures:
  mock_routes:
    - pattern: "**/api/time"
      body: '{"now":"2026-04-24T00:00:00Z"}'
  dialog_policy: dismiss
teardown:
  macro: cleanup-session
verify:
  player: assert-in-server
  monitor: assert-monitor-healthy
```

Or as Python for dynamic participant lists — `<name>.py` exposes `def build() -> Scenario`.

Lifecycle:

- `scenario_start <name>` launches all participants in parallel, applies
  fixtures, runs per-participant startup macros. Browsers **stay open**.
- `scenario_run_macro <id> <macro> [role=...]` broadcasts a macro across
  participants (optionally role-filtered). Per-participant results returned.
- Any single participant can still be driven by `instance_id` with the regular
  `browser_*` tools.
- `scenario_stop <id>` runs the teardown macro per participant, closes every
  window, returns a summary.
- `scenario_run_as_test <id>` (or `--test` on the CLI) runs `verify` macros
  and produces JUnit XML.

CLI: `octowright scenario list|start [--test --out <xml>]`; the `start`
command blocks until Ctrl-C, then runs teardown and exits.

## Defaults

Configurable via env vars:

- `OCTOWRIGHT_DEFAULT_URL` — fallback `url` when `browser_launch` omits it. Defaults to `https://warp.undef.games`.
- `OCTOWRIGHT_RECORDINGS` — where JSONL logs land. Defaults to `./recordings/` in this repo.
- `OCTOWRIGHT_PROFILES_DIR` — where persistent profiles live. Defaults to `~/.config/undef/profiles/`.
- `OCTOWRIGHT_VIEWPORT_W` / `OCTOWRIGHT_VIEWPORT_H` — default window size (1280×800).
- `OCTOWRIGHT_HEADLESS` — set to `1` to default to headless mode (default is headed).
- `OCTOWRIGHT_NAV_TIMEOUT_MS` / `OCTOWRIGHT_ACTION_TIMEOUT_MS` — per-navigation / per-action timeouts.
- `OCTOWRIGHT_MACROS_DIR` — where saved macros live. Defaults to `~/.config/undef/macros/`.
- `OCTOWRIGHT_SCENARIOS_DIR` — where scenario specs live. Defaults to `~/.config/undef/scenarios/`.

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
