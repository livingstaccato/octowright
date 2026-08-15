# Engines

Octowright drives all three Playwright browser engines, side-by-side, in the
same session:

- `chromium` — Chrome / Edge engine family.
- `firefox` — Mozilla's engine.
- `webkit` — the upstream WebKit engine that ships with Playwright (see the
  [Safari caveat](#safari-caveat) below).

The engine is chosen per-launch via the `kind` argument to `browser_launch`.

## Install and verify

Install the engine binaries that match your installed Playwright version:

```bash
uv run playwright install webkit firefox chromium
```

When a launch fails immediately, suspect the engine binary first. Today
engine management is CLI-driven (`playwright install`, `playwright install --list`);
Octowright does not currently expose dedicated MCP tools for install/reinstall.

## Custom channel / binary / launch flags

`browser_launch` takes three optional launch-time-only params for cases where
the bundled Playwright binary isn't what you want:

- `channel` — use a real installed browser channel instead of Playwright's
  bundled binary (`chrome`, `chrome-beta`, `chrome-dev`, `chrome-canary`,
  `msedge`, `msedge-beta`, `msedge-dev`, `msedge-canary`). Unknown channel
  strings are rejected at launch with a clear error.
- `executable_path` — point at a specific browser binary on disk. Validated
  to exist at launch time (a fast, clear failure instead of an opaque
  Playwright error).
- `launch_args` — extra CLI flags appended after Octowright's own internal
  chromium args (new-tab extension, tiling, `--disable-dev-shm-usage`), so a
  user-supplied flag can deliberately override one of those if it conflicts.

All three apply to every engine (`chromium`/`firefox`/`webkit`). They are
**launch-time only**: never written to the JSONL recording, never read back
from a saved launch record, and never carried across handoff/relaunch — a
poisoned recording can't turn into an `executable_path` code-execution
primitive, and replay can't silently weaken sandboxing via `launch_args`.

## Launch mode (headed vs headless)

Mode is environment-driven, with one explicit override:

| Condition | Mode |
|---|---|
| `OCTOWRIGHT_HEADLESS=1` | headless (forced) |
| `OCTOWRIGHT_HEADLESS=0` | headed (forced) |
| `CI=true` | headless |
| Linux without `$DISPLAY` / `$WAYLAND_DISPLAY` | headless |
| macOS, or Linux with a display | headed (default) |

In **headed mode with no explicit viewport**, sessions launch with
`no_viewport=True` so the page tracks the OS window as it's resized.
If `viewport_w` / `viewport_h` are explicitly set (or `OCTOWRIGHT_VIEWPORT_W`
/ `OCTOWRIGHT_VIEWPORT_H` are set in the environment), Playwright honors those
dimensions instead.

## Mixed-mode flows (handoff)

For "headless prep, then headed continue" workflows, *don't* try to mutate a
single running browser between modes. Instead:

1. Run prep in one launch mode against a persona/profile.
2. Close that browser (state flushes to disk).
3. Launch a second browser against the same persona/profile in the desired mode.

The persona/profile preserves practical continuity (cookies, localStorage,
service workers) without requiring Octowright to mutate a running Playwright
process.

The `browser_handoff` behavior is implemented through close/relaunch semantics:
preserve state by reusing the same profile between launches instead of mutating
an existing Playwright process.

## Safari caveat

Playwright's `webkit` channel is the **bundled upstream WebKit engine**, not
Apple's Safari.app. The two share an engine family but are separate binaries.
Driving real Safari.app with your cookies/profile would require Apple's
`safaridriver`, which Playwright does not support today.

This rarely matters in practice — the upstream WebKit binary tracks Safari
closely enough for site-compatibility testing.

## Related

- [troubleshooting.md](troubleshooting.md#engine-launch-failures) — diagnosis
  flow when a launch fails immediately.
- [personas.md](personas.md) — each persona can hold profiles across all three
  engines independently.
