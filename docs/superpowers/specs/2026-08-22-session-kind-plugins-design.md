# Session-Kind Plugins — Design Spec

**Date:** 2026-08-22
**Status:** Approved in brainstorming; pending written-spec review
**Topic:** Replace the built-in terminal subsystem with a general session-kind plugin API, and move terminal out of the repository as the first plugin.

## 1. Summary

Octowright supports exactly two session kinds: browsers, and terminals behind the optional
`octowright[terminal]` extra. Terminal support is quarantined at the *import* level — core never
imports uterm — but the *concept* of a non-browser session is not quarantined at all. It is spread
across 13 core files and 187 lines, most heavily in the scenario layer.

This design removes terminal from core entirely and replaces it with a session-kind plugin API that a
third party can target. Terminal is rebuilt as the first plugin in its own repository. A deliberately
partial reference plugin lives in `tests/` so every seam of the API has a consumer inside core CI
without core depending on uterm.

The goal is **not** line-count reduction — the net saving is modest (roughly 500 Python and 130
TypeScript lines). The goal is **dependency inversion**: after this, a change to scenarios, session
detail, closed-session discovery, or close semantics reasons about a registry instead of about a
second hard-coded session kind, and `provide-uterm`'s release schedule stops gating octowright's.

There is no migration path and no compatibility shim. This spec describes the end state.

## 2. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Motivation | Cut core maintenance **and** enable third-party session kinds. Not primarily about unblocking the terminal install. |
| 2 | Integration depth | **Full parity.** A plugin can own a session pool, register MCP tools, appear in the dashboard, classify its recordings, and participate in scenarios. |
| 3 | Discovery | Python entry points (`octowright.session_kinds`) for discovery; **explicit enable** required before load. |
| 4 | Enable scope | Daemon-scoped (`OCTOWRIGHT_PLUGINS`, else user-level config). **Not** the CWD-walked project config. |
| 5 | Placement | Terminal moves to its own repository and release cadence. A partial reference plugin stays in `tests/`. |
| 6 | Recording | Core owns the JSONL `Recorder`. Plugins record rows; they never open files. |
| 7 | Side artifacts | Plugins request contained paths via `ctx.artifact_path(...)`; they never compose a path. Artifacts are registered so cleanup and storage reporting can see them. |
| 8 | Scenario capabilities | A plugin declares support from a **closed, core-defined** vocabulary. Core skips what a kind does not declare. |
| 9 | Closed-session discovery | Core writes a uniform `session_start` opening row, so discovery classifies recordings with **zero** plugin knowledge. |
| 10 | Dashboard UI | Plugins ship prebuilt JS. Core owns the page chrome; the plugin fills one pane via `mountStream`. |
| 11 | Trust model | The trust decision is concentrated at *enable*. No UI sandboxing. |
| 12 | Failure isolation | A failing plugin is skipped, logged, and reported — never fatal to the daemon. |

## 3. Goals and scope

### In scope

- A `SessionKindPlugin` contract with pool, session-record, capability, and frontend surfaces.
- Entry-point discovery with daemon-scoped explicit enable, version checking, and failure isolation.
- Generalizing the scenario layer from a browser/terminal binary to a kind registry.
- Generalizing dashboard session list, detail, and close to the registry.
- Uniform closed-recording classification that survives plugin uninstall.
- A frontend renderer contract plus a generic fallback renderer.
- Contained, registered side artifacts for plugins.
- An in-tree reference plugin and its contract tests.
- Deleting terminal from core and standing up `octowright-terminal`.

### Out of scope

- Publishing `provide-uterm` (see §10 — extraction does not make terminal installable).
- Any compatibility shim, deprecation window, or migration of existing scenario YAML.
- Changing the browser recording format.
- Plugin-authored HTTP routes beyond static asset serving.
- Terminal-session operation gating, control leases, and the repo-wide DRY audit — already
  out of scope for the operation gate and unchanged here.

## 4. The plugin contract

Three structural `Protocol`s. No inheritance — matching the existing deliberate choice that
`TerminalSession` is a *parallel* dataclass rather than a `BrowserSession` subclass. A plugin
implements shapes, never inheriting core's lifecycle assumptions.

