# Session-Kind Plugins — Design Spec

**Date:** 2026-08-22
**Status:** Revised after external contract review; pending written-spec approval
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

The governing principle, and the correction that drove this revision: **core owns every lifecycle it
promises to enforce.** A guarantee that reads as a documented obligation on the plugin author is not a
guarantee. So the recorder is not merely core-issued but core-*transacted*; scenario capabilities are
not declared strings but derived from supplied handlers; artifact registration is not an in-memory
note but a durable row.

The goal is **not** line-count reduction — §10 does that arithmetic, and after two review rounds the
Python saving is best treated as zero. The goal is **dependency inversion**: after this, a change to scenarios, session detail, closed-session
discovery, or close semantics reasons about a registry instead of about a second hard-coded session
kind, and `provide-uterm`'s release schedule stops gating octowright's.

There is no migration path and no compatibility shim. This spec describes the end state.

## 2. Locked decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Motivation | Cut core maintenance **and** enable third-party session kinds. Not primarily about unblocking the terminal install. |
| 2 | Integration depth | **Full parity.** A plugin can own a session pool, register MCP tools, appear in the dashboard, classify its recordings, and participate in scenarios. |
| 3 | Discovery | Python entry points (`octowright.session_kinds`) for discovery; **explicit enable** required before load. |
| 4 | Enable scope | Daemon-scoped (`OCTOWRIGHT_PLUGINS`, else user-level config). **Not** the CWD-walked project config. |
| 5 | Placement | Terminal moves to its own repository and release cadence. A partial reference plugin stays in `tests/`. |
| 6 | Recording | Core owns the recorder **and the launch transaction around it**. A plugin cannot obtain a recorder outside that transaction. Core's own metadata rows are **control rows** that bypass the byte ceiling (§5.3). |
| 7 | Side artifacts | Core issues contained paths; the plugin commits, and the commit writes a durable JSONL row. Artifacts never registered in memory only. |
| 8 | Scenario capabilities | Closed, core-defined vocabulary of four. A plugin supplies a **handler** per capability; core *derives* the supported set from the handlers present. Capabilities are never self-declared strings. |
| 9 | Closed-session discovery | Core writes a uniform `session_start` opening row, so discovery classifies recordings with **zero** plugin knowledge. |
| 10 | Dashboard UI | Plugins ship prebuilt JS. Core owns the page chrome; the plugin fills one pane via `mountStream`. |
| 11 | Versioning | Backend (`plugin_api_version`) and renderer (`renderer_api_version`) version independently. |
| 12 | Trust model | The trust decision is concentrated at *enable*. No UI sandboxing. |
| 13 | Failure isolation | Ordinary exceptions where core retains control are isolated, logged, and reported. Enabled plugins share the leader's process; crashes, hangs, and deliberate interference are **not** isolated (§9). |
| 14 | Identity | The entry-point name is the configured identity. `kind` is runtime metadata and is not what an operator types (§4.2). |
| 15 | Session registry | The plugin's `SessionPool` is the **single** registry. Core holds no parallel session table; it enforces cross-pool ID uniqueness at commit (§4.3). |

## 3. Goals and scope

### In scope

- A plugin contract with descriptor, pool, session-record, scenario-adapter, and frontend surfaces.
- Entry-point discovery with daemon-scoped explicit enable, version checking, namespace validation,
  and a rollback-capable load transaction.
- Generalizing the scenario layer from a browser/terminal binary to a kind registry with per-kind
  adapters.
- Generalizing dashboard session list, detail, and close to the registry.
- Uniform closed-recording classification that survives plugin uninstall.
- A frontend renderer contract plus a generic fallback renderer.
- Contained, durably registered side artifacts for plugins.
- An in-tree reference plugin and its contract tests.
- Deleting terminal from core and standing up `octowright-terminal`.

### Out of scope

- Publishing `provide-uterm` (see §10 — extraction does not make terminal installable).
- Any compatibility shim, deprecation window, or migration of existing scenario YAML.
- Changing the browser recording format.
- Plugin-authored HTTP routes beyond static asset serving.
- Process-level plugin isolation (subprocess/RPC boundary). See §9 for what that costs and why the
  in-process boundary is accepted instead.
- Terminal-session operation gating, control leases, and the repo-wide DRY audit — already
  out of scope for the operation gate and unchanged here.

## 4. The plugin contract

Structural `Protocol`s throughout. No inheritance — matching the existing deliberate choice that
`TerminalSession` is a *parallel* dataclass rather than a `BrowserSession` subclass. A plugin
implements shapes, never inheriting core's lifecycle assumptions.

### 4.1 `SessionKindPlugin`

The package-level descriptor an entry point resolves to. Every member is metadata core can validate
*before* it runs any plugin code beyond the module import that produced the descriptor.

| Member | Meaning |
|---|---|
| `kind: str` | The session kind, stamped into recordings and used for renderer dispatch. Unique across enabled plugins; namespace-validated (§4.2). |
| `display_name: str` | Human label for status output and the dashboard. |
| `plugin_api_version: int` | Backend contract version. Refused on mismatch, with a legible message. Independent of the renderer version (§8.7). |
| `tool_names: frozenset[str]` | Every MCP-visible tool name `tool_module` will register. Validated for collisions before import (§6.4). |
| `tool_module: str \| None` | Import path whose `@mcp.tool` decorators register those tools. |
| `profile_name: str \| None` | Capability-profile name the plugin's tools register under (§6.5). |
| `frontend: FrontendAsset \| None` | Prebuilt UI (§8.5–§8.7). |
| `create_scenario_adapter(pool) -> ScenarioAdapter \| None` | Scenario participation (§7.3). Returns `None` when the kind cannot appear in a scenario at all. A factory rather than an attribute because the adapter resolves instance IDs against the pool, which does not exist until `create_pool` has run. |
| `create_pool(ctx) -> SessionPool` | Builds the pool. The only member core *invokes* during load; importing `tool_module` also runs plugin code (§6.3). |
| `session_detail(session) -> dict` | Dashboard detail payload (§8.2). |

There is deliberately **no** `supports` member. The supported capability set is derived from the
adapter's handlers (§7.5), because a self-declared capability string is a claim core cannot check.

