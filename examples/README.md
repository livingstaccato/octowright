# octowright examples

Demo macros and scenarios that work offline (everything points at `about:blank`
with injected HTML — no external network required).

## Usage

Tell octowright to load these instead of the user-config defaults by setting:

```bash
export OCTOWRIGHT_MACROS_DIR="$(pwd)/examples/macros"
export OCTOWRIGHT_SCENARIOS_DIR="$(pwd)/examples/scenarios"
```

Or inside an MCP session, the tools that read these dirs will pick them up
automatically when those env vars are set on the server process.

## Macros

| Name | Kind | What |
|---|---|---|
| `inject-form` | utility | Drops a tiny `<form>` with `#user` / `#pass` / `#submit` into the current page body. Reused by other demos. |
| `[test]assert-arithmetic` | test | Trivial smoke: `2 + 2 === 4` via `expect_js`. |
| `[test]injected-form-fill` | test | Inject a form, fill two inputs, assert values via `expect_js`. No external network. |
| `[test]click-counter` | test | Inject a button with an inline counter, click 3×, assert `data-n === "3"`. |
| `[test]selector-presence` | test | Inject HTML, then `expect_selector` + `expect_text`. |
| `[test:smoke]page-ready` | tagged-test | Tagged with `smoke`. Asserts `window` / `document` / `document.body` exist. |
| `discord-style-login` | parameterized | Demo login flow with `{{email}}` / `{{password}}` substitution against an injected mock form. NOT a real Discord macro — selectors are demo-only. |

Run a single macro:
```bash
# In an MCP session
macro_run instance_id=<id> name=[test]click-counter
```

Run all `[test]` macros as a suite:
```bash
octowright test examples/macros
```

Run only `[test:smoke]`-tagged tests:
```bash
octowright test examples/macros --tag smoke
```

## Scenarios

| File | Participants | Why |
|---|---|---|
| `solo.yaml` | 1 webkit | Smallest scenario — verifies start/stop plumbing. |
| `duo.yaml` | 2 webkit | Demonstrates `role` filtering (`player` / `monitor`). |
| `cross-engine.yaml` | 1 chromium + 1 firefox + 1 webkit | Cross-engine smoke. Has a `verify:` mapping for `--test` mode. |
| `with-fixtures.yaml` | 2 webkit | Shared `mock_routes` + `dialog_policy` fixtures applied to every participant. |
| `verify-suite.yaml` | 3 webkit (one per role) | Each role has its own verify macro. Runs as a JUnit-emitting test suite under `--test`. |
| `seven-mix.yaml` | 9 instances (7 players + monitor + spectator) | The "Discord raid" shape from the design spec. Engines distributed across webkit/firefox/chromium. |

Start a scenario interactively (browsers stay open until Ctrl-C):

```bash
octowright scenario start solo
```

Run a scenario as a test suite (verify macros required):

```bash
octowright scenario start verify-suite --test --out report.xml
echo "exit=$?"
cat report.xml
```

Or via MCP:

```
scenario_start name=cross-engine
scenario_run_macro scenario_id=<id> macro=[test:smoke]page-ready
scenario_stop scenario_id=<id>
```

## Notes on offline use

Every example uses `url: about:blank` and injects HTML via `evaluate` so the
demos run without any outbound network. Replace those URLs with real targets
when you're ready. The `with-fixtures.yaml` scenario also installs route
mocks for `**/api/time` + `**/api/health` to show how shared fixtures fan out
across participants.