### 4.1 `SessionKindPlugin`

The package-level descriptor an entry point resolves to.

| Member | Meaning |
|---|---|
| `kind: str` | The session kind. Must be unique across enabled plugins. |
| `display_name: str` | Human label for status output and the dashboard. |
| `api_version: int` | Refused on mismatch, with a legible message. |
| `supports: frozenset[str]` | Scenario capabilities from the closed vocabulary (§7.5). |
| `profile_name: str \| None` | Capability-profile name this plugin's tools register under. |
| `tool_module: str \| None` | Import path whose `@mcp.tool` decorators register the plugin's tools. |
| `frontend: FrontendAsset \| None` | Prebuilt UI (§8.5, §8.6). |
| `create_pool(ctx) -> SessionPool` | Builds the pool. |
| `resolve_participant(p, persona) -> dict` | Scenario participant → `pool.launch(**kwargs)` (§7.3). |
| `session_detail(session) -> dict` | Dashboard detail payload (§8.2). |

### 4.2 `SessionPool`

Verbatim the surface `TerminalPool` already exposes, because it is already proven sufficient for
every consumer in the tree:

`launch(...)`, `get(id)`, `maybe_get(id)`, `iter_sessions()`, `list_sessions()`,
`close(id, *, force=False)`, `close_all(*, force=False)`.

`close` must raise core's `ProtectedSessionCloseError` when the session is protected and `force` is
not set (§7.3).

### 4.3 `SessionRecord`

Verbatim `TerminalSession`'s field set: `instance_id`, `kind`, `label`, `profile`, `url` (nullable),
`recorder`, `log_path`, `protected`, plus `extra: dict` for kind-specific fields. Terminal's
`connector_type` becomes an `extra` member.

### 4.4 Plugin context (`ctx`)

Passed to `create_pool`. Exposes:

- `new_recording(instance_id, label) -> (Recorder, Path)` — core-issued, containment-checked.
- `artifact_path(session, name, suffix) -> Path` — contained side-artifact path (§5.2).
- `redaction_mode() -> str` — the resolved `OCTOWRIGHT_REDACT_INPUTS` policy.
- `recordings_dir: Path` — the owning pool's root.
- `log` — the structured logger.

Plugins receive the resolved redaction policy; they never read the environment variable themselves.
Same reasoning as `redact_headers_for_report` flooring at `passwords` rather than trusting a caller.

## 5. Recording and artifacts

Two tiers, with different rules. The distinction is load-bearing and is the main correction made
during brainstorming.

### 5.1 Tier 1 — the JSONL recorder is core-owned

Non-negotiable, and not for tidiness: **the recording is the session's identity.**
`http/discovery.py` classifies a closed session by reading its opening row; `/tail` and
`browser_tail_recording` share one cursor protocol; export, replay, and golden diffing all assume
one format. A plugin emitting its own log format would be invisible to all of it.

Every disk guarantee in the project is enforced at this boundary: `0600` recordings under a `0700`
parent, `RECORDINGS_DIR` containment, the `OCTOWRIGHT_RECORDING_MAX_BYTES` ceiling and its
`recording_truncated` marker, `OCTOWRIGHT_TAIL_MAX_BYTES` on the read side, and the per-pool
`recordings_dir` override. A plugin that opened its own file would fragment all of it silently.

The plugin's only recording surface is `recorder.record(action, **fields)`.

Because core owns the recorder, core also owns the **failed-launch rule** currently living inside
`TerminalPool._discard_failed_launch`: a launch that fails before anything is recorded must not
leave an orphaned empty recording, but a *partial* recording is kept, because a real if orphaned
recording beats destroying diagnostic data. Every plugin gets this right by default.

### 5.2 Tier 2 — side artifacts get core-issued paths

Core's own browser sessions write video, HAR, downloads, and traces, so a flat "plugins never write
files" rule would give plugins strictly less than browsers have and break the parity decision.