```python
@dataclass(frozen=True)
class FrontendAsset:
    renderer_api_version: int
    asset_dir: Path                        # served under /plugins/{name}/
    module_path: str                       # relative to asset_dir
    layout: Literal["browser", "stream"]
```

### 4.2 Identity and namespaces

Seven identifiers appear in this design; their relationships are fixed here, because several flow
into URLs, recording metadata, status output, and lookup dictionaries.

- **Entry-point name** is the configured identity. It is what an operator writes in
  `OCTOWRIGHT_PLUGINS`, what `octowright_status()` reports as `name`, and what appears in the
  `/plugins/{name}/` asset route. It is available from metadata **without importing the plugin**,
  which is exactly why it and not `kind` is the enable identity (§6.8).
- **`kind`** is runtime metadata. It need not equal the entry-point name, but both are validated
  against `^[a-z][a-z0-9_-]{0,63}$`.
- **Reserved kinds:** the three browser engine names (`chromium`, `firefox`, `webkit`), `browser`,
  `unknown`, and `session`. A plugin claiming one is refused.
- **Duplicate entry-point names** across installed distributions are refused outright rather than
  resolved by enumeration order, which is installation-dependent and would make behaviour vary by
  machine.
- **Tool names** must be prefixed `{kind}_`. This is enforced, not advisory: it makes the collision
  check in §6.4 a fast-path in the common case and keeps a third-party tool from squatting a name
  core may want later.
- **Profile names** share one namespace with core's `PROFILES`. A plugin may create a profile; it may
  not extend or shadow a core one.

### 4.3 `SessionPool`

Written out rather than deferred to "whatever `TerminalPool` does", because this is a public
third-party contract and the reader will not have `TerminalPool` open.

```python
class SessionPool(Protocol):
    async def launch(self, **kwargs: Any) -> LaunchResult: ...
    def get(self, instance_id: str) -> SessionRecord: ...
    def maybe_get(self, instance_id: str) -> SessionRecord | None: ...
    def iter_sessions(self) -> Iterator[SessionRecord]: ...
    async def close(self, instance_id: str, *, force: bool = False) -> CloseResult: ...
    async def close_all(self, *, force: bool = False) -> None: ...
```

`LaunchResult` and `CloseResult` are `TypedDict`s, not bare dicts. `LaunchResult` requires
`instance_id`, `kind`, `label`, `profile`, `log_path`; anything else lands in `extra`.

Rules, all of which core relies on:

- `get` raises `KeyError` for an unknown id; `maybe_get` returns `None`.
- `close` raises core's `ProtectedSessionCloseError` when the session is protected and `force` is not
  set (§8.3).
- `iter_sessions` returns a **snapshot** — core iterates it while other tasks may be launching.
- `close_all` continues past an individual failure and raises an aggregate at the end. Daemon
  shutdown depends on this (§6.7).
- Core may call any method concurrently from different tasks; the pool serializes its own state.
- Instance IDs must be unique **across all pools**, not merely within one, because core's HTTP layer
  resolves a session by id alone and searches the registry. **Core enforces this** at
  `launch.commit()` by probing every other registered pool's `maybe_get`, and fails the transaction
  on collision. Stating the requirement and then declining to check it would contradict §1's
  governing principle in the same document that sets it out.

The pool is the **single** session registry. Core keeps no parallel table: `_live_summary`, session
detail, and close all resolve by iterating `iter_sessions` / `maybe_get` across registered pools. This
is why `launch.commit()` does not "register with core" in any sense that implies a second store — it
validates the record, enforces uniqueness against the other pools, and hands the record back for the
plugin's own pool to hold (§5.1).

`list_sessions` is **removed** from the contract. Terminal's version duplicated serialization core
already does in `_live_summary`; core now serializes from `iter_sessions` for every kind, which is
what makes the live list genuinely generic (§8.1).

`close` returning a `CloseResult` rather than `None` resolves a real discrepancy: terminal's `close`
returns `None` while browser close paths return metadata used for close-time cache warming. A generic
close route needs one shape.

### 4.4 `SessionRecord`

`instance_id`, `kind`, `label`, `profile`, `url` (nullable), `recorder`, `log_path`, `protected`,
plus `extra: dict` for kind-specific fields. Terminal's `connector_type` becomes an `extra` member.

### 4.5 Plugin context (`ctx`)

Passed to `create_pool`. Exposes:

- `begin_session(*, instance_id, label, profile, extra=None) -> SessionLaunch` — the mandatory launch
  transaction (§5.1). It takes no `kind`: `ctx` already holds the validated descriptor, so accepting
  one would let a plugin stamp a recording with a kind core never approved.
- `artifact(session, name, suffix) -> ArtifactHandle` — contained side artifact (§5.2).
- `redaction_mode() -> str` — the resolved `OCTOWRIGHT_REDACT_INPUTS` policy.
- `recordings_dir: Path` — the owning pool's root.
- `log` — the structured logger.

Plugins receive the resolved redaction policy; they never read the environment variable themselves.
Same reasoning as `redact_headers_for_report` flooring at `passwords` rather than trusting a caller.

Note there is **no** `new_recording`. `begin_session` is the only way to obtain a `Recorder`, which is
what makes §5.1's guarantee structural rather than documentary.

## 5. Recording and artifacts

Two tiers with different rules. The distinction is load-bearing.

### 5.1 Tier 1 — the launch transaction

The recording is the session's identity. `http/discovery.py` classifies a closed session by reading
its opening row; `/tail` and `browser_tail_recording` share one cursor protocol; export, replay, and
golden diffing all assume one format. A plugin emitting its own log format would be invisible to all
of it.

Every disk guarantee in the project is enforced at this boundary: `0600` recordings under a `0700`
parent, `RECORDINGS_DIR` containment, the `OCTOWRIGHT_RECORDING_MAX_BYTES` ceiling and its
`recording_truncated` marker, `OCTOWRIGHT_TAIL_MAX_BYTES` on the read side, and the per-pool
`recordings_dir` override.

So core does not hand a plugin a recorder and hope. It runs the launch:

```python
async with ctx.begin_session(
    instance_id=instance_id,
    label=label,
    profile=profile,
) as launch:
    engine = await backend.start(launch.recorder)
    return launch.commit(session_record)   # plugin then holds the record in its own pool
```

