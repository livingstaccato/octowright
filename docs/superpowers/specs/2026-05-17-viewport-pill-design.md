# Viewport Pill Design

## Summary

Add a dedicated viewport-status pill to Octowright browser pages so agents and users can see whether a session is using a fluid OS-window-tracking viewport or a fixed Playwright viewport. The pill should also expose safe corrective actions for fixed sessions whose canvas or layout no longer follows manual window resizing.

## Goals

- Make fixed versus fluid viewport mode visible in headed browser sessions.
- Warn when a fixed viewport appears mismatched from the surrounding browser window.
- Keep the overlay from interfering with the page under test.
- Provide two Alt-click actions:
  - Sync once: resize the Playwright viewport to the current effective window/page size.
  - Relaunch fluid: recreate the browser session with `no_viewport=True` where possible.
- Preserve deterministic fixed-viewport behavior for headless sessions and explicit viewport launches.

## Non-Goals

- Do not auto-relaunch sessions without user action.
- Do not force all fixed sessions into fluid mode; fixed viewports remain valid for tests, recordings, screenshots, and reproducibility.
- Do not make the pill a general layout debugger. It reports viewport mode and offers targeted correction only.

## Current Context

Octowright currently decides viewport mode during launch:

- Headed sessions with no explicit `viewport_w` or `viewport_h` use `no_viewport=True`, which lets the page track the native window.
- Headless sessions and explicit-size launches use Playwright `viewport={width, height}`, which pins `window.innerWidth` and `window.innerHeight`.
- `browser_resize` calls `page.set_viewport_size(...)`, which changes the page viewport but does not resize the native OS window.

This split explains the observed canvas behavior: a browser window can be manually resized while the page viewport remains fixed, so a canvas game may not redraw to the new window dimensions.

Octowright already injects page overlays through browser-context init scripts:

- title tag
- corner identity badge
- macro status pill

The viewport pill should follow this existing pattern.

## UX Design

Use a small dedicated viewport pill, separate from the identity badge and macro pill.

States:

- `fluid`: green, text like `viewport · fluid`
- `fixed`: gray, text like `viewport · fixed 1280x800`
- `fixed mismatch`: amber, text like `viewport · fixed mismatch`

Visibility:

- Fixed sessions show the pill persistently.
- Fluid sessions show the pill briefly after launch and after state changes, then fade.
- Fixed sessions turn amber when the page viewport appears out of sync with the outer window size.

Interaction:

- The pill is click-through by default with `pointer-events: none`.
- Holding Alt makes the pill interactive, matching the macro pill convention.
- Alt-click opens a compact modal.

The modal shows:

- viewport mode: fixed or fluid
- page viewport size from `window.innerWidth` and `window.innerHeight`
- outer window size from `window.outerWidth` and `window.outerHeight` when available
- current fixed viewport size if known
- actions: `Sync once`, `Relaunch fluid`, `Close`

## Behavior

### Default Launch Policy

- Headed interactive launches default to fluid when the caller does not provide `viewport_w` or `viewport_h`.
- Headless launches stay fixed.
- Explicit viewport launches stay fixed.
- Recording/video/test paths that intentionally provide viewport dimensions keep fixed behavior.

### Sync Once

`Sync once` updates the Playwright viewport for the live page to match the current effective browser content size.

Implementation should prefer a measured browser/page value that is stable across engines. If outer-window measurements are too browser-frame-dependent, the first implementation can sync to the largest reliable page-derived size and clearly report the result in the modal.

After sync, the session remains fixed. The pill should update to gray if the fixed viewport now matches, or amber if it still appears mismatched.

### Relaunch Fluid

`Relaunch fluid` closes and relaunches the browser context with `no_viewport=True`.

It should preserve where feasible:

- engine kind
- current URL
- label
- profile or session-scoped user-data-dir mode
- badge/stabilize/trace/HAR intent when safe

The action is disruptive and should be explicit in the modal. It may create a new `instance_id`; the response should make that clear.

## Architecture

Add a new viewport-status init script under `src/octowright/browser_pool/_assets/`, parallel to `macro_pill.js`.

Add Python wiring in `browser_pool/visuals.py` to inject the viewport script with static launch metadata:

- initial viewport mode: `fluid` or `fixed`
- fixed dimensions when known
- instance id
- label/profile chip data if useful for modal context

Track viewport mode in the session model or launch metadata so MCP tools and HTTP relaunch paths can report and preserve it. The model should distinguish:

- `fluid`: context was launched with `no_viewport=True`
- `fixed`: context was launched with Playwright viewport dimensions
- `unknown`: legacy or malformed recording/session data

Expose backend actions through MCP tools and matching HTTP endpoints:

- `browser_viewport_status(instance_id)`
- `browser_viewport_sync(instance_id)`
- `browser_relaunch_fluid(instance_id)`

The page overlay cannot directly call MCP. It should either:

- expose intent through a page binding registered by Octowright, or
- use existing dashboard/HTTP routes if available and safe for loopback-only local control.

Prefer a page binding if it keeps the action scoped to the owning session and avoids exposing new unauthenticated browser-control endpoints beyond the existing local dashboard surface.

## Error Handling

- If dimensions cannot be measured, the modal should show `unknown` and disable `Sync once`.
- If relaunch cannot preserve state, report the reason and do not close the original browser.
- If sync fails, keep the modal open and show the error in a bounded message.
- If the page removes the overlay, the MutationObserver should re-add it, following the badge pattern.
- If the page is inside an iframe, do not inject or render the pill; top-level pages only.

## Testing

Unit tests:

- viewport launch metadata is `fluid` for headed launches without explicit viewport
- viewport launch metadata is `fixed` for headless launches
- viewport launch metadata is `fixed` for explicit `viewport_w` or `viewport_h`
- viewport pill script is injected with the correct metadata
- `browser_viewport_status` reports mode and dimensions
- `browser_viewport_sync` calls `page.set_viewport_size(...)`
- `browser_relaunch_fluid` preserves URL/profile/label and returns the new instance id

Browser/integration tests:

- launch headed fluid page and verify `window.innerWidth` changes after native window resize where Playwright supports it
- launch fixed viewport page and verify manual window resize does not change `window.innerWidth`
- verify fixed mismatch pill state can be produced
- verify Alt-click modal opens without intercepting normal page clicks
- verify sync once changes `window.innerWidth`/`innerHeight`
- verify relaunch fluid closes the old session and opens a new fluid session

Regression tests:

- macro pill still works and keeps its Alt-click behavior
- identity badge still renders and remains click-through
- existing explicit-viewport replay/export behavior remains fixed and deterministic

## Open Implementation Notes

The relaunch path should reuse existing handoff/relaunch logic where possible, but it likely needs one explicit launch option: force `no_viewport=True` by omitting viewport dimensions and marking the session as fluid. If current relaunch code reconstructs viewport dimensions from recordings by default, it must preserve the distinction between "no viewport was set" and "default viewport dimensions were implied."

The first implementation should keep auto-sync out of scope. Fixed sessions should warn and offer actions, not continuously mutate viewport size behind the user's back.