A plugin that needs a real file calls `ctx.artifact_path(session, name, suffix)`. Core resolves and
contains it under that session's recordings root, applies the `OCTOWRIGHT_RECORDINGS_PRIVATE` mode,
registers it on the session record, and returns it. The plugin writes to the path it was handed and
never composes one.

Path composition is precisely where this project's disk-containment bugs have lived —
`browser_export_script`'s `out_path`, the HAR path recovered from a poisoned launch record, and
`save_as` materializing a `NNN-..` parent from a remote-controlled `suggested_filename`. Each was
fixed by routing through a single resolve-and-contain choke point. Handing plugins a path composer
would reopen that class of bug in code core does not review.

Registration is the other half. Because the artifact is recorded on the session, `octowright
cleanup`, `octowright_storage_report`, and dashboard media can enumerate it. An artifact core does
not know about is an artifact core can never prune.

This is not a new pattern. `launch_helpers.build_recording_kwargs` already assembles video and HAR
paths under the pool's root and hands them to Playwright to write into. Tier 2 gives that existing
pattern a name and a public door.

`artifact_path` is the highest-risk API in this design and receives the same test battery
`reject_unsafe_path` has: `..` traversal, absolute paths, and a symlinked parent, with symlinks
resolved before the prefix check.

## 6. Discovery, enable, and lifecycle

### 6.1 Declaration

Entry point group `octowright.session_kinds`, resolving to a `SessionKindPlugin`. Installing a
package makes it **discoverable**, never loaded.

### 6.2 Enable is daemon-scoped

Resolution order:

1. `OCTOWRIGHT_PLUGINS=terminal,foo` — explicit, wins.
2. A `plugins:` list in `config_paths.user_config_dir() / "plugins.yaml"`.
3. Nothing loads.

**Not** `.octowright/config.yaml`. That file is found by `defaults._find_config()` walking up from
**CWD**, so enabling plugins there would make the MCP tool surface depend on which directory the
daemon happened to be spawned in — the same class of surprise as `octowright restart` ignoring
`--http-port`. The project config keeps doing what it does today (`label`, `persona`, `profile`):
per-project defaults, not capability grants.

### 6.3 Load sequence

Hooks where `server/_state.py` already does this work; that code is already the right shape and is
merely hardcoded to one plugin.

1. `_state.py` builds `pool` and `scenario_pool` as today.
2. Resolve entry points → filter to the enabled set → check `api_version` → `create_pool(ctx)`.
3. The result is a `dict[kind, SessionPool]` registry, replacing the single nullable `terminal_pool`.
4. `server/_optional_tools.py` imports each enabled plugin's `tool_module`; its `@mcp.tool`
   decorators fire on import, exactly as `server/terminal/` does today.

Step 4 is why enable must resolve before tool import: MCP registration is an import-time side
effect, so the decision has to precede it. That ordering already exists; it becomes data-driven.

`http/state.py` re-exports the registry through the same module-property seam it uses today for
`pool` / `scenario_pool` / `terminal_pool`.

### 6.4 Capability profiles

`server/profiles.PROFILES` is a static `dict[str, list[str]]` of tool names in core, so a plugin's
tools cannot be in it. A plugin declares `profile_name` and its tool names, and `build_allowed_set`
consults registered plugin profiles alongside `PROFILES`. Core's `terminals` profile entry leaves
with the terminal code; the plugin supplies it.

Plugins may **not** add to `ALWAYS_ON_TOOLS`. That set exists to guarantee diagnostics survive any
filter, and a plugin bypassing the filter would defeat its purpose.

### 6.5 Failure isolation

A plugin that raises during entry-point resolution, version check, or `create_pool` is skipped,
logged, and recorded — never fatal. The daemon owns live browsers; a bad third-party package must
not be able to take it down or push it into fragile inline mode.

### 6.6 Shutdown

`cli/serve._shutdown_browser_pool_on_shutdown` gains the registry and calls `close_all(force=True)`
on every pool at daemon exit. Today only the browser pool is torn down there, so terminals rely on
process death — survivable for a PTY, not for an SSH session.

### 6.7 Observability