`__aenter__` opens the recorder under containment and writes the `session_start` row (§8.4) carrying
`kind`, `label`, and `profile` — which is why `profile` is a parameter here and was missing from the
`new_recording(instance_id, label)` shape this replaces.

`commit(record)` validates and finalizes; it does **not** create a second registry (§4.3). It checks
that the record's `instance_id`, `kind`, `recorder`, and `log_path` match the ones the transaction
issued — a plugin swapping in its own recorder would otherwise silently escape every guarantee this
section exists to provide — enforces cross-pool ID uniqueness, returns the `LaunchResult`, and marks
the transaction successful. The plugin's pool holds the record. A block that exits without committing
is treated as a failure.

`__aexit__` on any `BaseException`, cancellation included:

1. Closes the recorder.
2. Deletes the recording **if it contains nothing but core's own opening row**. A *partial* recording
   is kept, because a real if orphaned recording beats destroying diagnostic data.
3. Re-raises.

Point 2 is the corrected form of `TerminalPool._discard_failed_launch`, which deletes only when
`st_size == 0`. That heuristic worked because nothing was written before the failure; once core writes
`session_start` first, size is never zero, and the old rule would leave an opening-only orphan behind
every failed launch. Content, not size, is the durable test.

The plugin's only recording surface is `launch.recorder.record(action, **fields)`. It never opens a
file, never composes a path, and cannot acquire a recorder outside this block.

### 5.2 Tier 2 — side artifacts

Core's own browser sessions write video, HAR, downloads, and traces, so a flat "plugins never write
files" rule would give plugins strictly less than browsers have and break the parity decision.

A plugin that needs a real file reserves, writes, then commits:

```python
artifact = ctx.artifact(session, name="transcript", suffix=".txt")
await write_transcript(artifact.path)
artifact.commit(mime_type="text/plain")
```

`ctx.artifact` resolves and contains the path under that session's recordings root, creates and
secures the per-session artifact directory under `OCTOWRIGHT_RECORDINGS_PRIVATE` **before** returning
— core cannot secure a file it does not write, so the directory is the control, consistent with how
`secure_artifact_tree` already treats captures, goldens, and macros.

`commit` writes a durable **control row** (§5.3) into the session's own JSONL:

```json
{"action": "artifact_registered", "artifact_id": "transcript",
 "path": "session-artifacts/abc123/transcript.txt", "mime_type": "text/plain"}
```

The stored path is **relative** to the recordings root and is re-resolved and re-contained on read, so
a recording moved between machines still works and a hand-edited recording cannot point the media
route at `/etc/passwd`.

Durability is the point. An in-memory note on the session record dies at close and at daemon restart,
and closed-session artifact scanning (`http/artifacts.py`) recognizes only fixed browser sidecars, so
an in-memory registry would make plugin artifacts invisible to exactly the readers that need them.
Registration in JSONL also survives plugin uninstall, matching §8.4's reasoning.

Lifecycle rules: an artifact exists once committed. A reserved-but-never-committed path is never
referenced by anything and is pruned by ordinary age-based cleanup. `artifact_id` is unique per
session; committing the same id twice replaces the reference. The dashboard serves only committed
artifacts, and only with a MIME type from a core allowlist.

**Correction to an earlier draft of this spec:** registration is *not* what makes an artifact
prunable. `recording_cleanup.find_stale_files` and `captures.storage_report` both walk the tree with
`rglob` and never consult a registry — an unregistered file is already pruned and already counted.
Registration buys association, presentation, and controlled serving. The earlier claim that "an
artifact core does not know about is an artifact core can never prune" was simply wrong.

Path composition is where this project's disk-containment bugs have lived — `browser_export_script`'s
`out_path`, the HAR path recovered from a poisoned launch record, and `save_as` materializing a
`NNN-..` parent from a remote-controlled `suggested_filename`. Each was fixed by routing through a
single resolve-and-contain choke point. Handing plugins a path composer would reopen that class of bug
in code core does not review. `ctx.artifact` receives the same test battery `reject_unsafe_path` has:
`..` traversal, absolute paths, and a symlinked parent, with symlinks resolved before the prefix check.

### 5.3 Control rows bypass the byte ceiling

`Recorder.record()` stops writing once `OCTOWRIGHT_RECORDING_MAX_BYTES` is reached: it emits one
`recording_truncated` marker, sets `_truncated`, and every later call returns silently. That is
correct for the action stream — the ceiling exists to bound a firehose page — but it makes core's own
metadata rows unreliable in exactly the way that matters:

- A long-running session that hits its ceiling and then commits an artifact gets a *successful*
  `commit()` whose `artifact_registered` row was dropped. The durability §5.2 promises evaporates
  silently.
- A ceiling smaller than the opening row means `session_start` never lands, so discovery loses the
  kind (§8.4) and the failed-launch rule's "nothing but core's own opening row" test (§5.1) has no
  opening row to reason about.

So core's metadata rows are **control rows**, written through `Recorder.record_control(...)`, which
bypasses the ceiling and is drawn from a small separate budget of its own. The precedent already
exists in the file: `_write_truncation_marker` deliberately bypasses the ceiling for exactly this
reason — "so the cut is always visible to replay/export/discovery." Control rows generalize that rule
rather than inventing one.

The control set is closed and core-only: `session_start`, `artifact_registered`,
`recording_truncated`. A plugin cannot write one; `record()` remains its only surface, ceiling and
all. The separate budget is bounded so a plugin cannot evade the disk-fill guard by committing
artifacts in a loop — a commit that would exceed the control budget fails, which is a failure the
plugin can see, rather than a success whose row vanished.

This is off-by-default territory in practice (`OCTOWRIGHT_RECORDING_MAX_BYTES` is unset by default,
so nothing truncates), but a guarantee that holds only when a knob is off is not a guarantee.

## 6. Discovery, enable, and lifecycle

### 6.1 Declaration

Entry point group `octowright.session_kinds`, resolving to a `SessionKindPlugin`. Installing a
package makes it **discoverable**, never loaded.

### 6.2 Enable is daemon-scoped

Resolution order, by **entry-point name** (§4.2):

