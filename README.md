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

## Docs Map

- [docs/README.md](docs/README.md): canonical user and contributor documentation index.
- [README.md](README.md): user workflows, setup, CLI usage, and operating guidance.
- [MCP-SHARED-CONTRACT.md](MCP-SHARED-CONTRACT.md): API/wire contract between Python server and frontend.
- [docs/getting-started.md](docs/getting-started.md): install, registration, and first successful run.
- [docs/engines.md](docs/engines.md): engine install/status/reinstall and launch-mode behavior.
- [docs/personas.md](docs/personas.md): persona/profile lifecycle and credential preflight.
- [docs/macros.md](docs/macros.md): macro record/replay, linting, and test execution.
- [docs/scenarios.md](docs/scenarios.md): multi-browser orchestration lifecycle.
- [docs/goldens.md](docs/goldens.md): baseline capture vs verify policy.
- [docs/ci-quality.md](docs/ci-quality.md): quality gates and local CI parity commands.
- [docs/troubleshooting.md](docs/troubleshooting.md): fast diagnosis for common failures.
- [docs/architecture/](docs/architecture/): system diagrams and architecture references.
- [docs/images/README.md](docs/images/README.md): canonical branding/image asset workflow.
- [CHANGELOG.md](CHANGELOG.md): release summaries.

## Install

```bash
cd ~/code/gh/provide-io/octowright
uv sync
uv run playwright install webkit firefox chromium
```

## Register with Claude Code

Add to `.mcp.json` in the repo you want to use it from (or `~/.claude.json` globally) —
replace `<absolute-path-to-octowright>` with the path on YOUR machine
(e.g. `~/code/octowright` expanded to its absolute form):

```json
{
  "mcpServers": {
    "octowright": {
      "command": "uv",
      "args": [
        "--directory",
        "<absolute-path-to-octowright>",
        "run",
        "octowright",
        "serve"
      ]
    }
  }
}
```

Run `octowright init` to print this same block with the path filled in for
your install — and to scaffold a sample persona, scenario, and macro under
`~/.config/undef/`. Reload Claude; tools appear as
`mcp__octowright__browser_launch`, etc.

## Distributed Skill Pack

octowright ships a packaged skill named `using-octowright` for Codex and
project-local plugin manifests for Claude/Codex runtimes.

Install everything:

```bash
uv run octowright skill install using-octowright --target all
```

Check status and drift:

```bash
uv run octowright skill status using-octowright --target all
```

Run diagnostics:

```bash
uv run octowright skill doctor --json
```

Notes:
- Codex skill install target is `$CODEX_HOME/skills` (defaults to `~/.codex/skills`).
- Plugin manifests are written in the current project under `.claude-plugin/plugin.json`
  and `.codex-plugin/plugin.json`.
- Use `--dry-run` to preview writes and `--force` to overwrite existing installs.
- Distributed skill/plugin metadata versions are sourced from `octowright.VERSION`.

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
spin up with one call. Both are covered later. **The dashboard** ties every
piece together visually — see the next section.

## Dashboard

`octowright serve` boots two things in one process: the MCP stdio server (what
Claude talks to) and a Starlette HTTP server on `http://127.0.0.1:8765/` (what
*you* look at). One stable URL, pinned in your browser, replaces the old dance
of copying log paths and shelling out to `npx playwright show-trace` by hand.

Ask Claude `"give me the octowright dashboard URL"` (it'll call the
`octowright_dashboard_url` MCP tool), or just open the URL directly. You get:

- **Top-level dashboard** — every live browser, every live scenario, recent
  closed sessions, all your personas, all your saved macros. Auto-refreshes
  every 5 seconds.
- **Persona management** — each persona card shows engine list, last-used
  time, and on-disk size (chromium + firefox + webkit + yaml). Hover the
  card and click the edit (✎) icon to open an in-page YAML editor; save
  writes back to `<persona>/profile.yaml` via `PUT /api/personas/{name}`.
  Disk sizes are loaded lazily after first paint via
  `GET /api/personas/sizes` (a single `du -sk` over `~/.config/undef/profiles/`).
- **Closed-session cleanup** — closed-session rows expose an `⊗` delete
  button on hover; clicking removes the JSONL recording, video, trace, and
  screenshots from disk via `DELETE /api/sessions/{id}/recording`. Live
  sessions reject the call with 409 (close them first).
- **Per-session debugger** — click any session for a two-column page with the
  embedded session video on the left, action timeline on the right. Click any
  action in the timeline to seek the video to that moment. Tabs underneath
  the timeline switch between **console messages** (filtered by level),
  **downloads** (with a "missing" badge if the file was moved),
  **markdown export**, and **screenshots** (lazy-loaded thumbnail grid).