`octowright_status()["plugins"]` lists every **discovered** plugin as
`{name, version, kind, api_version, state}` where `state` is `enabled`, `disabled`, or `failed`
with a reason. Discovered-but-disabled must be visible, or "why don't I have terminal tools?"
becomes unanswerable from inside the agent — the same gap the `follower_versions` work closed for
version skew.

## 7. Scenario participation

The heaviest seam: 62 terminal-mentioning lines in `scenarios_pool.py` alone, plus `terminal_pool`
threaded explicitly through eight method signatures.

### 7.1 Participant kind is registry-resolved

`_validate_participant_kind` today branches `if p.kind == "terminal": … elif p.kind not in
SUPPORTED_KINDS: raise`. It becomes: a browser engine name, or a kind registered by an enabled
plugin, else the same error — now listing the enabled kinds, so a typo or a disabled plugin is
self-diagnosing.

### 7.2 `connector_type` leaves core

`Participant.connector_type` is a terminal-specific field in core's dataclass, and core's YAML
parser defaults it to `"pty"`. Both are removed. They are replaced by a free-form `options: dict`
that core passes through opaquely and the plugin validates.

**This changes the scenario YAML shape**: `connector_type: ssh` moves under `options:`. Accepted as
a break under the no-migration decision. It touches `examples/scenarios/browser-plus-terminal.yaml`
and any `demo/bundles/` scenario declaring a terminal participant.

### 7.3 Launch-kwarg resolution becomes a plugin method

`scenarios.resolve_terminal_launch(p)` becomes `plugin.resolve_participant(p, persona)`. This is
what deletes core's `from octowright.terminal.connector_config import …`, and with it the reason
that module was carved out as "pure builders so core doesn't import uterm." The workaround stops
being necessary once the dependency inverts.

SSH field resolution from a persona's freeform `app.ssh` block, and the rule that **no SSH password
is read from a scenario**, move into the plugin unchanged.

### 7.4 The binary partition becomes a group-by

`start()` today partitions participants into `terminal_specs` / `browser_specs`. It becomes: group
by kind, look each kind's pool up in the registry, launch each group, reassemble in declaration
order. `_pool_for`, `_close_launched`, `_rollback_start`, `stop`, and `remap` already operate on
`(pool, ids)` pairs and simply iterate the registry.

### 7.5 Browser-only steps become declared capabilities

There are five `kind == "terminal"` skip sites today — fixtures, startup/verify/teardown macros,
`wait_for_sync`, dialog policy, and mock routes. Replacing each with `kind not in browser_kinds`
would re-hardcode the same binary under a new name.

Instead a plugin declares `supports: frozenset[str]` from a **closed, core-defined** vocabulary:

`{"macros", "fixtures", "sync", "dialog_policy", "mock_routes"}`

Browsers declare all five. Terminal declares none. Core checks membership.

The vocabulary is *derived from the five skip sites that already exist*, not invented ahead of need.
The existing validation error ("terminal participant cannot declare `startup_macros`") stops being a
special case and becomes "a kind that does not declare `macros` cannot declare `startup_macros`",
covering every future plugin for free.

The vocabulary is **closed** deliberately: core must know what a capability means in order to skip
it, so a plugin inventing one would declare something core cannot act on. Adding a capability is a
core change, and that is correct.

Roles are untouched. `_validate_scenario` keeps logging `scenario.unknown_role`; plugins get no say
in role vocabulary.

## 8. Dashboard and HTTP

### 8.1 Live list

Already generic — `_live_summary` is getattr-defensive and, per its own comment, terminal sessions
"serialize through it cleanly." It iterates the registry instead of `browser_pool` plus one
nullable.

### 8.2 Detail

`_terminal_session_detail` moves to the plugin as `session_detail(session)`. Core dispatches by kind
through the registry, falling back to the browser detail builder. The existing short-circuit before
the browser builder is preserved; it stops being a hardcoded branch.

Registered Tier-2 artifacts appear in the detail payload so the dashboard can link them.

### 8.3 Close

`_maybe_close_terminal` generalizes to registry iteration. `ProtectedTerminalCloseError` becomes a
core `ProtectedSessionCloseError` that plugins raise — this removes
`http/routes/sessions.py`'s direct `from octowright.terminal.errors import …`, the single import
that most clearly inverts under this design. The 409-on-protected-without-force mapping is unchanged.