1. `OCTOWRIGHT_PLUGINS=terminal,foo` — explicit, wins.
2. A `plugins:` list in `config_paths.user_config_dir() / "plugins.yaml"`.
3. Nothing loads.

**Not** `.octowright/config.yaml`. That file is found by `defaults._find_config()` walking up from
**CWD**, so enabling plugins there would make the MCP tool surface depend on which directory the
daemon happened to be spawned in — the same class of surprise as `octowright restart` ignoring
`--http-port`. The project config keeps doing what it does today (`label`, `persona`, `profile`):
per-project defaults, not capability grants.

### 6.3 The load transaction

A plugin either loads completely or not at all. Partial load is the failure mode worth designing
against: a plugin whose tools registered but whose pool did not exist would answer MCP calls with
internal errors forever.

1. Enumerate entry points in `octowright.session_kinds`. Reject duplicate names.
2. Filter to the enabled set. An enabled name with no entry point is recorded `state: missing`.
3. Import the entry point and resolve the descriptor.
4. Validate metadata with **no** plugin logic run: `plugin_api_version`, `kind` syntax and reserved
   list, `tool_names` prefix and collisions (§6.4), `profile_name` collision, frontend asset paths.
5. Register the profile name, so it is visible before any tool decorator fires (§6.5).
6. Snapshot the tool manager's registered names, import `tool_module`, then compute the **delta**
   (§6.4). Its `@mcp.tool` decorators register the tools during the import.
7. `create_pool(ctx)`.
8. `create_scenario_adapter(pool)`; derive the capability set from the handlers it supplies (§7.5).
9. Commit: add the pool to the registry and mark `state: enabled`.

Rollback on failure at any step: unregister **the entire measured delta** from step 6, unregister the
profile, close a pool created in step 7, mark `state: failed` with the reason, and continue to the
next plugin.

Ordering note — tool import precedes pool creation deliberately. Tool registration is the step whose
rollback touches shared state; doing it before the pool exists means the pool never has to be torn
down for a tool failure in the common case, and `create_pool` failing leaves only the tool
registrations to unwind.

`http/state.py` re-exports the registry through the same module-property seam it uses today for
`pool` / `scenario_pool` / `terminal_pool`.

### 6.4 Tool registration and collisions

The installed MCP `ToolManager.add_tool` is **first-wins with a warning**: a duplicate name logs
`Tool already exists` and returns the existing registration. So without a check, a plugin could load
successfully while some of its tools silently resolve to core's or to another plugin's — a failure
that is invisible in status output and only shows up as wrong behaviour at call time.

Core therefore validates `tool_names` in step 4 against the live tool manager and against every other
enabled plugin's declared names, and refuses the plugin on collision. The `{kind}_` prefix rule
(§4.2) makes cross-plugin collision nearly impossible and core collision impossible.

Rollback removes **what was actually registered**, not what was declared. Core snapshots the tool
manager's key set immediately before the import and diffs it after; on failure the whole delta is
removed, and on success the delta is checked against the subset of `tool_names` the active profile
permits. Rolling back by declared name alone would leak an *undeclared* tool registered by a module
that then raised — the one case rollback exists for.

Both the snapshot and the removal reach into the tool manager's mapping, which is private. That is an
accepted, narrow coupling, pinned by a test so an SDK change fails loudly rather than silently leaving
a half-registered plugin.

**Rejected alternative:** a staged `register_tools(registrar)` interface that validates before
mutating. It is cleaner in isolation but gives plugins a second, different registration path from the
one all 129 core tools use, and every future change to tool wrapping (heartbeat, idempotency, advisor
tracking) would have to be applied twice. Declared names plus rollback keeps one path.

### 6.5 Capability profiles

`server/profiles.PROFILES` is a static `dict[str, list[str]]` of tool names in core, so a plugin's
tools cannot be in it. A plugin declares `profile_name` and `tool_names`, and `build_allowed_set`
consults registered plugin profiles alongside `PROFILES`. Core's `terminals` profile entry leaves with
the terminal code; the plugin supplies it.

**Bootstrap ordering is the whole requirement, and the current code gets it wrong twice.**
`_state.py` computes `_allowed_tools = active_filter()` at module import, before any plugin is
discovered. Two consequences, both real:

1. `build_allowed_set` resolves the spec against `PROFILES` alone. Registering a plugin profile
   afterwards does not retroactively widen an already-computed set.
2. It also *diagnoses* against `PROFILES` alone — `OCTOWRIGHT_PROFILE=terminals` with terminal as a
   plugin logs `octowright.profile.unknown`, and if that is the only name, `octowright.profile.all_unknown`
   at ERROR. Both would be false, and the second is the loudest signal the daemon emits.

So the bootstrap becomes explicit: `_state.py` stores the **raw spec**, discovery and enable
resolution run, plugin profiles register, and only then is the allowed set computed and applied. The
filter is a mutable `set` that `_ProfiledMCPServer.tool()` re-reads on every call, so it is mutated in
place; rebinding the name would not take. Diagnostics fire after plugin profiles are known, so an
unknown-profile warning means the profile is genuinely unknown.

Pinned by a test that loads a plugin under a narrow `OCTOWRIGHT_PROFILE` naming that plugin's profile
and asserts both that its tools registered **and** that no unknown-profile diagnostic fired.

Plugins may **not** add to `ALWAYS_ON_TOOLS`. That set exists to guarantee diagnostics survive any
filter, and a plugin bypassing the filter would defeat its purpose.

### 6.6 Failure isolation

Ordinary exceptions raised during entry-point resolution, metadata validation, tool import,
`create_pool`, and any core-routed call into a plugin are caught, logged, and surfaced in status. The
daemon owns live browsers; a bad third-party package must not be able to take it down or push it into
fragile inline mode.

The honest limit is stated in §9: an enabled plugin shares the leader's process, so a hang, an
`os._exit`, a native crash, or deliberate interference is not isolated by any of this.

### 6.7 Shutdown

`cli/serve._shutdown_browser_pool_on_shutdown` gains the registry and calls `close_all(force=True)` on
every pool at daemon exit, tolerating and aggregating per-pool failures. Today only the browser pool
is torn down there, so terminals rely on process death — survivable for a PTY, not for an SSH session.

### 6.8 Observability

