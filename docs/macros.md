# Macros

A **macro** is a named, parameterizable action sequence derived from a recording.
Capture a flow once (e.g. a login), replay it later — possibly with different
arguments, possibly across many participants in a scenario.

Macros live as JSON under the Octowright config dir: POSIX uses the XDG config
dir `${XDG_CONFIG_HOME:-~/.config}/octowright/macros/`, and Windows uses
`%APPDATA%\octowright\macros\`. Override with `OCTOWRIGHT_MACROS_DIR`.

The macro tools (`macro_save`, `macro_run`, `macro_run_sequence`, `macro_list`,
`macro_delete`, `macro_lint`, `macro_repair_preview`, `macro_compile`,
`macro_explain`) belong to the `macros` capability profile. By default every
tool registers; if your operator runs `octowright serve --profile=core`, add
`macros` to the spec (`--profile=core,macros`) to keep these visible. See
[getting-started.md](getting-started.md#slimming-the-llm-tool-surface).

## When to use a macro

- **Login flows** that you want to re-run across multiple personas or sessions.
- **Setup steps** that put a page into a known state before exploration or testing.
- **Verification probes** tagged `[test]` that the test runner can execute as a suite.

When NOT to use a macro: durable production automation. Macros break when the
target site rewrites its DOM (Discord especially loves to rotate CSS classes).
Treat them as short-term automation — when one breaks, **re-record rather than
hand-patch**.

## Recording → save → replay

The canonical workflow:

```bash
# 1. Manually perform the flow on a live instance.
browser_launch kind=webkit profile=disc-1 url=https://discord.com/login
# ... fill email, password, submit ...

# 2. Snapshot those actions as a macro. Tell Octowright which literal values
#    should be parameters.
macro_save instance_id=<id> name=discord-login \
           parameters={"email":"me@example.com","password":"hunter2"}

# 3. Replay later, against any instance, with any args.
browser_launch kind=webkit profile=disc-2 url=https://discord.com/login
macro_run instance_id=<new-id> name=discord-login \
          args={"email":"other@example.com","password":"correcthorsebatterystaple"}
```

Lifecycle actions (`launch`, `close`, `snapshot`) are dropped by default — macros
are the **reusable middle** of a flow, not the wrapper. Pass `include_launch=True`
on `macro_save` if you need the initial navigation baked into the macro.

Recorded CSS `click` and `fill` actions may include semantic metadata such as
`role`, `role_name`, `label`, `text`, or `test_id`. Macro replay treats those as
ARIA-first hints: it tries `click_by` / `fill_by` with the semantic metadata,
then falls back to the recorded CSS selector if the semantic locator fails.
Standalone Python and TypeScript exports follow the same order.

## Conditional / branching actions

For sites that ship multiple DOM versions of the same flow, four action types
let one macro cover all of them. Hand-author these by editing the JSON directly;
record the linear baseline first, then wrap the fragile steps.

### `if_selector`

Predicate on selector presence; runs `then` or `else`:

```json
{"action": "if_selector", "selector": ".cookie-banner", "present": true,
 "then": [{"action": "click", "selector": ".accept-cookies"}]}
```

### `try`

Best-effort sub-sequence that **suppresses errors**. Use for optional steps
like dismissing a one-off banner that may or may not exist:

```json
{"action": "try", "actions": [
    {"action": "click", "selector": "#optional-popup-close"}
]}
```

### `try_each`

Branches in order; succeeds on the first whose every action completes; raises
if all fail. The "v1 OR v2 OR v3" hammer:

```json
{"action": "try_each", "branches": [
    [{"action": "click", "selector": "[aria-label='Close']"}],
    [{"action": "click", "selector": "button.dismiss"}],
    [{"action": "press_key", "key": "Escape"}]
]}
```

These nest freely — `if_selector` inside `try_each` inside `try` works as you
would expect. See `examples/macros/conditional-discord-modal-dismiss.json` for a
real-world pattern.

### `macro_call`

Call another saved macro from inside a macro. This keeps large branches readable
and lets shared setup/dismissal snippets stay reusable:

```json
{"action": "macro_call", "name": "dismiss-cookie-banner",
 "args": {"variant": "compact"}}
