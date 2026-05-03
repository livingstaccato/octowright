# Engines

Octowright supports Playwright engines:

- `chromium`
- `firefox`
- `webkit`

## Install and Verify

Install browser binaries:

```bash
uv run playwright install webkit firefox chromium
```

Tool-level checks:

- `browser_engine_status`
- `browser_engine_install`
- `browser_engine_reinstall`

Use these before blaming higher-level macro/scenario logic for launch failures.

## Launch Mode Semantics

- Headed/headless default is environment-driven (`OCTOWRIGHT_HEADLESS` override supported).
- In headed mode with no explicit viewport, sessions use `no_viewport=True` so page size tracks OS window resizing.
- If viewport dimensions are explicitly set, Playwright uses those dimensions.

## Handoff and Mixed Flows

`browser_handoff` exists for transitioning from automated flow to human takeover workflows.

Operationally, when you want "headless prep then headed continue", do it as:

1. Run prep macro/session in one launch mode.
2. Persist state via persona/profile.
3. Launch a second session in the desired mode with the same persona/profile.

This preserves practical continuity through profile state rather than mutating one running browser process from headless to headed.