- **Live updates** — for currently-running sessions, the page opens a
  WebSocket to `/api/sessions/{id}/tail` and appends new events as they
  arrive (no manual refresh). WebSocket frame payloads that are binary are
  intentionally hidden in the UI preview as `[binary payload hidden]`. Full frames
  are still cached to the websocket cache using base64 for safe replay and
  debugging.
- **Trace deep-dive** — a button on each session page spawns
  `npx playwright show-trace` against that session's `.zip` trace, opening
  the official Playwright trace viewer for full per-action inspection
  (network, snapshots, source links). Requires `npx` on PATH.

The markdown tab uses the new `GET /api/sessions/{id}/markdown` endpoint; the
server captures cached markdown on page load and user navigation, and generates
it on demand if a live session hasn't populated the cache yet.

The dashboard is a TypeScript SPA built into `packages/octowright-frontend/`
(strict tsc + Biome + vitest). It uses `@provide-io/telemetry` for structured
logging so frontend log lines are correlated with the Python server's
`provide.telemetry` calls. The compiled bundle ships inside the wheel; the
frontend has zero runtime dependency on Node — Node is only needed at build
time and for the optional `npx playwright show-trace` deep-dive.

If port 8765 is taken, the server walks up to 5 higher ports and picks the
first free one (or logs a warning and continues without the HTTP layer if all
are busy — MCP keeps running). Override the default with `OCTOWRIGHT_HTTP_PORT`
or bind to a different host with `OCTOWRIGHT_HTTP_HOST` (default `127.0.0.1`;
use `0.0.0.0` if you want the dashboard reachable from another machine on
your network).

## Concepts: how the pieces relate

Five layers, each building on the one below:

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