### 8.4 Closed-recording discovery must not need the plugin

`_read_first_opening` hardcodes `("launch", "terminal_start")` and `_summarise_recording` hardcodes
the terminal branch. Making that registry-driven would be **wrong**, because recordings outlive
plugins: uninstall the terminal plugin and every terminal recording on disk becomes `unknown` —
unlistable and unprunable.

Since core owns the recorder (§5.1), core writes the opening row itself: a `session_start` row
carrying `kind`, `label`, and `profile`, written before control passes to the plugin. Discovery
reads `kind` off that row and never needs to know what kinds exist.

Browser recordings still open with `launch`, so discovery recognizes both shapes. That asymmetry is
accepted rather than rewriting the browser recording format, which would break every existing
recording, export, and golden.

### 8.5 Plugin asset serving

`GET /plugins/{name}/{path}`, path-contained against the plugin's declared asset directory with
symlinks resolved before the prefix check — the same discipline as `RECORDINGS_DIR`.

Gated like the **static SPA mount**, not like the session APIs: behind the Host/Origin
`SensitiveASGIGuard`, not behind dashboard pairing. These are static assets from an
operator-enabled package, they contain no session data, and the dashboard shell must boot before
pairing completes.

### 8.6 Frontend renderer contract

`GET /api/plugins` returns `kind → {moduleUrl, apiVersion, displayName, layout}`.

`session.ts` replaces `if (detail.kind === "terminal")` with a registry lookup and a dynamic import
of `moduleUrl` — the lazy-chunk pattern already at `session.ts:665`, now data-driven.

**Core owns the page chrome; the plugin fills one pane.** `bootTerminalSession` today owns the whole
page but almost nothing in it is terminal-specific: it builds a header / slot / timeline / footer
layout and then calls back into core for `renderHeader`, `renderFooter`,
`installDashboardAuthRequiredNotice`, `renderTimeline`, `appendTimelineEvents`, `openTail`,
`getEvents`, and `tailWebSocketUrl`. The only plugin-owned code is `mountTerminalView` and
`view.feedEvents(...)`.

So core renders the chrome and does the `getEvents` → `renderTimeline` → `openTail` →
`appendTimelineEvents` wiring itself, and the plugin implements only:

```
mountStream(el, ctx) -> { feed(events), destroy() }
```

This yields an **identical page by construction** — header, timeline, footer, and the
`session-page--terminal` / `.session-terminal` CSS stay core code rendering the same DOM, so nothing
about the layout can drift. It shrinks the frontend SDK from eight exported functions to two
concepts, and the plugin never touches the WebSocket, the cursor protocol, or the auth notice.

`layout` selects between core's browser page and the slim stream page. Closed vocabulary, same
approach as §7.5; today there are exactly two values.

### 8.7 Fallback renderer

Core ships a generic action-timeline plus raw-JSONL view, used when a kind has no frontend, when
`apiVersion` mismatches, or when the module import fails. The mismatch case renders **with a visible
reason** — a blank panel with a console error is the worst possible failure for something a third
party built.

### 8.8 Types

`connector_type` and the `"telnet"` member of the kind union leave `types.ts` with the plugin.
Core's `SessionSummary` keeps a free-form `extra` for kind-specific fields.

## 9. Trust model

An enabled plugin runs Python **inside the leader process**. It can already drive browsers, read the
`0600` lockfile, mint its own pairing code, and read every recording. Its JavaScript holding the
dashboard bearer is therefore strictly *weaker* than what its Python half already holds.

The trust decision is concentrated at **enable** (§6.2). After that, sandboxing the UI would defend
against nothing, so there is no iframe isolation and no separate origin. Plugin JS is served
same-origin and imported directly.

This is why explicit enable is required rather than auto-loading discovered entry points: installing
a package — including a transitive dependency — must not silently extend a browser-driving daemon.

## 10. What moves, and the honest accounting

Measured on the current tree:

| Leaves core | LOC |
|---|---|
| `src/octowright/terminal/` + `src/octowright/server/terminal/` | 937 |
| `tests/terminal/` + `tests/test_terminal_supervision.py` | 1,262 |
| `terminal-view.ts`, `session-terminal.ts` and their tests | 531 |
| terminal-specific lines across 13 core files | 187 |
| terminal prose in `AGENTS.md`, `README.md`, `docs/getting-started.md`, the tool inventory, and the shared contract | substantial |

Core **gains**: plugin contract Protocols (~80), registry and loader (~150), `artifact_path` and its
containment (~80), `/api/plugins` and asset serving (~80), frontend registry lookup and fallback
renderer (~120 TS), and the reference plugin (~150). New machinery is split across
`octowright/plugins/{contract,registry,loader,artifacts}.py` to respect the 500-LOC file ceiling.

**Net LOC saving is modest — roughly 500 Python and 130 TypeScript.** The case for this design is
not line count. It is that core stops knowing a terminal exists: a change to scenarios, session
detail, discovery, or close reasons about a registry instead of a second hardcoded kind. The
`AGENTS.md` reduction is disproportionately valuable because that file is read on every task.

### The uterm caveat

Extraction does **not** make terminal installable. `provide-uterm` and `provide-uterm-server` both
return 404 from PyPI, so `octowright-terminal` will be exactly as uninstallable as
`octowright[terminal]` is today. What changes is that the blocker moves *out of octowright's release
path*: octowright ships complete, and uterm's schedule stops being octowright's problem. If
installability is the goal, publishing uterm is a separate task and this design does not substitute
for it.

## 11. Testing

### 11.1 The reference plugin

A fake session kind under `tests/`, exercising every seam: its own pool, MCP tools, a declared
capability set, scenario participation, session detail, protected close, a Tier-2 artifact, and a
frontend `mountStream` module.

The failure mode to design against is the reference plugin decaying into a toy that passes while
representing nothing. Two guards:

1. It declares a **partial** capability set, so the skip paths are exercised rather than only the
   happy path.
2. A contract test asserts the reference plugin covers every member of the capability vocabulary and
   every `SessionPool` method — so adding a capability without covering it fails CI.

### 11.2 Specific test obligations

- `artifact_path`: `..` traversal, absolute paths, symlinked parent, symlink resolution before the
  prefix check.
- `api_version`: a test ties the constant to the contract's shape, so a contract change that forgets
  to bump it fails.
- Discovery: a recording written by a plugin that is **no longer installed** still classifies, lists,
  and prunes correctly.
- Failure isolation: a plugin raising at each of resolve / version-check / `create_pool` leaves the
  daemon healthy and reports `state: failed` in `octowright_status`.
- Enable scope: a `.octowright/config.yaml` naming a plugin does **not** load it.
- Frontend: an `apiVersion` mismatch renders the fallback with a visible reason, not a blank pane.

## 12. Build order

Sequencing only — there is no migration and no compatibility window.

Each step is its own implementation plan. Step 1 is the only one that can start immediately;
steps 2 and 3 depend on the contract it lands, and step 4 depends on both.

1. **Contract, registry, loader, reference plugin.** CI green with terminal still on its existing
   path.
2. **Invert the seams.** Scenarios, dashboard detail/close, discovery's `session_start` row,
   profiles, shutdown, status.
3. **Frontend contract.** `/api/plugins`, `mountStream`, core-owned chrome, fallback renderer.
4. **Delete terminal from core**; stand up the `octowright-terminal` repository.

## 13. Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | `api_version` is only useful if core actually bumps it | Test ties the constant to the contract shape (§11.2) |
| 2 | `artifact_path` containment bugs reopen a closed bug class | Full `reject_unsafe_path` test battery (§5.2) |
| 3 | Reference plugin decays into a toy | Partial capability set + coverage assertion (§11.1) |
| 4 | Scenario-YAML break reaches examples and demo bundles | Enumerated in §7.2; update in step 2 |
| 5 | Third-party plugin frontend drifts from core's chrome | Impossible by construction — core renders the chrome (§8.6) |
| 6 | Plugin API rots with no in-repo consumer | Reference plugin is that consumer (§11.1) |