`octowright_status()["plugins"]` lists every plugin core knows about. The shape differs by state,
because **reporting `kind` or `plugin_api_version` for a disabled plugin would require importing it**,
executing precisely the code explicit enable exists to gate.

Disabled — metadata only, no import:

```json
{"name": "terminal", "distribution": "octowright-terminal", "version": "1.2.0",
 "entry_point": "octowright_terminal.plugin:plugin", "state": "disabled"}
```

Enabled — the descriptor resolved, so `kind`, `display_name`, `plugin_api_version`, and `tool_names`
are added.

Failed — `reason` is always present; the descriptor fields are **optional**, because a plugin that
raised while importing its own module has no descriptor to report. A schema requiring them would make
the earliest and most common failure unreportable.

`state` is one of `enabled`, `disabled`, `failed`, or `missing`. `missing` covers an
`OCTOWRIGHT_PLUGINS` entry with no matching entry point — a typo — which is otherwise almost
undiagnosable from inside the agent, the same gap the `follower_versions` work closed for version
skew.

## 7. Scenario participation

The heaviest seam: 62 terminal-mentioning lines in `scenarios_pool.py` alone, plus `terminal_pool`
threaded explicitly through eight method signatures.

### 7.1 Participant kind is registry-resolved

`_validate_participant_kind` today branches `if p.kind == "terminal": … elif p.kind not in
SUPPORTED_KINDS: raise`. It becomes: a browser engine name, or a kind registered by an enabled plugin
whose `create_scenario_adapter` returned an adapter, else the same error — now listing the enabled kinds, so a typo or a
disabled plugin is self-diagnosing.

### 7.2 `connector_type` leaves core

`Participant.connector_type` is a terminal-specific field in core's dataclass, and core's YAML parser
defaults it to `"pty"`. Both are removed. They are replaced by a free-form `options: dict` that core
passes through opaquely and the plugin validates.

**This changes the scenario YAML shape**: `connector_type: ssh` moves under `options:`. Accepted as a
break under the no-migration decision. It touches `examples/scenarios/browser-plus-terminal.yaml` and
any `demo/bundles/` scenario declaring a terminal participant.

Note that `resolve_participant` receives core's `Participant` dataclass, which makes its field set
part of the public plugin API. That is a real commitment: adding a field is safe, renaming or removing
one is a `plugin_api_version` bump.

### 7.3 `ScenarioAdapter`

A capability flag alone does not generalize anything, and this is the finding that most changed the
design. The existing skip sites are not merely permission checks — the code immediately after each one
is browser-specific:

```python
if p.get("kind") == "terminal":
    return {... "error": "terminal sessions do not support browser macros"}
session = browser_pool.get(p["instance_id"])           # scenarios_pool.py:422
await _macros.run_macro(session=session, name=macro, args=args or {})
```

and, in `wait_for_sync`, `session.wait_for` / `session.operation` / `session.page`. Replacing
`kind == "terminal"` with `"macros" not in supports` leaves that body unchanged, so a plugin that
declared `macros` would still be looked up in the browser pool.

So each kind supplies an adapter, built by a factory that receives the pool:

```python
class ScenarioAdapter(Protocol):
    """The mandatory floor. Everything else is a separate capability Protocol."""
    def resolve_participant(self, spec: Participant, persona: Persona | None) -> dict: ...

class SupportsMacros(Protocol):
    async def run_macro(self, instance_id: str, *, name: str, args: dict) -> None: ...

class SupportsSync(Protocol):
    async def wait_for_sync(self, instance_id: str, *, selector, text, url, timeout_ms) -> None: ...

class SupportsDialogPolicy(Protocol):
    async def set_dialog_policy(self, instance_id: str, policy: str) -> None: ...

class SupportsMockRoutes(Protocol):
    async def install_mock_routes(self, instance_id: str, routes: list) -> None: ...
```

The capabilities are **separate Protocols, not optional methods on one**. A `Protocol` that declares a
method requires it structurally, so a single combined Protocol with "optional" handlers is a
contradiction — terminal's adapter, implementing only `resolve_participant`, would not satisfy it.
Core narrows with `isinstance(adapter, SupportsMacros)` on runtime-checkable Protocols.

`create_scenario_adapter(pool)` is a factory rather than a descriptor attribute because the adapter
resolves instance IDs against its own pool, and the pool does not exist until `create_pool` has run
(load step 8, §6.3).

`resolve_participant` is mandatory and replaces `scenarios.resolve_terminal_launch(p)`. This is what
deletes core's `from octowright.terminal.connector_config import …`, and with it the reason that module
was carved out as "pure builders so core doesn't import uterm." The workaround stops being necessary
once the dependency inverts. SSH field resolution from a persona's freeform `app.ssh` block, and the
rule that **no SSH password is read from a scenario**, move into the plugin unchanged.

The adapters take an `instance_id`, not a session object, and resolve it against their own pool. That
is what removes `browser_pool` from eight `ScenarioPool` method signatures: core no longer needs to
know which pool a participant lives in, because the adapter does.

Browsers get a `BrowserScenarioAdapter` implementing all five, holding exactly the code that lives
inline in `scenarios_pool.py` today. Terminal's adapter implements `resolve_participant` and nothing
else.

### 7.4 The binary partition becomes a group-by

`start()` today partitions participants into `terminal_specs` / `browser_specs`. It becomes: group by
kind, look each kind's pool up in the registry, launch each group, reassemble in declaration order.
`_pool_for`, `_close_launched`, `_rollback_start`, `stop`, and `remap` already operate on `(pool, ids)`
pairs and simply iterate the registry.

### 7.5 Capabilities are derived, never declared

The vocabulary is closed and core-defined:

`{"macros", "sync", "dialog_policy", "mock_routes"}`

It is derived from the skip sites that already exist, not invented ahead of need. `fixtures` was in an
earlier draft and is **removed**: `_validate_fixtures` accepts exactly two keys, `dialog_policy` and
`mock_routes`, and `_apply_fixtures` does nothing but dispatch to those two. Keeping it as a
capability alongside its own constituents would mean either an undefined precedence between the
container and its parts, or a browser adapter applying the same fixture twice. Core keeps
`_apply_fixtures` as the dispatcher and calls the two capability handlers; the fixture *vocabulary* in
scenario YAML is unchanged. Core must know
what a capability means in order to skip it, so a plugin inventing one would declare something core
cannot act on. Adding a capability is a core change, and that is correct.

