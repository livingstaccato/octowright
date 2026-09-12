# Session-kind plugins

Octowright core drives one kind of session: browsers. Every other kind (terminals
today) comes from a **session-kind plugin**, a separate Python distribution that
core discovers through an entry point and loads only when an operator enables
it. This page is for plugin authors. The reference implementation is the
terminal plugin in `packages/octowright-terminal`. Operators who only want to
enable a plugin need `OCTOWRIGHT_PLUGINS` in [env-vars.md](env-vars.md).

The contract itself lives in code: `octowright.plugins.contract` for the backend
Protocols, and `packages/octowright-frontend/src/plugin-contract.d.ts` for the
dashboard renderer. This page explains how core uses them.

## Discovery and enablement

Declare the descriptor object in the `octowright.session_kinds` entry-point
group:

```toml
[project.entry-points."octowright.session_kinds"]
mykind = "my_plugin.plugin:plugin"
```

- Core lists the group without importing anything. The entry-point name is the
  plugin's operator-facing name: up to 64 lowercase letters, digits, `_` and
  `-`, starting with a letter.
- When two installed distributions declare the same name, neither loads. The
  conflict is reported and every other plugin still loads.
- Installing a plugin does not enable it. The daemon loads the names listed in
  `OCTOWRIGHT_PLUGINS` (comma-separated) or, when that is unset, in the
  `plugins:` list of `plugins.yaml` in the user config directory. Enablement is
  daemon-wide, never read from a project's `.octowright/config.yaml`.
- `octowright_status()` returns a `plugins` list with one row per plugin core
  knows about: `name`, `distribution`, `version`, `entry_point` and `state`.
  The state is `enabled`, `failed` (with the reason), `disabled` (installed but
  not enabled) or `missing` (enabled but not installed).

## The descriptor

The entry point resolves to an object satisfying `SessionKindPlugin`:

| Member | Meaning |
|---|---|
| `kind` | The session kind, such as `terminal`. Up to 64 lowercase letters, digits and `_`, starting with a letter. No hyphen: the kind is part of recording filenames that core splits on `-`. Core's own kinds (`chromium`, `firefox`, `webkit`, `browser`, `unknown`, `session`) are reserved. |
| `display_name` | The name the dashboard shows. |
| `plugin_api_version` | The backend contract version the plugin was written against. It must equal core's `PLUGIN_API_VERSION`. |
| `tool_names` | Every MCP tool the plugin registers. Each must start with `{kind}_`. |
| `tool_module` | The module core imports to register those tools, or `None`. |
| `profile_name` | A capability profile holding the plugin's tools, or `None`. It may not reuse a core profile name. |
| `frontend` | A `FrontendAsset` describing the dashboard renderer, or `None`. |
| `create_pool(ctx)` | Builds the kind's `SessionPool` from a `PluginContext`. |
| `create_scenario_adapter(pool)` | Builds the scenario adapter, or returns `None` if the kind cannot join scenarios. |
| `session_detail(session)` | Kind-specific fields for the dashboard's session detail. |

Write `plugin_api_version` as a literal rather than importing core's constant.
Importing it makes every plugin agree with every core by construction, which
turns the check off; the terminal plugin has a test that fails when its literal
and core's constant diverge, so a core bump forces a deliberate decision.

### What core checks, and when

1. **Before calling into the plugin**, core validates the descriptor's metadata:
   the API version, the kind's syntax and reservation, the tool-name prefix,
   and collisions with a kind or tool that core or another enabled plugin
   already claims.
2. **At activation**, after core's own tools are registered:
   - a tool name that collides with an already-registered tool refuses the
     plugin;
   - `tool_module` is imported. Tools register as an import side effect with
     core's `mcp` instance (`from octowright.server._state import mcp`), the
     way core's own tool modules do;
   - a registered tool missing from `tool_names` refuses the plugin. A declared
     tool that did not register is allowed, because the active capability
     profile may have filtered it;
   - `create_pool(ctx)` runs, then `create_scenario_adapter(pool)`, and the
     adapter is checked against the scenario contract described below.
3. **Any failure during activation** removes the tools the module registered,
   abandons the pool, unregisters the plugin's profile and records the plugin as
   `failed` with the reason. Other plugins are unaffected.

## Pools and sessions

Core keeps no session table of its own. A plugin's `SessionPool` is the only
registry for its kind; the session list, detail pages and close all iterate the
registered pools.

- `launch(**kwargs) -> LaunchResult` opens a session; see the launch
  transaction below.
- `get(instance_id)` returns the session or raises `KeyError`. `maybe_get`
  returns `None` instead.
- `iter_sessions()` iterates a snapshot, because core iterates while other tasks
  may be launching.
- `close(instance_id, *, force=False)` raises `KeyError` for an unknown id, and
  `ProtectedSessionCloseError` for a protected session closed without `force`.
- `close_all(*, force=False)` closes every session, continues past an individual
  failure, and raises an aggregate at the end if any close failed.

Each session satisfies `SessionRecord`: `instance_id`, `kind`, `label`,
`profile`, `url`, `recorder`, `log_path`, `protected` and a free-form `extra`
map. Instance ids must be unique across all pools, not only within one.

## The launch transaction

A plugin never opens a recording itself. The `PluginContext` handed to
`create_pool` provides a core-owned transaction:

```python
async def launch(self, *, label=None, profile=None, **options):
    async with self._ctx.begin_session(instance_id=uuid4().hex[:12], label=label, profile=profile) as launch:
        session = MySession(
            instance_id=launch.instance_id,
            kind=launch.kind,
            recorder=launch.recorder,
            log_path=launch.log_path,
            label=label,
            profile=profile,
            # ... and the other SessionRecord fields
        )
        await session.start()
        try:
            result = launch.commit(session)
        except BaseException:
            await session.stop()
            raise
    self._sessions[session.instance_id] = session
    return result
```

