# octowright-terminal

The terminal session-kind plugin for [octowright](https://github.com/livingstaccato/octowright). Drives a local PTY shell, an SSH session, or a telnet connection in-process, recorded to the same JSONL format octowright uses for browsers, and exposed to an MCP client as `terminal_*` tools.

This package is entirely optional. Octowright core has no terminal-specific code left anywhere in `src/octowright` — no `terminal/` package, no `provide.uterm` import, no hardcoded scenario branch. Everything here reaches core through exactly one seam: the `octowright.session_kinds` entry point (`octowright_terminal.plugin:plugin`, declared in this package's `pyproject.toml`). See `AGENTS.md` in the octowright repo root ("Terminal Sessions (plugin)") for the short version and how `OCTOWRIGHT_PLUGINS` resolves which plugins a daemon loads.

## Installation

This package is **not on PyPI yet** — `uv pip install octowright-terminal` still answers "No matching distribution found". Its dependencies are all there: `provide-uterm`, `provide-uterm-platform` and `provide-uterm-server` were published on 2026-08-26, so nothing here needs a sibling `../provide-uterm` checkout any more (the `[tool.uv.sources]` path overrides that once required one are gone from the octowright repo's `pyproject.toml`).

The release path is wired: the octowright repo's `release.yml` builds this package into its own `dist-terminal/` and publishes it alongside core from the same GitHub Release, so the next release is its first upload. One thing gates that, and it is not something the repo can do for itself — PyPI needs a trusted publisher for the `octowright-terminal` project, registered as a *pending publisher* because the name has never been uploaded. Until it lands, install from the repo:

```bash
uv pip install ./packages/octowright-terminal   # from an octowright checkout
```

**Versions here move independently of core's.** This package is at 0.1.0 while core is at 0.17.0, because locking them would force a plugin release on every core release even when nothing here changed. The practical consequence is that most core releases re-present a plugin version the index already has, which is why the plugin's publish steps set `skip-existing` and core's do not.

It needs a core carrying the plugin machinery (`octowright.plugins`). **0.17.0 is the first release that does** — the published wheel contains `octowright/plugins/`. That floor is declared here as `octowright>=0.17.0`, so an older core fails at resolve time with a readable error instead of installing cleanly and then dying at daemon start with `ModuleNotFoundError: No module named 'octowright.plugins'`.

From that checkout, this package is a `[tool.uv.workspace]` member and installs in editable mode from the `terminal` dependency group — deliberately its own group rather than part of `dev`, so core stays uterm-free unless asked:

```bash
uv sync --all-groups        # or: uv sync --group terminal   (make install does the former)
```

A plain `uv sync` (default groups) does more than skip it: a sync is exact, so on a checkout that already has the group it **uninstalls** the plugin and its uterm tree again. `make test-terminal` then refuses to run rather than passing over zero tests — re-sync the group.

Installing the distribution only makes the plugin *discoverable*. Enable it at daemon start with:

```bash
OCTOWRIGHT_PLUGINS=terminal uv run octowright serve
```

Without that env var (or an equivalent `plugins:` entry in `~/.config/octowright/plugins.yaml`), the plugin is merely *discoverable* — `octowright_status()["plugins"]` lists it, but no `terminal_*` tool registers and no terminal pool exists. That is deliberate: installing a distribution that declares this entry point must not silently extend a browser-driving daemon.

## Architecture

A terminal session drives one `provide-uterm` `SessionConnector` **in-process** — no hub, no WebSocket. `TerminalEngine` (`engine.py`) runs a poll loop that pumps `connector.poll_messages()`, translates each cumulative screen snapshot into a JSONL delta via `MessageTranslator` (`translate.py`), and appends actions to the recording in the same format browsers use: `terminal_start` / `terminal_input` / `terminal_output` / `terminal_stop`. `TerminalSession` (`session.py`) carries `kind="terminal"` and a `connector_type` of `pty`, `ssh`, or `telnet`. `TerminalPool` (`pool.py`) mirrors `BrowserPool`'s surface (`launch`/`get`/`maybe_get`/`iter_sessions`/`list_sessions`/`close`/`close_all`) and conforms to core's `SessionPool` protocol, opening every launch through `ctx.begin_session` so the 0600/0700 recording guarantees, the recording byte ceiling, and cross-pool instance-id uniqueness are structural rather than something this package has to remember to do itself.

The connector's cumulative screen buffer caps at roughly 32KB and front-truncates; `MessageTranslator` distinguishes a **cap slide** (the buffer grew past the cap — emit just the new tail, consumer keeps appending) from a **reset** (`connector.clear()` emptied the buffer — emit `reset: true` plus the full new buffer, consumer must clear its display first). A program emitting `\x1b[2J` is *not* a reset in this sense — it's ordinary appended bytes the renderer's own terminal emulation executes.

## Tools

Seven MCP tools, registered only when the plugin is enabled. They form the `terminals` capability profile (declared by this plugin, not by core).

| Tool | Purpose |
|---|---|
| `terminal_launch` | Launch a session and start recording. `kind="pty"` (default), `"ssh"`, or `"telnet"`; returns `instance_id` for the other tools. |
| `terminal_send_input` | Send text (e.g. a command + `"\n"`) to a session. `password=True` marks the send as credential-bearing for redaction. |
| `terminal_snapshot` | Current screen text + cursor position. |
| `terminal_read` | Current screen text only. |
| `terminal_wait_for` | Block until a regex (`prompt=`) or substring (`text=`) appears on screen, or `timeout` elapses. |
| `terminal_close` | Close a session. Honors `protected` exactly like a browser close — refuses without `force=True`, raising `ProtectedTerminalCloseError`. |
| `terminal_list` | List live terminal sessions. |

### PTY (`kind="pty"`)

Forks a local shell. `command=` (default `/bin/bash`), `cols`/`rows` (default 80×24) size the PTY.

### SSH (`kind="ssh"`)

Args: `host`, `port` (default 22, overridable via `OCTOWRIGHT_SSH_PORT`), `user`, `key_path`, `password`, `known_hosts`, `insecure_no_host_check`. These map to the uterm SSH connector's config keys (`host`/`port`/`username`/`client_key_path`/`password`/`known_hosts`/`insecure_no_host_check`). The connector **requires `known_hosts`** unless `insecure_no_host_check=true`; a missing value surfaces as a clean `{"ok": false, "error": ...}` rather than an exception (the connector raises `ValueError` synchronously in `build_connector`, caught in `terminal_launch`). Passwords are accepted only as a live argument and never persisted. The SSH connector fixes its own remote PTY size and rejects unknown config keys, so `cols`/`rows`/`command` are PTY-only and never sent to it. <!-- pragma: allowlist secret (arg-name prose, not a credential) -->

### Telnet (`kind="telnet"`)

Args: `host`, `port` (default 23). Uses `TelnetSessionConnector` from `provide-uterm-server`. Performs full RFC 854 IAC negotiation (NAWS, TTYPE) and decodes incoming bytes as **CP437** — the encoding most BBS servers use — so box-drawing art and ANSI color codes render correctly. The connector hardcodes 80×25 terminal geometry (standard BBS size); `cols`/`rows`/`command` are ignored. `wait_for` and `snapshot` operate on the raw (CP437-decoded, ANSI-preserved) byte stream rather than a rendered grid, so contiguous ASCII prompt text matches reliably while cursor-addressed char-by-char draws do not. **Telnet is not supported as a scenario participant** — only `pty` and `ssh` are (see Scenario participants below).

## Input redaction

Reuses core's `OCTOWRIGHT_REDACT_INPUTS` policy (`off` / `passwords` default / `all`). The connector always receives the real bytes typed; only the recorded `terminal_input` value in the JSONL is masked. Masking triggers when the screen is at a detected password prompt (a `password`/`passphrase` prompt regex, mirroring the uterm hosted-runtime's own detection) or when the caller passed `password=True` to `terminal_send_input`. Under `all`, every send is masked regardless.

## Dashboard

The plugin ships its own dashboard renderer (`assets/renderer.js`, a self-contained bundle with `@xterm/xterm` plus `addon-web-links` and `addon-unicode11` inlined) rather than reaching into core's frontend bundle — core's own SPA has no xterm dependency at all. Core serves it generically: `octowright.plugins.contract.FrontendAsset` (declared on `TerminalPlugin`) points at the committed asset directory, `http/routes/plugin_assets.py` serves it as a static file addressed by the plugin's entry-point name, and the dashboard's `session.ts` resolves any non-core session `kind` through a generic plugin registry (`plugin-registry.ts`) rather than importing a specific plugin's renderer — so a plugin's renderer bundle never lands in core's own SPA chunk. `TerminalPlugin.session_detail` supplies terminal's own fields (`connector_type`, plus explicit `None`s for the browser-only fields core's uniform session-detail payload expects) merged under core's shared `_live_summary` base.

The renderer is output-only: it never sends keystrokes and does not echo recorded `terminal_input` back onto the screen (typed input stays visible in the action timeline instead). It writes each `terminal_output.data` delta verbatim — ANSI escapes and all — and lets xterm.js do its own emulation.

To rebuild the renderer after editing `assets-src/src/renderer.ts`, see `assets-src/README.md`.

## Child-exit EOF

The poll loop ends a session with `terminal_stop` reason `"eof"` when `connector.is_connected()` flips. The PTY connector's master fd is non-blocking, so a `b""` read is a true EOF: Linux raises `EIO`, macOS returns `b""`, and the connector flips `_connected` on either, so EOF detection is cross-platform. An unexpected poll-loop death (any exception other than a clean return or a `stop()`-driven cancellation) is recorded as reason `"error"` instead, so a `poll_messages()` or recorder failure doesn't vanish silently.

## Scenario participants

A terminal participant is `kind: terminal, options: {connector_type: pty/ssh, ...}` — a `Participant` carries plugin-specific settings under one opaque `options:` map rather than a flat field per kind, and this plugin reads its own keys out of that map (core's YAML loader has no idea what `cols` means). PTY options take `command`/`cols`/`rows`; SSH options take `host`/`port`/`user`/`key_path`/`known_hosts`/`insecure_no_host_check`. SSH fields resolve participant-supplied `options` first, falling back to the persona's freeform `app.ssh` block; **no SSH password is ever read from a scenario** (scenarios are persisted files — key-based / `known_hosts` auth only).

`TerminalScenarioAdapter` (`scenario.py`) implements only the mandatory `resolve_participant` — nothing else. Core derives what a kind can do in a scenario from which optional protocols its adapter satisfies (`SupportsMacros` / `SupportsSync` / `SupportsDialogPolicy` / `SupportsMockRoutes`), not from a declared string, so this is the real, current behavior rather than a placeholder: **terminal cannot run macros, wait-for-sync, set a dialog policy, or install mock routes in a scenario, today.** A terminal participant declaring `startup_macros` is therefore a validation error, and `run_macro`/`wait_for_sync` against it report the missing capability by name (e.g. "does not support macros (its adapter provides no run_macro)"). Starting a scenario with a `kind: terminal` participant when this plugin isn't enabled fails scenario validation with an "unsupported kind" error that names `OCTOWRIGHT_PLUGINS` as the fix, rather than failing obscurely deeper in the launch path.

Example: `examples/scenarios/browser-plus-terminal.yaml` in the octowright repo root.

## Telemetry

Emitted only when `PROVIDE_TRACE_ENABLED=true` / `PROVIDE_METRICS_ENABLED=true` (see the main repo's AGENTS.md "Telemetry (OpenTelemetry)" for the export setup — this plugin uses the same `octowright._tracing` helpers core does).

**Spans** (attributes `connector_type`, `instance_id`):

| Span | Emitted by |
|---|---|
| `octowright.terminal.launch` | `TerminalEngine.start` |
| `octowright.terminal.close` | `TerminalEngine.stop` |
| `octowright.terminal.send_input` | `TerminalEngine.send_input` |

**Metrics:**

| Instrument | Type | Labels | Description |
|---|---|---|---|
| `octowright_terminal_launched_total` | counter | `connector_type` | Terminal sessions launched, counted after a successful `engine.start()`. |
| `octowright_terminal_closed_total` | counter | `connector_type` | Terminal sessions ended, counted once per session whichever path got there first (explicit close or poll-loop EOF). |

`connector_type` is bounded to `pty` / `ssh` / `telnet`.

## Development

This package is a `uv` workspace member of the octowright repo, but it lives in its own `terminal` dependency group rather than in `dev`. The group *is* the dependency boundary: core's CI legs sync `--all-groups --no-group terminal` to prove core builds, installs and passes with no uterm present anywhere (the dependency-layer twin of `tests/test_plugin_isolation.py`), and `make install` / `--all-groups` is how a dev working here opts in. `uv sync` on its own (default groups only) does **not** install it — and uninstalls it if it was there; ask for the group, or for every group:

```bash
uv sync --active --all-groups                      # or: --group terminal
uv run --active --no-sync mypy src/octowright packages/octowright-terminal/src tests/plugins/reference
uv run --active --no-sync pytest packages/octowright-terminal/tests -v --no-cov
```

`make test-terminal` from the repo root runs `ci/run_terminal_plugin_tests.sh` — the same availability guard CI uses, then that suite, then the tool-surface check — rather than the bare pytest line, for the reason below.

The `terminal` pytest marker is auto-applied to everything under `packages/octowright-terminal/tests/` (see the root `pyproject.toml`). Those tests import uterm-backed modules, so the suite ignores itself at collection when uterm is absent (a core install, or a checkout that has not synced the `terminal` group) — which is right for a core install and dangerous for a gate, since pytest then reports a clean pass over zero tests. CI therefore runs this suite in a dedicated `terminal-plugin` job — the one job that syncs the `terminal` dependency group — which asserts uterm is importable *before* pytest starts, so an absent dependency fails loudly instead of passing over zero tests. That job also carries the two checks that mean nothing without uterm installed: mypy over this package (the lint job's mypy runs without uterm and types every `provide.uterm` symbol as `Any`) and pip-audit over the group's dependency tree (the lint job's audit never sees those packages).