**Core computes the supported set from the adapter**, by checking which optional handlers the adapter
supplies. A plugin cannot claim `macros` without implementing `run_macro`, because the claim *is* the
implementation. This is the same derive-don't-mirror discipline that made `RECORDER_NOISE` derived
rather than hand-mirrored, and that removed the hardcoded `timeout_ms` literal from
`lint_fields._click_or_fill_allowed`.

The existing validation error ("terminal participant cannot declare `startup_macros`") stops being a
special case and becomes "a kind whose adapter has no `run_macro` cannot declare `startup_macros`",
covering every future plugin for free.

Roles are untouched. `_validate_scenario` keeps logging `scenario.unknown_role`; plugins get no say in
role vocabulary.

## 8. Dashboard and HTTP

### 8.1 Live list

`_live_summary` is already getattr-defensive and, per its own comment, terminal sessions "serialize
through it cleanly." It iterates the registry instead of `browser_pool` plus one nullable, and it is
now the *only* serializer — dropping `list_sessions` from the pool contract (§4.3) removes the
duplicate.

### 8.2 Detail

`_terminal_session_detail` moves to the plugin as `session_detail(session)`. Core dispatches by kind
through the registry, falling back to the browser detail builder. The existing short-circuit before
the browser builder is preserved; it stops being a hardcoded branch. A plugin raising here is caught
and rendered as a degraded detail rather than a 500.

Committed Tier-2 artifacts, read from the recording's `artifact_registered` rows, appear in the detail
payload so the dashboard can link them.

### 8.3 Close

`_maybe_close_terminal` generalizes to registry iteration. `ProtectedTerminalCloseError` becomes a core
`ProtectedSessionCloseError` that plugins raise — this removes `http/routes/sessions.py`'s direct
`from octowright.terminal.errors import …`, the single import that most clearly inverts under this
design. The 409-on-protected-without-force mapping is unchanged, and `CloseResult` (§4.3) gives the
route one response shape across kinds.

### 8.4 Closed-recording discovery must not need the plugin

`_read_first_opening` hardcodes `("launch", "terminal_start")` and `_summarise_recording` hardcodes the
terminal branch. Making that registry-driven would be wrong, because recordings outlive plugins.

Since core owns the launch transaction (§5.1), core writes the opening row itself: a `session_start`
row carrying `kind`, `label`, and `profile`, written before control passes to the plugin. Discovery
reads `kind` off that row and never needs to know what kinds exist.

The precise cost of *not* doing this is worth stating accurately, because an earlier draft overstated
it. `_summarise_recording` still returns a summary whenever the filename parses, and cleanup walks the
tree by `rglob`, so an uninstalled plugin's recordings remain **listed and prunable**. What is lost
without a uniform opening row is the kind itself — the summary degrades to `kind: "unknown"` with
`label`, `profile`, and `url` all `None` — and with the kind goes renderer selection and any
kind-specific artifact association. That is the real argument, and it is sufficient.

Browser recordings still open with `launch`, so discovery recognizes both shapes. That asymmetry is
accepted rather than rewriting the browser recording format, which would break every existing
recording, export, and golden.

### 8.5 Plugin asset serving

`GET /plugins/{name}/{path}` where `{name}` is the entry-point name (§4.2), path-contained against the
plugin's declared `asset_dir` with symlinks resolved before the prefix check — the same discipline as
`RECORDINGS_DIR`.

Gated like the **static SPA mount**, not like the session APIs: behind the Host/Origin
`SensitiveASGIGuard`, not behind dashboard pairing. These are static assets from an operator-enabled
package, they contain no session data, and the dashboard shell must boot before pairing completes.

### 8.6 Frontend renderer contract

`GET /api/plugins` returns `kind → {moduleUrl, rendererApiVersion, displayName, layout}`.

`session.ts` replaces `if (detail.kind === "terminal")` with a registry lookup and a dynamic import of
`moduleUrl` — the lazy-chunk pattern already at `session.ts:665`, now data-driven.

**Core owns the page chrome; the plugin fills one pane.** `bootTerminalSession` today owns the whole
page but almost nothing in it is terminal-specific: it builds a header / slot / timeline / footer
layout and then calls back into core for `renderHeader`, `renderFooter`,
`installDashboardAuthRequiredNotice`, `renderTimeline`, `appendTimelineEvents`, `openTail`,
`getEvents`, and `tailWebSocketUrl`. The only plugin-owned code is `mountTerminalView` and
`view.feedEvents(...)`.

So core renders the chrome and does the `getEvents` → `renderTimeline` → `openTail` →
`appendTimelineEvents` wiring itself. The plugin implements one function:

```ts
export function mountStream(
  el: HTMLElement,
  ctx: StreamContext,
): StreamHandle | Promise<StreamHandle>;

interface StreamHandle {
  feed(events: SessionEvent[]): void;
  destroy(): void;
}
```

Contract details, stated because a third party cannot read them off core's source:

- `mountStream` may be async; core awaits it before the first `feed`.
- `feed` receives **batches** in JSONL order. Historical events are fed before any live event.
- Delivery is **at-least-once**: a `/tail` reconnect may replay a batch. A renderer must tolerate a
  repeat, which is why the terminal view's `reset: true` delta semantics survive unchanged.
- `destroy` is idempotent and is always called on teardown, once a handle has been obtained. A
  `mountStream` that rejects yields no handle and so has nothing to destroy; core switches that pane
  to the fallback renderer instead.
- An exception thrown from `mountStream` or `feed` is caught by core and switches the pane to the
  fallback renderer with a visible reason (§8.7).
- A plugin ships its own CSS alongside its module and scopes selectors under the mount element. Core
  does not enforce this — same-origin trusted JS could restyle anything (§9) — but an unscoped
  stylesheet is the most likely accidental way to break a page the plugin does not own.
- Core publishes `octowright-plugin.d.ts` with `StreamContext`, `SessionEvent`, and `StreamHandle`, so
  a third party builds against types rather than a prose sketch.