- `begin_session` validates the id (up to 64 lowercase letters, digits and `_`,
  no hyphen), creates the recording under core's recordings directory with
  core's file permissions, containment and size ceiling, and writes the opening
  `session_start` row. It takes no `kind`, so a recording is always stamped with
  the descriptor's validated kind.
- `commit(record)` checks that the record is the one this transaction issued
  (same id, kind, recorder object and log path) and that no other pool holds the
  id, raising `SessionIdInUseError` if one does. It returns the `LaunchResult`.
  A record's `extra` map is copied into `LaunchResult["extra"]`, the only way to
  add a kind-specific field to the result.
- Raising inside the block, or leaving it without committing, deletes the
  recording if it holds nothing but `session_start`. A recording with anything
  more is kept for diagnosis. The transaction cannot stop what the plugin
  started, so stop it yourself if `commit` raises, as above.
- Write rows through `launch.recorder`. Control rows have their own budget, and
  exceeding it raises `ControlBudgetExceededError` rather than dropping the row.
- For input redaction, call `ctx.redaction_mode()`. It returns the resolved
  `OCTOWRIGHT_REDACT_INPUTS` policy (`off`, `passwords` or `all`); do not read
  the environment directly.

## Side artifacts

For files other than the recording, reserve a path from core rather than
composing one:

```python
handle = self._ctx.artifact(session, "transcript", ".txt")
handle.path.write_text(text)
handle.commit(mime_type="text/plain")
```

- The artifact id is up to 64 lowercase letters, digits, `_` and `-`, starting
  with a letter. The suffix is a real extension such as `.txt`.
- Core creates and locks the session's directory under `session-artifacts/`
  before returning the path.
- `commit` requires a MIME type from a closed allowlist: `text/plain`,
  `text/csv`, `application/json`, `application/zip`,
  `application/octet-stream`, `image/png`, `image/jpeg`, `image/svg+xml`,
  `video/webm` and `video/mp4`. It writes an `artifact_registered` row into the
  session's recording, so the artifact stays discoverable after the session
  closes, the daemon restarts, or the plugin is uninstalled.
- The dashboard lists artifacts by id and MIME type and serves them as
  downloads, never inline.
- A reserved path that is never committed is referenced by nothing and is
  removed by ordinary age-based cleanup.

## Scenarios

`create_scenario_adapter` returns `None` for a kind that cannot join scenarios,
or an adapter implementing `resolve_participant(spec, persona) -> dict`. Core
calls it for each participant of the kind and passes the returned dict to
`pool.launch(**kwargs)`. `spec.options` is opaque to core, so validate it there.
`persona` is the participant's loaded persona, or `None`.

Capabilities come from the methods an adapter implements; a plugin cannot
declare one:

| Capability | Method |
|---|---|
| `macros` | `async run_macro(instance_id, *, name, args)` |
| `sync` | `async wait_for_sync(instance_id, *, selector, text, url, timeout_ms)` |
| `dialog_policy` | `async set_dialog_policy(instance_id, policy)` |
| `mock_routes` | `async install_mock_routes(instance_id, routes)` |

A participant that needs a capability its kind lacks is refused: the terminal
adapter has no `run_macro`, so a terminal participant with `startup_macros`
fails validation.

Before registering an adapter, core binds each implemented method's declared
call shape against the implementation. A method that is not `async`, or that
rejects a keyword core passes, fails activation instead of failing partway
through someone's scenario. Positional parameters may be renamed; keyword-only
parameters may not.

## Dashboard

### Session detail

`session_detail(session)` returns kind-specific fields. Core merges them over
the summary every session gets (start time, live and protected state, event
counts, log path and so on), and the plugin's fields win on conflict. An
exception in either half degrades to a partial payload rather than an error
page. The terminal plugin reports browser-only fields such as `video_path` as
`None` rather than omitting them, so the payload keeps one shape across kinds.

### Renderer

```python
frontend = FrontendAsset(renderer_api_version=1, asset_dir=ASSET_DIR, module_path="renderer.js", layout="stream")
```

- Core serves files from `asset_dir` at `/plugins/<entry-point name>/<path>`,
  contained to that directory and limited to `.js`, `.mjs`, `.css`, `.map`,
  `.woff2`, `.svg` and `.png`. These routes do not require pairing, so ship only
  static code and assets.
- `/api/plugins` gives the dashboard each kind's module URL, renderer version,
  display name and layout.
- The module exports `mountStream(el, ctx)`, returning a `StreamHandle` or a
  promise of one, as typed in `plugin-contract.d.ts`. Core owns the page chrome,
  the WebSocket and the cursor. The renderer receives batches of recorded rows
  through `feed(events)`: history first, then live rows, delivered at least
  once, so a repeated batch must be tolerated. Core calls `destroy()` on
  teardown.
- Only `layout: "stream"` is hosted. A module that fails to load, one with no
  `mountStream` export, a `renderer_api_version` the dashboard does not
  implement, or any other layout shows core's fallback renderer with the reason.
- Ship the module prebuilt. A Python wheel has no npm step at install time,
  which is why the terminal plugin commits its built renderer.

## Versioning

The two contract versions move independently, and both are currently `1`:

- `PLUGIN_API_VERSION` covers the backend Protocols. A mismatch refuses the
  plugin at load.
- `RENDERER_API_VERSION` covers the dashboard renderer. A mismatch leaves the
  plugin loaded, with its tools, pool and scenario support working, and the
  dashboard shows the fallback renderer for its sessions.
