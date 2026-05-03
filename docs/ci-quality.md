# CI and Quality

Use these commands as the release gate for this repository.

## Local Quality Gate

```bash
make quality
make test
```

`make quality` runs linting, typing, security checks, license checks, complexity checks, and coverage baseline validation.

## CI Parity with `act`

```bash
make act-all
```

Additional targets:

- `make act-smoke`
- `make act-quality`
- `make act-tests`

Notes:

- `act` paths are slower on Apple Silicon due to amd64 emulation.
- Under `ACT=true`, some live-browser-heavy tests are excluded by CI config for local parity and stability.