This shrinks the frontend surface from eight exported functions to one, and the plugin never touches
the WebSocket, the cursor protocol, or the auth notice. `layout` selects between core's browser page
and the slim stream page — closed vocabulary, same approach as §7.5; today there are exactly two
values.

### 8.7 Renderer versioning and fallback

`renderer_api_version` is checked by the **dashboard**, against the version core's SPA implements.
This is deliberately separate from `plugin_api_version`, which the loader checks. Collapsing them makes
the frontend mismatch path unreachable: a version-mismatched plugin would be refused at load, never
reach `/api/plugins`, and the fallback's "visible reason" would never render. Independent versions also
match reality — a backend contract change and a renderer contract change have no reason to move
together, and a plugin should not be refused wholesale because its UI is a version behind.

Core ships a generic action-timeline plus raw-JSONL view, used when a kind has no frontend, when
`rendererApiVersion` mismatches, or when the module import or mount fails. Every case renders **with a
visible reason** — a blank panel with a console error is the worst possible failure for something a
third party built.

### 8.8 Types

`connector_type` and the `"telnet"` member of the kind union leave `types.ts` with the plugin. Core's
`SessionSummary` keeps a free-form `extra` for kind-specific fields.

## 9. Trust model

An enabled plugin runs Python **inside the leader process**. It can already drive browsers, read the
`0600` lockfile, mint its own pairing code, and read every recording. Its JavaScript holding the
dashboard bearer is therefore strictly *weaker* than what its Python half already holds.

The trust decision is concentrated at **enable** (§6.2). After that, sandboxing the UI would defend
against nothing, so there is no iframe isolation and no separate origin. Plugin JS is served
same-origin and imported directly.

This is why explicit enable is required rather than auto-loading discovered entry points: installing a
package — including a transitive dependency — must not silently extend a browser-driving daemon.

**The honest limit on failure isolation.** Ordinary exceptions raised during plugin resolution,
validation, initialization, and core-routed plugin operations are isolated and reported, because core
retains control at those points. Enabled plugins otherwise share the leader's process and trust
boundary: a hung import or `create_pool`, an `os._exit`, a native extension crash, unbounded
allocation, event-loop blocking, global monkeypatching, or deliberate reading and mutation of core
state are **not** isolated. Stronger isolation would require a subprocess or RPC boundary, which is a
materially different design and out of scope (§3).

By the same token, core-owned chrome (§8.6) *minimizes accidental* layout drift; it does not make it
impossible. Same-origin trusted JS can walk to an ancestor, inject global CSS, or replace core's DOM.
That is consistent with the trust model, and the mitigation is enable, not architecture.

## 10. What moves, and the honest accounting

Measured on the current tree:

| Leaves core | LOC |
|---|---|
| `src/octowright/terminal/` + `src/octowright/server/terminal/` | 937 |
| `tests/terminal/` + `tests/test_terminal_supervision.py` | 1,262 |
| `terminal-view.ts`, `session-terminal.ts` and their tests | 531 |
| terminal-specific lines across 13 core files | 187 |
| terminal prose in `AGENTS.md`, `README.md`, `docs/getting-started.md`, the tool inventory, and the shared contract | substantial |

Core **gains**, revised upward after review — the correctness fixes in §5.1, §6.3, §6.4, §7.3 and §8.7
are all *more* core machinery, because each moves a guarantee from the plugin author to core:

| Core gains | LOC |
|---|---|
| Contract Protocols and `TypedDict`s, including the four capability Protocols | ~140 |
| Registry, loader, load transaction, delta rollback, profile bootstrap | ~270 |
| `begin_session`, failed-launch rule, `record_control`, ID-uniqueness check | ~140 |
| `ctx.artifact` reserve/commit plus containment | ~110 |
| `BrowserScenarioAdapter` (relocated inline code), adapter factory and dispatch | ~200 |
| `/api/plugins`, asset serving | ~80 |
| Frontend registry, `mountStream` host, fallback renderer, `.d.ts` | ~160 TS |
| Reference plugin and contract tests | ~290 |

Doing the arithmetic honestly, source for source: 1,124 Python lines leave core (937 + 187) and
roughly 940 non-test lines arrive, a saving **under 200 Python lines**. The first draft claimed ~500;
two rounds of review have taken it down by more than half, because every correctness fix moved work
*into* core — which is the point, but it is also the cost. TypeScript saves about 370 (531 out, 160
in). Tests look better on paper — 1,262 out, ~290 in — but that is a swap of terminal-specific tests
for contract tests, not a reduction in what is verified.

**Assume the Python saving is zero.** The remaining margin is inside the error bars of these
estimates, and a third round of review that finds one more unenforced guarantee would erase it. Line
count is not the case for this design; if it were, the honest recommendation would be to stop.

The case is that core stops knowing a terminal exists: a change to scenarios, session detail,
discovery, or close reasons about a registry instead of a second hardcoded kind, third parties get a
targetable API, and `provide-uterm`'s release schedule stops gating octowright's. The `AGENTS.md`
reduction is disproportionately valuable because that file is read on every task.

**This is the decision to weigh before Step 1**, and it is a genuine trade. A smaller design — one
that kept capabilities as declared strings, the recorder as a handed-out object, and rollback by
declared name — would save real lines. It would also ship four guarantees it could not enforce, which
is how the first draft read before review.

### The uterm caveat

Extraction does **not** make terminal installable. `provide-uterm` and `provide-uterm-server` both
return 404 from PyPI, so `octowright-terminal` will be exactly as uninstallable as
`octowright[terminal]` is today. What changes is that the blocker moves *out of octowright's release
path*: octowright ships complete, and uterm's schedule stops being octowright's problem. If
installability is the goal, publishing uterm is a separate task and this design does not substitute
for it.

## 11. Testing

### 11.1 The reference plugin

A fake session kind under `tests/`, exercising every seam that exists at its build step (§12): its own
pool, MCP tools, a partial scenario adapter, session detail, protected close, a Tier-2 artifact, and —
from step 4 — a frontend `mountStream` module.

The failure mode to design against is the reference plugin decaying into a toy that passes while
representing nothing. Two guards:

1. It supplies a **partial** adapter, so the capability-skip paths are exercised rather than only the
   happy path.