```

Octowright detects direct and mutual recursion (`a -> b -> a`) and enforces a
depth cap so a bad macro graph fails with a clear error instead of looping.

## YAML DSL

JSON remains the runtime/storage format, but `macro_compile` can compile a
friendlier YAML document into canonical macro JSON. Dry-run first:

```bash
macro_compile yaml_text='
name: login-smoke
parameters: [email, password]
actions:
  - navigate: "https://example.com/login"
  - fill: {selector: "#email", value: "{{email}}"}
  - fill: {selector: "#password", value: "{{password}}"}
  - try_each:
      branches:
        - [{click: "button[type=submit]"}]
        - [{press_key: Enter}]
' write=false
```

Pass `write=true` to save the compiled JSON under the normal macro directory.
The dashboard editor also edits the canonical JSON shape and shows branch
summaries for conditionals.

## Linting

Before promoting a macro into shared workflows or CI, run:

```bash
macro_lint name=discord-login
```

The linter catches:

- Missing required fields on each action.
- Unknown action types (typos).
- **Unparameterized credential-shaped strings** (looks like a password or token
  but isn't a `{{parameter}}`) — the most common security mistake.
- Empty conditional branches that would silently no-op.

## Test suite mode

Macros tagged `[test]` (in either `name` or `description`) participate in the
test runner. The runner emits JUnit XML so the result drops cleanly into any CI
reporting pipeline.

```bash
uv run octowright test [path] --kind webkit --tag smoke --out dist/macro-tests.xml
```

Equivalent MCP tool: `run_test_suite`.

## Watching execution

Every page rendered by a launched browser gets a faint **status pill** injected at
the bottom-center. While a macro runs, the pill shows:

```
[ <id-chip> ]  <elapsed>  ·  <macro-stack> | <action description>
```

- The ID chip color matches the corner badge for the same browser.
- The elapsed counter ticks live (~10Hz) and freezes on completion.
- After a macro finishes the pill stays visible with `<name> | done` (or
  `| failed`) until the next macro starts or `visible: false` is pushed.
- The pill is `pointer-events: none` by default — clicks fall through to the page.

**Alt-click** (Option-click on Mac) the pill to open a themed run-history modal
listing every push for the run with timestamps. Dismiss with the X button, by
clicking the dimmed backdrop, or by pressing Esc.

To slow execution down so you can follow along by eye, pass `slowmo_ms`:

```bash
macro_run instance_id=<id> name=discord-login slowmo_ms=800
```

Or set the default for a session via `OCTOWRIGHT_MACRO_SLOWMO_MS=800`. The pause
happens between the status push and the action dispatch, so the pill always
reflects the upcoming action while you have time to read it. The headed walkthrough
under `examples/pill-status-demo/` shows this end-to-end.

## Tools

| Tool | Purpose |
|---|---|
| `macro_save` | Snapshot a recording into a named macro JSON. |
| `macro_list` | Enumerate all saved macros. |
| `macro_run` | Replay a single macro against a live instance. |
| `macro_run_sequence` | Replay several macros in order on the same instance. |
| `macro_compile` | Compile YAML macro DSL to canonical JSON; optionally save it. |
| `macro_delete` | Remove a saved macro file. |
| `macro_lint` | Static-analysis pass on a saved macro. |
| `run_test_suite` | Execute every `[test]`-tagged macro in a directory; emit JUnit XML. |

## Related

- [personas.md](personas.md) — `default_macros` runs after launch (typical use: auto-login).
- [scenarios.md](scenarios.md) — `scenario_run_macro` broadcasts a macro across participants.
- [troubleshooting.md](troubleshooting.md#scenariomacro-failures) — when a macro stops finding selectors.
