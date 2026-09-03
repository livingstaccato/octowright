![octowright](https://raw.githubusercontent.com/livingstaccato/octowright/main/docs/images/brand/octowright-banner.png)

# Octowright

An MCP server that lets agentic coding clients drive **many headed Playwright browsers
in parallel** with a **mix of engines** (Chromium, Firefox, WebKit), recording every
action to a JSONL log so a session can later be exported as a standalone Playwright
script.

Octowright is optimized for multi-session, mixed-engine browser orchestration with
persistent profiles, structured recordings, and a local debugger dashboard.

## Get started

Octowright is on PyPI (`uv tool install octowright`, or `uvx octowright serve`
to run it without installing), but this guide walks the
from-source path: it is what the MCP registration block below assumes, and
the optional terminal plugin is only installable from the checkout. Octowright
uses [`uv`](https://docs.astral.sh/uv/) for dependency management. If you
don't have `uv` yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, from any directory you'd like Octowright to live under (e.g. `~/code/`):

```bash
git clone https://github.com/livingstaccato/octowright.git
cd octowright
uv sync                                              # install Python deps
uv run playwright install webkit firefox chromium    # install browser binaries
uv run octowright init                               # print MCP registration block + scaffold config
```

Engine-binary management is currently CLI-driven (`playwright install` /
`playwright install --list`), not exposed as Octowright MCP tools.

The last command prints a JSON block to paste into an MCP client config, commonly
`.mcp.json` for a project or `~/.claude.json` for Claude Code. It also creates
Octowright's user config directory with a sample persona, scenario, and macro so
you have something to play with.

The block it prints looks like this — `init` substitutes your install path
into `<absolute-path-to-octowright>`:

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

Reload your MCP client. The tools appear as `mcp__octowright__browser_launch`, etc.

Verify in 30 seconds: ask your client to launch a webkit browser at `octowright.com`,
click a link, list browsers, then close. The next section walks through that same
flow as a tour of what Octowright actually does.

## Your first 5 minutes

Once installed and registered, ask your MCP client to walk through these in order.
Each step builds on the previous one and shows you what Octowright actually does.

**1. Open a browser.** Ask: *"launch a webkit browser at octowright.com"*. The client
calls `browser_launch kind=webkit url=https://octowright.com`. A real WebKit window opens
on your desktop. The result includes the `instance_id` so the client can target later
actions.

**2. Drive it.** Ask: *"click the 'More information' link"*. The client calls
`browser_click text="More information"` (an ARIA locator — no CSS selector needed).
The window navigates. Every action lands in a JSONL recording on disk.

**3. List what's open.** Ask: *"what browsers are running?"*. `browser_list` returns a
one-line summary like `1 browser: 8a3f.../webkit @ iana.org/help/example-domains`.

**4. Save the session as a macro.** Ask: *"save the last few clicks as a macro called
example-tour"*. The client calls `macro_save`. Now `macro_run name=example-tour`
replays it.

**5. Close the browser, then launch a *named* one.** Ask: *"close that browser, then
launch a chromium browser with profile=demo at github.com"*. The window opens, you log
in (or whatever). When you close it, the cookies/localStorage flush to
the profile directory in Octowright's config dir. Re-launch with the same profile and you're
already logged in.

That's the whole tool: parallel browsers, recordings, named macros, persistent
profiles. **Personas** are profiles with metadata (display name, default URL,
credential references); **scenarios** are pre-declared groups of personas you can
spin up with one call. Both are covered later. **The dashboard** ties every
piece together visually — see the next section.

## Demo catalog

Octowright now ships a curated demo catalog on top of the raw `examples/`
material.

- Repo-facing catalog: [`demo/INDEX.md`](demo/INDEX.md)
- Authored bundle manifests: `demo/bundles/<demo-id>/demo.yaml`

The current hero set promotes seven offline-first bundles:
`first-run-session`, `macro-replay-loop`, `cross-engine-trio`,
`role-based-duo`, `fixture-lab`, `verify-suite`, and
`seven-mix-orchestration`.

`examples/` remains the raw source layer for reusable macros and scenarios.
`demo/bundles/` is the product-facing layer that adds audience/tag metadata,
artifact expectations, regen commands, tutorial-export metadata, and small
deterministic seed assets.

To refresh the generated repo catalog and per-bundle tutorial-export JSON from the manifests:

```bash
uv run python scripts/demos/record_heroes.py
```

## Distributed Skill Pack

Octowright ships a packaged skill named `octowright` for Codex and
project-local plugin manifests for compatible runtimes such as Claude Code and Codex.

Install everything:

```bash
uv run octowright skill install octowright --target all
```

Check status and drift:

```bash
uv run octowright skill status octowright --target all
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

## Dashboard

`octowright serve` boots two things in one process: the MCP stdio server (what
your client talks to) and a Starlette HTTP server on `http://127.0.0.1:6286/` (what
*you* look at) — every live browser and scenario, closed-session cleanup, a
per-session debugger (video, action timeline, console/downloads/screenshots),
live WebSocket updates, and a Playwright trace-viewer deep-dive.

Ask your MCP client `"give me the octowright dashboard URL"` (it'll call the
`octowright_dashboard_url` MCP tool), or just open the URL directly.

Full reference: [docs/dashboard.md](docs/dashboard.md).

## Concepts: how the pieces relate

Five layers, each building on the one below:

**1. Browser.** A single live Playwright browser — one engine (chromium /
firefox / webkit), one window. Identified by an `instance_id`. Every action you
run against it gets appended to a JSONL recording, and a separate
`BrowserContext` gives it its own cookie jar (so seven parallel Discord tabs
never share auth, even when they all run on WebKit).

**2. Profile.** A directory on disk (`<octowright-config>/profiles/<persona>/<kind>/`)
that stores cookies, localStorage, IndexedDB, and service-worker state
between browser runs. When you pass `profile=dante` to `browser_launch`, the
browser uses a **persistent context** pointed at that directory — close the
browser, re-launch tomorrow, and you're still logged in. Profiles are scoped
per engine; Dante on WebKit and Dante on Firefox are two distinct profile
dirs under the same persona.

**3. Persona.** A *named identity* that owns profiles across one or more
engines, plus metadata: display name, `default_url` (which is also the
context's Playwright `base_url`, so a macro can navigate `/orders` and stay
portable across deployments), `default_macros` to run at launch,
`credentials` (references to env vars or shell commands — secrets themselves
are never stored on disk), and an `app` dict for
domain metadata. Think of a persona as "Dante — my Discord power
user across all three engines", and a profile as one engine-specific piece
of that identity. You launch it with `browser_launch profile=dante`;
the resolver (`browser_suggest_for_url`) works out which persona to reuse
when the URL is ambiguous. See [docs/personas.md](https://github.com/livingstaccato/octowright/blob/main/docs/personas.md)
for the full `profile.yaml` shape.

**4. Scenario.** A *pre-declared group of personas to launch together*, each
with a `role` (`player`, `monitor`, `spectator`). Declared in
`<octowright-config>/scenarios/<name>.yaml` (or a Python `build()` function for
dynamic rosters). `scenario_start name=discord-raid` launches all seven
participants in parallel, applies shared fixtures (dialog policy, mock
routes), runs each participant's startup macros. You can then broadcast a
macro across all participants (`scenario_run_macro`), role-filter
(`role=player`), or drive a single participant by its `instance_id`. See
[docs/scenarios.md](https://github.com/livingstaccato/octowright/blob/main/docs/scenarios.md) for the full spec shape.

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
want to *see* what's happening rather than ask your MCP client.

**Operation ordering.** Every browser session serializes its own operations
FIFO — a second tool call, a background capture, or a queued macro action
against the same `instance_id` waits its turn instead of racing the one in
flight, and one macro (or macro sequence, or artifact replay) holds its
browser for the whole run so a manual action can't land mid-sequence.
Different sessions never wait on each other. A queued operation gives up
after `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` (default 300s, see
[Configuration](#configuration)) and fails with a plain tool error — it never
takes down the MCP connection or another browser. Closing a session drains
whatever is already queued, then rejects anything new; if the browser or page
closes out from under an in-flight call instead, that call still fails
cleanly and any still-queued work is released with a session-closed error.
Embedders driving `BrowserSession` directly: `list_pages()`, `list_frames()`,
and `set_dialog_policy()` are `async` now and must be awaited, and a session
should be torn down through `BrowserPool.close()` rather than closing the
underlying Playwright objects directly so the drain/reject behavior above
still applies.

## Tools

Every mutating tool takes an `instance_id` returned from `browser_launch`. Each call
appends a record to that instance's JSONL log.

### Browser lifecycle

| Tool | What |
|---|---|
| `browser_launch` | Launch a new headed browser. `kind` = `chromium` / `firefox` / `webkit`. Optional `channel` (e.g. `chrome`, `msedge`), `executable_path` (custom binary), `launch_args` (extra CLI flags) — launch-time only, never persisted to recordings or replayed on handoff. Returns `instance_id`. |
| `browser_suggest_for_url` | Pre-launch: which saved persona owns this URL? Disambiguates `"open discord.com"` requests. |
| `browser_list` | List all live instances. |
| `browser_close` / `browser_close_all` | Close one / all. Protected browsers require `force=True`; `browser_close_all` skips protected browsers unless forced and reports failures. `browser_close_all` also takes `exclude_labels` / `exclude_profiles` to spare matching sessions. |
| `browser_spawn_roster` | Launch N browsers in parallel from a list of launch specs. |
| `browser_navigate` | Navigate a specific instance. |
| `browser_navigate_back` | Go back one entry in the browser's history. Returns `{ok, url, title}`; `ok=False` when there's no previous page. |
| `browser_open_url` | Open a URL on an existing instance. `target='tab'` (default) appends a new page; `target='window'` calls `window.open(...,'popup',width=W,height=H)` so the OS opens a new window (defaults 1024×768). Returns `{ok, target, page_index, url}`. |
| `browser_resize` | Resize the page viewport to `width × height` CSS pixels (does not resize the OS window). |

### Input

| Tool | What |
|---|---|
| `browser_click` / `browser_type` / `browser_fill` / `browser_press_key` | CSS-selector input. `browser_click` also accepts ARIA locators (`role` / `label` / `text` / `test_id`) instead of `selector`; `browser_fill` accepts `role` / `label` / `test_id` (there is no `text` — a fill targets one field, it does not search page text). `role_exact` / `label_exact` / `text_exact` switch that finder from substring to whole-string **and case-sensitive** matching. |
| `browser_get_text_by` | ARIA-locator text read (role / label / text / data-testid) — no CSS-selector equivalent. |
| `browser_hover` | Hover the cursor over a CSS selector (triggers `:hover` / hover-reveal menus / tooltips). |
| `browser_select_option` | Select one option in a native `<select>` by `value`, `label`, or 0-based `index`. |
| `browser_drag` | Drag-and-drop from `source_selector` onto `target_selector` (Playwright `drag_and_drop`). |
| `browser_set_input_files` | Upload files into an `<input type=file>`. |

Recorded CSS `click` and `fill` actions also capture semantic metadata when
Playwright can resolve it. Macro playback and exported replay scripts try that
ARIA locator first, then fall back to the original CSS selector.

### Inspection

| Tool | What |
|---|---|
| `browser_screenshot` | PNG to disk. |
| `browser_snapshot` | Accessibility tree (defaults to `body`). |
| `browser_read_markdown` | Cached Markdown representation (highly token-efficient for reading). |
| `browser_evaluate` | Run JS in the page. |
| `browser_console_messages` | Collected console output since launch (cursor pagination). |
| `browser_wait_for` | Wait for selector / text / network-idle. |
| `browser_recording_path` | Path to the JSONL action log for this instance. |
| `browser_artifact_manifest` | One-call artifact manifest (log/video/trace/har paths) for a session, live or already closed. |
| `browser_tail_recording` | Stream new JSONL events appended since a byte cursor — for live monitoring without `tail -f`. |
| `browser_export_script` | Emit a Playwright Python (or TS) script that replays the log. |
| `browser_open_trace` | Open the Playwright trace viewer (`npx playwright show-trace`) on this session's `.zip`. |

### Assertions

| Tool | What |
|---|---|
| `browser_expect_url` / `browser_expect_text` / `browser_expect_selector` / `browser_expect_js` | Recording-aware assertions (raise on mismatch, append to JSONL). |

### Network & dialogs

| Tool | What |
|---|---|
| `browser_set_dialog_policy` | accept / dismiss / manual for `confirm()` / `alert()` / `prompt()`. Default: dismiss. |
| `browser_mock_route` / `browser_unmock_route` | Stub network responses for deterministic tests. |
| `browser_launch(extra_http_headers=…)` | Headers on **every** request the browser makes — pages, popups, subresources. The one to reach for first. |
| `browser_set_extra_http_headers` | Headers for **this page**, overriding the launch ones. For a token the run only learns partway through (log in, then carry it). Per page: a popup opened later does not inherit it. |
| `browser_inject_headers` / `browser_uninject_headers` | Headers for requests matching a **URL pattern** only. Costs a route interception per matching request; note a later `browser_mock_route` on an overlapping pattern silently suppresses it. |
| `browser_network_requests` | List captured HTTP/HTTPS requests for an instance. Optional substring `url` / `method` / `resource_type` filters; pass `since` cursor for incremental polling. |

### Pages, frames, downloads

| Tool | What |
|---|---|
| `page_list` / `page_switch` / `page_close` | Manage tabs + popups. |
| `browser_switch_frame` / `browser_reset_frame` / `browser_list_frames` | Drive an iframe. |
| `browser_downloads` / `browser_wait_for_download` | Captured downloads (cursor pagination). |

### Profiles, personas, scenarios, macros, goldens

| Tool | What |
|---|---|
| `profile_list` / `profile_delete` | Saved per-engine profile dirs. |
| `persona_list` / `persona_get` / `persona_create` / `persona_delete` | Identity-layer over profiles. |
| `persona_credentials_check` | Pre-flight: resolve every credential reference without launching a browser. |
| `scenario_list` / `scenario_start` / `scenario_status` / `scenario_stop` / `scenario_run_macro` / `scenario_participants` / `scenario_run_as_test` / `scenario_tail` | Multi-browser orchestration + verify-as-test. |
| `scenario_plan` | Dry-run: show resolved per-participant launch_kwargs without launching anything. |
| `macro_save` / `macro_list` / `macro_run` / `macro_run_sequence` / `macro_delete` | Named, parameterised action sequences. Supports `macro_call` for reusable submacros. |
| `macro_compile` | Compile the YAML macro DSL to canonical JSON; dry-run by default, save with `write=true`. |
| `macro_lint` | Static-analysis pass on a saved macro: missing required fields, unknown actions, unparameterized credential-shaped strings, empty conditional branches. |
| `golden_save` / `golden_assert` / `golden_list` / `golden_delete` | Accessibility-tree snapshot diffs. |
| `run_test_suite` | Run every `[test]`-tagged macro in a directory; emit JUnit XML. |

### Housekeeping

| Tool | What |
|---|---|
| `octowright_dashboard_url` | Returns the localhost dashboard URL (with optional `session_id` deep-link). |
| `octowright_check_takeover` | Detect competing Playwright MCP plugins in `.mcp.json` / `~/.claude.json`; report scope + suggested actions. |
| `recordings_cleanup` | Prune old recording artefacts older than N days. Dry-run by default. |

## Persistent profiles, personas, macros, scenarios

Quick orientation — each links to its full reference:

- **Persistent profiles.** Pass `profile=<name>` to `browser_launch` and
  cookies/localStorage/IndexedDB survive close/relaunch (`(kind, profile)` is
  the identity; each live browser still has its own isolated `BrowserContext`).
  Window titles get a `(<persona-emoji><engine-emoji>) [<profile>]` badge so
  parallel windows are distinguishable, and protected sessions refuse a close
  without `force=True`. Full reference: [docs/personas.md](https://github.com/livingstaccato/octowright/blob/main/docs/personas.md#window-title-and-corner-badge).
- **Personas.** A named identity that owns profiles across engines, plus
  display name, `default_url`, startup macros, and credential references
  (never secrets themselves — `persona_credentials_check` pre-flights them).
  Full reference: [docs/personas.md](https://github.com/livingstaccato/octowright/blob/main/docs/personas.md).
- **Macros.** Record a flow once (`macro_save`), replay it with different
  args later (`macro_run`), across any persona/profile. `if_selector` /
  `try` / `try_each` cover multi-DOM-version flows. Full reference: [docs/macros.md](https://github.com/livingstaccato/octowright/blob/main/docs/macros.md).
- **Scenarios.** A named, declared group of personas launched together with
  roles, fixtures, and verify macros — `scenario_start` brings up N
  participants in parallel; `scenario_run_macro` broadcasts across them.
  Full reference: [docs/scenarios.md](https://github.com/livingstaccato/octowright/blob/main/docs/scenarios.md).

## Configuration

All defaults live in `src/octowright/defaults.py` and can be overridden via environment
variables:

On POSIX systems, Octowright follows the XDG Base Directory split:

- Config: `${XDG_CONFIG_HOME:-~/.config}/octowright/` for durable user-authored data.
- State: `${XDG_STATE_HOME:-~/.local/state}/octowright/` for session history, logs, and manifests.
- Cache: `${XDG_CACHE_HOME:-~/.cache}/octowright/` for rebuildable analysis captures.

On Windows, config uses `%APPDATA%\octowright\`, while state and cache use
`%LOCALAPPDATA%\octowright\State\` and `%LOCALAPPDATA%\octowright\Cache\`.

| Variable | Default | Description |
|---|---|---|
| `OCTOWRIGHT_DEFAULT_URL` | `https://octowright.com` | Fallback `url` when `browser_launch` omits it. |
| `OCTOWRIGHT_RECORDINGS` | POSIX: `${XDG_STATE_HOME:-~/.local/state}/octowright/sessions/`; Windows: `%LOCALAPPDATA%\octowright\State\sessions\` | Where session artifacts land: JSONL action logs, traces, screenshots, videos, downloads, and markdown captures. |
| `OCTOWRIGHT_CAPTURES_DIR` | POSIX: `${XDG_CACHE_HOME:-~/.cache}/octowright/captures/`; Windows: `%LOCALAPPDATA%\octowright\Cache\captures\` | Where large cached analysis payloads live. |
| `OCTOWRIGHT_CAPTURE_MAX_TOTAL_BYTES` | `52428800` | Size cap for cached analysis captures before oldest captures are pruned. |
| `OCTOWRIGHT_CAPTURE_TTL_SECONDS` | `604800` | Age cap for cached analysis captures. |
| `OCTOWRIGHT_SESSION_MANIFEST` | POSIX: `${XDG_STATE_HOME:-~/.local/state}/octowright/session-manifest.json`; Windows: `%LOCALAPPDATA%\octowright\State\session-manifest.json` | Live-session manifest used for crash recovery/status. |
| `OCTOWRIGHT_PROFILES_DIR` | POSIX: `${XDG_CONFIG_HOME:-~/.config}/octowright/profiles/`; Windows: `%APPDATA%\octowright\profiles\` | Where persistent profiles live. |
| `OCTOWRIGHT_MACROS_DIR` | POSIX: `${XDG_CONFIG_HOME:-~/.config}/octowright/macros/`; Windows: `%APPDATA%\octowright\macros\` | Where saved macros live. |
| `OCTOWRIGHT_SCENARIOS_DIR` | POSIX: `${XDG_CONFIG_HOME:-~/.config}/octowright/scenarios/`; Windows: `%APPDATA%\octowright\scenarios\` | Where scenario specs live. |
| `OCTOWRIGHT_VIEWPORT_W` / `OCTOWRIGHT_VIEWPORT_H` | `1280` / `800` | Default viewport. Used in headless mode and when dimensions are explicitly passed to `browser_launch`. In headed mode with neither set, context launches with `no_viewport=True` so the page tracks the OS window. |
| `OCTOWRIGHT_HEADLESS` | auto | Explicit `0` / `1` overrides headless mode. Auto-detected: headed on macOS or Linux+display, headless on CI (`CI=true`) or Linux without `$DISPLAY` / `$WAYLAND_DISPLAY`. |
| `OCTOWRIGHT_DISABLE_GPU` | unset (off) | Launch **Chromium** with `--disable-gpu --disable-gpu-compositing`. An escape hatch for a headed-Chromium crash seen on Chrome 148 / macOS 26 (a main-process abort reached through native macOS UI and the Metal GPU path) — **not a confirmed fix**, but something to try in one argument if your browsers are crashing. Per-launch override with `browser_launch disable_gpu=true`, which wins over this variable either way. Chromium-only. WebGL still works via software (SwiftShader) rather than disappearing. |
| `OCTOWRIGHT_NAV_TIMEOUT_MS` / `OCTOWRIGHT_ACTION_TIMEOUT_MS` | — | Per-navigation / per-action timeouts. |
| `OCTOWRIGHT_HTTP_HOST` / `OCTOWRIGHT_HTTP_PORT` | `127.0.0.1` / `6286` | Dashboard bind address. Binding to `0.0.0.0` makes the HTTP sidecar reachable on your network, but sensitive dashboard/API/MCP routes stay blocked unless `OCTOWRIGHT_ALLOW_REMOTE_DASHBOARD=1` is also set. Only enable remote dashboard access on trusted networks because it exposes live browser state and local artifacts. If the port is in use, the server walks up 5 higher ports automatically. |
| `OCTOWRIGHT_LIVE_SCREENCAST_FPS` | `10` | Positive integer cap for backend live-preview stream FPS and requested frontend `fps`. |
| `OCTOWRIGHT_LIVE_SCREENCAST_QUALITY` | `70` | JPEG quality for live-preview frames, clamped to `1..100`. |
| `OCTOWRIGHT_LIVE_SCREENCAST_FULLSCREEN_MODE` | `native` | Live-preview fullscreen behavior: `native` browser fullscreen or `panel` in-page fullscreen. |
| `OCTOWRIGHT_IDLE_GRACE` | unset (off) | Seconds before auto-exit when the browser pool is empty. Off by default — the daemon stays up until an explicit `octowright restart`. Set a positive number to opt in; `--keep-alive` force-disables it. |
| `OCTOWRIGHT_OPERATION_QUEUE_TIMEOUT_SECONDS` | `300` | How long a queued operation waits its turn on a busy browser session before failing with a tool error (see [Operation ordering](#concepts-how-the-pieces-relate)). Separate from any Playwright action/navigation timeout. Must be positive, finite seconds. Embedders can override per-`BrowserPool` via `operation_queue_timeout_seconds=`, which takes precedence over this variable. |
| `OCTOWRIGHT_DASHBOARD_OPERATION_TIMEOUT_SECONDS` | `8` | Much shorter budget the dashboard's own read-only session views (live screenshot, aria snapshot, selector validate) wait on a busy session's gate before failing fast, independent of the MCP tool timeout above. |

## CLI

`octowright` is a Click-based CLI; subcommands let you do common housekeeping
without going through an MCP client:

| Command | What |
|---|---|
| `octowright serve [--profile=<spec>]` | Run the MCP stdio server + the dashboard HTTP server. This is the default when you invoke `octowright` with no subcommand. Pass `--profile=core` (or `core,macros` etc.) to slim the LLM-visible MCP tool surface — see [Capability profiles](#capability-profiles) below. |
| `octowright init [--force]` | First-run scaffolding: create the standard config dirs, drop a sample persona / scenario / macro, and print the `.mcp.json` registration block with your install path filled in. |
| `octowright selftest` | Print the list of registered MCP tools without needing a live MCP client. Sanity check after install. |
| `octowright test [<dir>] [--kind <engine>] [--tag <tag>] [--out <xml>]` | Run every `[test]`-tagged macro in a directory, emit JUnit XML. |
| `octowright cleanup [--days N] [--apply]` | Prune old recording artefacts (JSONL logs, screenshots, videos, traces). Dry-run by default; `--apply` actually deletes. |
| `octowright takeover [--apply --scope=session\|project\|global --name=<n>]` | Detect competing Playwright MCP plugins in `.mcp.json` / `~/.claude.json` and offer to disable them in favour of octowright. Default is read-only report; `--apply` rewrites the config (with timestamped backup). Reversible — rename back to re-enable. |
| `octowright persona list\|show\|create\|delete` | Manage personas from the terminal. |
| `octowright scenario list\|start [--test --out <xml>] [--watch]` | Start a scenario; `--watch` streams participant events to stdout in real-time; the command blocks until Ctrl-C. |
| `octowright restart [--keep-browsers] [--kill-followers]` | Stop the running daemon, sweep orphans, start a fresh one. `--kill-followers` also severs connected MCP client transports for a full reset. |
| `octowright dashboard [--open]` | Mint a single-use dashboard pairing code and print the `/pair` URL. Needed by default — pairing is **on** unless `OCTOWRIGHT_DASHBOARD_REQUIRE_PAIRING` is set to a falsey token. |
| `octowright skill install\|status\|doctor` | Install/inspect the packaged skill and plugin manifests. |

## Capability profiles

The full MCP tool surface is currently 131 tools on a core install — every workflow Octowright supports
(browser driving, macros, scenarios, persona management, etc.) shows up in
the LLM's tool schema by default. When the LLM only needs a slice, set
`OCTOWRIGHT_PROFILE` (or pass `--profile` to `octowright serve`) to one or
more comma-separated profile names. Tools not listed in any active profile
are skipped at registration time, so the LLM-visible schema shrinks. Seven
meta/Advisor tools are always registered so agents can inspect Octowright,
find the dashboard, and surface local guidance even under narrow profiles.

| Profile | What | Tool count |
|---|---|---|
| `core` | Minimum to drive a browser end-to-end, including compact DOM and HTTP-first discovery. | 24 |
| `advanced` | Inspection, cached captures, summaries, assertions, viewport controls, and ARIA-locator interactions for stable test automation. | 31 |
| `macros` | Macro record / list / run / lint / repair / compile + artifact bundles. | 15 |
| `scenarios` | Scenario orchestration (multi-browser test setups). | 12 |
| `personas` | Persona + on-disk profile management. | 8 |
| `goldens` | Accessibility-tree snapshot baselines + diff. | 5 |
| `terminals` | Terminal sessions, declared by the `octowright-terminal` session-kind plugin (**experimental**; `pip install octowright-terminal`, see AGENTS.md). Only present when the plugin is enabled via `OCTOWRIGHT_PLUGINS=terminal`. | 7 |
| always-on | Status, storage report, dashboard, takeover detection, and Advisor tools registered under every profile. | 7 |
| `all` (or unset) | Default — every core-install tool registers. | 131 |

```bash
octowright serve --profile=core              # 31 tools — core + always-on
octowright serve --profile=core,macros       # 46 tools — browser + macro pipeline + always-on
octowright serve --profile=core,scenarios    # browser + multi-browser orchestration
```

The active profile shows up in `octowright selftest` and in the
`octowright_status` MCP tool's `profile` block. If a tool you expected is
missing, that's where to look. The dict lives in
`src/octowright/server/profiles.py` — extend it to add or rebalance groups.

## Octowright Advisor

Octowright Advisor is a local, deterministic guidance layer exposed through
always-on MCP tools. `octowright_status` includes an `advisor` block, and
`octowright_advisor_status` returns the same Advisor snapshot directly:
preferences, recent usage summary, and current suggestions. (`octowright_status`
also carries an `upgrade` block on the first run after a version change — present
its highlights to the user as a "what's new" note.)

Advisor currently suggests two things:

- **Macro candidates**: agents call
  `octowright_advisor_record_macro_observation` when they notice repeated
  workflows. Two observations with the same signature produce a
  `macro_candidate` suggestion. Advisor never auto-saves a macro.
- **Profile changes**: recent MCP tool usage can suggest narrowing or expanding
  `OCTOWRIGHT_PROFILE`. Profile-change suggestions can be prompt-only or marked
  `auto_apply` when the `profile_change` preference is `automatic`.

Agents should check Advisor status after first-touch status and before asking a
user whether repeated work should become a macro. Preferences are persisted in
the local Advisor state file and can be changed with
`octowright_advisor_set_preference`. Set `OCTOWRIGHT_ADVISOR_STATE` to isolate
that JSON state file for tests or separate deployments.

## Telemetry

Both halves of Octowright (Python server, TypeScript dashboard) use the
`provide.telemetry` family for structured logging, with opt-in OpenTelemetry
trace/metric export to any OTLP collector:

```bash
export PROVIDE_TRACE_ENABLED=1
export PROVIDE_METRICS_ENABLED=1
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
uv run octowright serve
```

Full reference: [docs/telemetry.md](docs/telemetry.md).

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

## Documentation

- [docs/README.md](https://github.com/livingstaccato/octowright/blob/main/docs/README.md): full documentation index.
- [docs/getting-started.md](https://github.com/livingstaccato/octowright/blob/main/docs/getting-started.md): install, registration, and first successful run.
- [docs/engines.md](https://github.com/livingstaccato/octowright/blob/main/docs/engines.md): engine install/status/reinstall and launch-mode behavior.
- [docs/personas.md](https://github.com/livingstaccato/octowright/blob/main/docs/personas.md): persona/profile lifecycle and credential preflight.
- [docs/macros.md](https://github.com/livingstaccato/octowright/blob/main/docs/macros.md): macro record/replay, linting, and test execution.
- [docs/scenarios.md](https://github.com/livingstaccato/octowright/blob/main/docs/scenarios.md): multi-browser orchestration lifecycle.
- [docs/goldens.md](https://github.com/livingstaccato/octowright/blob/main/docs/goldens.md): baseline capture vs verify policy.
- [docs/dashboard.md](docs/dashboard.md): web UI, per-session debugger, trace deep-dive.
- [docs/telemetry.md](docs/telemetry.md): structured logging, OTLP export, HTTP metrics.
- [docs/ci-quality.md](https://github.com/livingstaccato/octowright/blob/main/docs/ci-quality.md): quality gates and local CI parity commands.
- [docs/troubleshooting.md](https://github.com/livingstaccato/octowright/blob/main/docs/troubleshooting.md): fast diagnosis for common failures.
- [docs/architecture/](https://github.com/livingstaccato/octowright/tree/main/docs/architecture/): system diagrams and architecture references.
- [CHANGELOG.md](https://github.com/livingstaccato/octowright/blob/main/CHANGELOG.md): release summaries.