2. A contract test asserts the reference plugin covers every member of the capability vocabulary
   (implemented or deliberately omitted, with the omission asserted) and every `SessionPool` method —
   so adding a capability without covering it fails CI.

### 11.2 Specific test obligations

- `ctx.artifact`: `..` traversal, absolute paths, symlinked parent, symlink resolution before the
  prefix check, and a committed row that resolves back to a contained path on read.
- **Launch transaction**: a plugin launch that raises after `begin_session` leaves no
  opening-row-only recording; a launch that raises *after* recording an action keeps the partial
  recording; a cancelled launch behaves as a failed one; a `commit` whose record carries a different
  recorder, id, or log path than the transaction issued is refused.
- **Control rows**: with `OCTOWRIGHT_RECORDING_MAX_BYTES` set smaller than a `session_start` row, the
  row is still written and discovery still classifies; an artifact committed after the action stream
  truncated still yields a readable `artifact_registered` row; a commit that would exceed the control
  budget fails visibly rather than reporting success.
- **ID uniqueness**: a commit whose `instance_id` already exists in another registered pool is
  refused, and the transaction rolls back as a failed launch.
- **Tool collision**: a plugin declaring a core tool name is refused at validation, before import.
- **Partial registration rollback**: a `tool_module` that registers one tool then raises leaves zero
  of its tools registered and no pool in the registry; a module that registers an **undeclared** tool
  and then raises leaves that tool unregistered too (the delta, not the declaration, is what unwinds).
- **Profile bootstrap**: a plugin's tools register under a narrow `OCTOWRIGHT_PROFILE` naming that
  plugin's profile, **and** neither `octowright.profile.unknown` nor `octowright.profile.all_unknown`
  fires for it.
- **Capability derivation**: an adapter implementing only `resolve_participant` type-checks, yields a
  kind whose participant cannot declare `startup_macros`, and core never calls a missing handler.
- **Adapter binding**: the adapter core uses resolves instance IDs against the pool from the same
  plugin's `create_pool`.
- `plugin_api_version` and `renderer_api_version`: a test ties each constant to its contract's shape,
  so a contract change that forgets to bump it fails.
- **Discovery**: a recording written by a plugin that is **no longer installed** still classifies,
  lists, and prunes correctly.
- **Failure isolation**: a plugin raising at each of resolve / validate / tool-import / `create_pool`
  leaves the daemon healthy and reports `state: failed` with a reason.
- **Observability**: a disabled plugin reports without being imported (asserted by a descriptor whose
  module records import at module scope); an `OCTOWRIGHT_PLUGINS` typo reports `state: missing`.
- **Enable scope**: a `.octowright/config.yaml` naming a plugin does **not** load it.
- **Frontend**: a `rendererApiVersion` mismatch, a failed module import, and a throwing `feed` each
  render the fallback with a visible reason, not a blank pane.

## 12. Build order

Sequencing only — there is no migration and no compatibility window. Each step is its own
implementation plan, and each step's reference-plugin coverage is limited to the seams that step
actually inverts. An earlier draft had step 1's reference plugin exercising every seam, which is
impossible while those seams are still hardcoded.

1. **Contract, loader, and the launch transaction.** Protocols, `TypedDict`s, identity and namespace
   validation, entry-point discovery, enable resolution, profile bootstrap, tool-collision checking
   and delta rollback, status reporting — plus `begin_session`, `record_control`, the `session_start`
   row, and the failed-launch rule. A **backend-only** reference plugin: pool, tools, protected close.
   The launch transaction lands here rather than in step 2 because a reference pool that cannot launch
   a session cannot demonstrate protected close, and a pool built against a temporary
   launch-and-recorder shape would be rewritten in step 2 anyway. CI green with terminal still on its
   existing path.
2. **Core-owned artifacts and dashboard.** `ctx.artifact` reserve/commit and its control row,
   registry-driven session list, detail, and close, shutdown teardown. Reference plugin grows a
   Tier-2 artifact.
3. **Scenarios.** `ScenarioAdapter`, `BrowserScenarioAdapter`, derived capabilities, the group-by
   partition, `options:` replacing `connector_type`, and the example/demo YAML updates. Reference
   plugin grows a partial adapter.
4. **Frontend contract.** `/api/plugins`, asset serving, `mountStream`, core-owned chrome, fallback
   renderer, `.d.ts`. Reference plugin grows a renderer.
5. **Extract.** Build `octowright-terminal` against the landed contract, prove it passes the same
   contract suite the reference plugin does, then delete terminal from core.

Step 5 deletes nothing until the external plugin passes. That ordering is the point: if the contract
is inadequate, it is discovered while the working implementation is still in the tree.

## 13. Risks

| # | Risk | Mitigation |
|---|---|---|
| 1 | There is effectively no LOC saving; the payoff is structural only | Arithmetic done plainly in §10 as a decision to weigh, not buried |
| 2 | `ctx.artifact` containment bugs reopen a closed bug class | Full `reject_unsafe_path` test battery (§5.2, §11.2) |
| 3 | Either API version is only useful if core actually bumps it | A test ties each constant to its contract shape (§11.2) |
| 4 | Reference plugin decays into a toy | Partial adapter + coverage assertion (§11.1) |
| 5 | Tool-rollback depends on the MCP SDK's private tool mapping | Narrow, deliberate, pinned by a test so an SDK change fails loudly (§6.4) |
| 6 | A plugin partially loads and answers calls it cannot serve | Load transaction with rollback; commit is the last step (§6.3) |
| 7 | Scenario-YAML break reaches examples and demo bundles | Enumerated in §7.2; updated in step 3 |
| 8 | Third-party frontend drifts from core's chrome | Core renders the chrome, minimizing *accidental* drift; deliberate drift is a trust-model consequence, not a bug (§9) |
| 9 | Plugin API rots with no in-repo consumer | Reference plugin is that consumer, grown per build step (§11.1, §12) |
| 10 | An in-process plugin hangs or crashes the leader | Not mitigated. Accepted and documented (§9); mitigation would require a process boundary |
| 11 | The byte ceiling silently drops core metadata rows | Control rows bypass the ceiling on a separate bounded budget (§5.3) |
| 12 | Core and a pool disagree about which sessions exist | The pool is the single registry; commit validates rather than duplicating (§4.3) |