**5. Dashboard.** The web UI bundled with `octowright serve` is the visual
projection of everything above. The dashboard page lists every live browser,
every live scenario, recent closed sessions, every persona, every macro;
each session links to a debugger page with embedded video, click-to-seek
action timeline, console messages, downloads, and screenshots. The Playwright
trace viewer is one button away. See the [Dashboard](#dashboard) section
above for what it shows; this layer doesn't add new state — it just makes
the other four layers observable.

**When to reach for which.** A single browser for one-shot exploration. A
named profile when you want login state to survive. A persona when that
identity is worth metadata and credential references. A scenario when you
need N coordinated browsers as a single unit. The dashboard whenever you
want to *see* what's happening rather than ask Claude.

## Tools

Every mutating tool takes an `instance_id` returned from `browser_launch`. Each call
appends a record to that instance's JSONL log.

| Tool | What |
|---|---|
**Browser lifecycle**

| Tool | What |
|---|---|
| `browser_launch` | Launch a new headed browser. `kind` = `chromium` / `firefox` / `webkit`. Returns `instance_id`. |
| `browser_suggest_for_url` | Pre-launch: which saved persona owns this URL? Disambiguates `"open discord.com"` requests. |
| `browser_list` | List all live instances. |
| `browser_close` / `browser_close_all` | Close one / all. |
| `browser_spawn_roster` | Launch N browsers in parallel from a list of launch specs. |
| `browser_navigate` | Navigate a specific instance. |
| `browser_navigate_back` | Go back one entry in the browser's history. Returns `{ok, url, title}`; `ok=False` when there's no previous page. |
| `browser_open_url` | Open a URL on an existing instance. `target='tab'` (default) appends a new page; `target='window'` calls `window.open(...,'popup',width=W,height=H)` so the OS opens a new window (defaults 1024×768). Returns `{ok, target, page_index, url}`. |
| `browser_resize` | Resize the page viewport to `width × height` CSS pixels (does not resize the OS window). |

**Input**

| Tool | What |
|---|---|
| `browser_click` / `browser_type` / `browser_fill` / `browser_press_key` | CSS-selector input. |
| `browser_click_by` / `browser_fill_by` / `browser_get_text_by` | ARIA-locator input (role / label / text / data-testid). |
| `browser_hover` | Hover the cursor over a CSS selector (triggers `:hover` / hover-reveal menus / tooltips). |
| `browser_select_option` | Select one option in a native `<select>` by `value`, `label`, or 0-based `index`. |
| `browser_drag` | Drag-and-drop from `source_selector` onto `target_selector` (Playwright `drag_and_drop`). |
| `browser_set_input_files` | Upload files into an `<input type=file>`. |

**Inspection**

| Tool | What |
|---|---|
| `browser_screenshot` | PNG to disk. |
| `browser_snapshot` | Accessibility tree. |
| `browser_evaluate` | Run JS in the page. |
| `browser_console_messages` | Collected console output since launch (cursor pagination). |
| `browser_wait_for` | Wait for selector / text / network-idle. |
| `browser_recording_path` | Path to the JSONL action log for this instance. |
| `browser_tail_recording` | Stream new JSONL events appended since a byte cursor — for live monitoring without `tail -f`. |
| `browser_export_script` | Emit a Playwright Python (or TS) script that replays the log. |
| `browser_open_trace` | Open the Playwright trace viewer (`npx playwright show-trace`) on this session's `.zip`. |

**Assertions**

| Tool | What |
|---|---|
| `browser_expect_url` / `browser_expect_text` / `browser_expect_selector` / `browser_expect_js` | Recording-aware assertions (raise on mismatch, append to JSONL). |

**Network & dialogs**

| Tool | What |
|---|---|
| `browser_set_dialog_policy` | accept / dismiss / manual for `confirm()` / `alert()` / `prompt()`. Default: dismiss. |
| `browser_mock_route` / `browser_unmock_route` | Stub network responses for deterministic tests. |
| `browser_network_requests` | List captured HTTP/HTTPS requests for an instance. Optional substring `url` / `method` / `resource_type` filters; pass `since` cursor for incremental polling. |

**Pages, frames, downloads**

| Tool | What |
|---|---|
| `page_list` / `page_switch` / `page_close` | Manage tabs + popups. |
| `browser_switch_frame` / `browser_reset_frame` / `browser_list_frames` | Drive an iframe. |
| `browser_downloads` / `browser_wait_for_download` | Captured downloads (cursor pagination). |

**Profiles, personas, scenarios, macros, goldens**

| Tool | What |
|---|---|
| `profile_list` / `profile_delete` | Saved per-engine profile dirs. |
| `persona_list` / `persona_get` / `persona_create` / `persona_delete` | Identity-layer over profiles. |
| `persona_credentials_check` | Pre-flight: resolve every credential reference without launching a browser. |
| `scenario_list` / `scenario_start` / `scenario_status` / `scenario_stop` / `scenario_run_macro` / `scenario_participants` / `scenario_run_as_test` / `scenario_tail` | Multi-browser orchestration + verify-as-test. |

| `scenario_plan` | Dry-run: show resolved per-participant launch_kwargs without launching anything. |
| `macro_save` / `macro_list` / `macro_run` / `macro_run_sequence` / `macro_delete` | Named, parameterised action sequences. |
| `macro_lint` | Static-analysis pass on a saved macro: missing required fields, unknown actions, unparameterized credential-shaped strings, empty conditional branches. |
| `golden_save` / `golden_assert` / `golden_list` / `golden_delete` | Accessibility-tree snapshot diffs. |
| `run_test_suite` | Run every `[test]`-tagged macro in a directory; emit JUnit XML. |

**Octowright self / housekeeping**

| Tool | What |
|---|---|
| `octowright_dashboard_url` | Returns the localhost dashboard URL (with optional `session_id` deep-link). |
| `octowright_check_takeover` | Detect competing Playwright MCP plugins in `.mcp.json` / `~/.claude.json`; report scope + suggested actions. |
| `recordings_cleanup` | Prune old recording artefacts older than N days. Dry-run by default. |

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

**Window title format.** Every page's `document.title` is rewritten on the fly to
end with ` (<persona-emoji><engine-emoji>) [<profile>]` — so the page's own title
leads and the badge tails. Example: `Yahoo | Mail, Weather, … (🐬🦊) [microdosing]`
in firefox, or `Yahoo | Mail, Weather, … (🐬🧭) [microdosing]` in webkit. The
persona emoji is hash-picked from a curated 33-pick pool keyed off the persona
name (deterministic, same emoji every time); the engine emoji is fixed
(🌐 chromium · 🦊 firefox · 🧭 webkit). When parallel windows pile up in cmd-` /
the Window menu / a tab strip, the suffix lets you tell them apart at a
glance even after deep navigation. Override the emoji by setting `emoji:` in
the persona's `profile.yaml`, or disable the corner badge entirely with
`badge=False` on `browser_launch` (the title injection has no off-switch
short of editing the launch — it's purely a string rewrite, no DOM nodes).

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
- `OCTOWRIGHT_MACROS_DIR` — where saved macros live. Defaults to `~/.config/undef/macros/`.
- `OCTOWRIGHT_SCENARIOS_DIR` — where scenario specs live. Defaults to `~/.config/undef/scenarios/`.
- `OCTOWRIGHT_VIEWPORT_W` / `OCTOWRIGHT_VIEWPORT_H` — default viewport size (1280×800). Used in **headless** mode and whenever `viewport_w` / `viewport_h` are explicitly passed to `browser_launch`. In **headed** mode with neither dimension set, the context launches with `no_viewport=True` so the page tracks the OS window as it's resized; the recorded `viewport` field is `null` in that case.
- `OCTOWRIGHT_HEADLESS` — explicit `0` / `1` overrides headless mode. Default is auto-detected: headed on macOS or Linux+display, headless on CI (`CI=true`) or Linux without `$DISPLAY` / `$WAYLAND_DISPLAY`.
- `OCTOWRIGHT_NAV_TIMEOUT_MS` / `OCTOWRIGHT_ACTION_TIMEOUT_MS` — per-navigation / per-action timeouts.
- `OCTOWRIGHT_HTTP_HOST` / `OCTOWRIGHT_HTTP_PORT` — dashboard HTTP server bind address. Default `127.0.0.1:8765`. Use `0.0.0.0` for the host to make the dashboard reachable from another machine on your network. If the port is in use, the server walks up 5 higher ports automatically.

## CLI commands

`octowright` itself is a click-based CLI; subcommands let you do the
common housekeeping without going through Claude:

| Command | What |
|---|---|
| `octowright serve` | Run the MCP stdio server + the dashboard HTTP server. This is the default when you invoke `octowright` with no subcommand. |
| `octowright init [--force]` | First-run scaffolding: create the standard dirs (`~/.config/undef/{profiles,macros,scenarios}/`), drop a sample persona / scenario / macro, and print the `.mcp.json` registration block with your install path filled in. |
| `octowright selftest` | Print the list of registered MCP tools without needing a live MCP client. Sanity check after install. |
| `octowright test [<dir>] [--kind <engine>] [--tag <tag>] [--out <xml>]` | Run every `[test]`-tagged macro in a directory, emit JUnit XML. |
| `octowright cleanup [--days N] [--apply]` | Prune old recording artefacts (JSONL logs, screenshots, videos, traces). Dry-run by default; `--apply` actually deletes. |
| `octowright takeover [--apply --scope=session\|project\|global --name=<n>]` | Detect competing Playwright MCP plugins in `.mcp.json` / `~/.claude.json` and offer to disable them in favour of octowright. Default is read-only report; `--apply` rewrites the config (with timestamped backup) by renaming the matched key to `_<name>_disabled_by_octowright`. Reversible — rename back to re-enable. |
| `octowright persona list\|show\|create\|delete` | Manage personas from the terminal. |
| `octowright scenario list\|start [--test --out <xml>] [--watch]` | Start a scenario; `--watch` streams participant events to stdout in real-time; the command blocks until Ctrl-C. |

## Telemetry

Both halves of octowright use the `provide.telemetry` family for structured
logging:

- **Python server** uses `provide-telemetry>=0.3` (structlog under the hood).
  `setup_telemetry()` is called by `octowright serve`; every module gets a
  logger via `get_logger(__name__)`. Logs land on stderr in development,
  JSON in production (auto-detected).
- **TypeScript dashboard** uses `@provide-io/telemetry@^0.3.0` (pino under
  the hood). `setupTelemetry()` runs at the top of each entrypoint;
  `getLogger('octowright.frontend.{api,tail,dashboard,session,global}')` per
  module. Logger names mirror the Python convention so log lines are easy
  to correlate across the stack.

### Configure log level and format

Use `PROVIDE_LOG_LEVEL` and `PROVIDE_LOG_FORMAT` to control verbosity and
renderer:

```bash
# Human-readable local debugging
export PROVIDE_LOG_LEVEL=DEBUG
export PROVIDE_LOG_FORMAT=pretty
uv run octowright serve
```

```bash
# Machine-friendly production logs
export PROVIDE_LOG_LEVEL=INFO
export PROVIDE_LOG_FORMAT=json
uv run octowright serve
```

`octowright serve --log-level DEBUG` is a convenience wrapper that sets
`PROVIDE_LOG_LEVEL` for the process and spawned daemon.

### Configure OTLP export (telemetry traces/metrics/logs)

Telemetry export is opt-in. To send OpenTelemetry signals to an OTLP collector:

```bash
export PROVIDE_TRACE_ENABLED=1
export PROVIDE_METRICS_ENABLED=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
# optional auth/tenant headers
export OTEL_EXPORTER_OTLP_HEADERS="authorization=Bearer%20TOKEN,x-tenant-id=dev"
uv run octowright serve
```

Signals are no-op if telemetry exporters are not configured/available.

### Playwright traces vs telemetry traces

- **Playwright trace**: per-session browser artifact (`*.trace.zip`) produced
  by Playwright when session tracing is enabled; inspect with
  `npx playwright show-trace`.
- **Telemetry trace**: OpenTelemetry spans emitted by `provide.telemetry`
  (when `PROVIDE_TRACE_ENABLED=1`) and exported to OTLP.

These are separate systems and can be enabled independently.

### Metrics endpoint

octowright exposes a lightweight Prometheus-text endpoint at:

- `GET /api/metrics`

It includes request totals, per-status counts, per-route counts, and per-route
duration sum/count for the debugger/API server process. Disable it with:

```bash
export OCTOWRIGHT_HTTP_METRICS=0
```

This endpoint is process-local and complements (not replaces) OTLP export.

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
