# CI and Quality

Use these commands as the release gate for this repository.

## Local Quality Gate

```bash
make lint
make test
```

`make lint` runs ruff lint/format checks, `mypy`, scoped `ty` checks for `src/octowright/http`,
`bandit` security checks, codespell, SPDX header validation, and `detect-secrets` against
`.secrets.baseline`. It then runs the repo's own guard scripts, each of which exists because
something drifted silently once:

| Guard | Fails when |
|---|---|
| `check_max_loc.py` | any Python file exceeds 777 lines. |
| `check_operation_gate_architecture.py` | Playwright is reached outside the session operation gate. |
| `check_agent_docs_sync.py` | `CLAUDE.md` is not a byte-for-byte copy of `AGENTS.md`. |
| `check_telemetry_docs.py` | an emitted metric or MCP notification is undocumented in `AGENTS.md`. |
| `check_tool_inventory_docs.py` | a tool count or list in `docs/architecture/mcp-tool-inventory.md`, its PlantUML diagram, `README.md` or `docs/getting-started.md` disagrees with the live registry. |
| `check_mutmut_selection.py` | a test that covers a mutated module is missing from the mutmut selection. |
| `check_vulture.py` / `check_xenon.py` | dead code or cyclomatic complexity rises above the committed baseline. `check_vulture.py` runs **two** passes: the original at 80% confidence over `src/` and `tests/`, plus one at 60% over the same paths that reports only `src/` findings and only unused functions/methods/classes. The second exists because vulture scores an unused callable at 60%, so the 80% gate structurally could never report one -- it saw unused imports (90%) and unreachable code (100%) and nothing else, and a dead module-level function was committed through a green gate. Scanning tests too (while reporting only `src/`) is what keeps a helper used solely by a test from being called dead: 38 findings src-only against 9 with tests included. Decorators that register a callable without naming it (Click commands, MCP tools, fixtures) are ignored, being false positives by construction. **Both gates ratchet in two directions:** a NEW finding fails, and a STALE baseline entry -- one whose finding no longer occurs -- also fails, because leaving it behind silently pre-approves the next regression in that same place. Adding that check pruned 5 of xenon's 20 entries immediately. Baseline entries carry no line number, so an edit *above* a baselined finding does not shift it into looking new; the vulture pass shipped without that and one added line failed the gate. Staleness is only checked on a full scan, since a narrowed `--paths` would make every unscanned entry look stale. |

`ty` is intentionally scoped to `src/octowright/http` in CI while broader-package baseline diagnostics
outside changed modules are being worked down. Use this non-gating probe command to assess expansion:

```bash
make typecheck-ty-probe
```

### Pre-commit hook stages

`.pre-commit-config.yaml` sets `default_stages: [pre-commit, pre-push]`. A hook
that declares no `stages:` of its own runs at **every** stage, so a single
`git commit` ran the whole battery twice — once at `pre-commit` and again at
`commit-msg`, vulture, xenon and detect-secrets included. `commitlint`
(`[commit-msg]`) and `pytest-quick` (`[pre-push]`) declare their own stages and
are unaffected.

Note the hooks only run if they are actually installed: a `core.hooksPath`
pointing elsewhere (a global hooks directory, for instance) leaves
`.git/hooks` empty and none of this config ever executes.

## CI Parity with `act`

```bash
make act-lint
make act-test
```

Additional targets:

- `make ci`

Notes:

- `act` paths are slower on Apple Silicon due to amd64 emulation.
- Under `ACT=true`, some live-browser-heavy tests are excluded by CI config for local parity and stability.

## Local Playground Integration Lane

Run the local-server-backed suite (same marker used by CI):

```bash
uv run pytest -q tests/ -m integration_local --no-cov
```

Optional local host alias:

```bash
# /etc/hosts
127.0.0.1 test.octowright.com
```

Then run the same suite against that host:

```bash
OCTOWRIGHT_TEST_BASE_URL=http://test.octowright.com uv run pytest -q tests/ -m integration_local --no-cov
```
