# CI and Quality

Use these commands as the release gate for this repository.

## Local Quality Gate

```bash
make lint
make test
```

`make lint` runs ruff lint/format checks, `mypy`, scoped `ty` checks for `src/octowright/http`,
`bandit` security checks, codespell, and SPDX header validation.

`ty` is intentionally scoped to `src/octowright/http` in CI while broader-package baseline diagnostics
outside changed modules are being worked down. Use this non-gating probe command to assess expansion:

```bash
make typecheck-ty-probe
```

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
