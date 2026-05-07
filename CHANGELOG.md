# Changelog

## Unreleased

### Added
- In-page macro status pill: bottom-centered, faint translucent overlay shows the
  ID chip (matches corner-badge color), live elapsed counter, and current action
  description. Stays visible after a macro finishes (frozen elapsed + `done`/`failed`
  status); next macro's `start` push resets the counter.
- Alt-modifier click-through: holding Alt makes the pill clickable; click opens a
  themed run-history modal listing every push with timestamps. Modal dismisses via
  X button, backdrop click, or Esc.
- `slowmo_ms` parameter on `macro_run`, `macro_run_sequence`, and `run_macro` for
  per-action delay; defaults from `OCTOWRIGHT_MACRO_SLOWMO_MS`. Sleep happens after
  the pill status push and before dispatch so the pill reflects the upcoming action.
- `run_macro` return value now includes `slowmo_ms` and `elapsed_s`.
- `examples/pill-status-demo/` — runnable headed walkthrough (macro JSON, probe
  page, runner script).

### Internal
- Extracted browser-pool init scripts (title-tag, corner badge, macro pill) to
  standalone `.js` files under `src/octowright/browser_pool/_assets/`. Loaded once
  at import; shipped in the wheel.
- File-size discipline: `visuals.py` 752→216 LOC; split `tests/test_badge.py` into
  `test_badge.py` + new `tests/test_pill.py`.

## 0.3.0 - 2026-05-03

### Added
- Distributed skill-pack support with packaged `using-octowright` assets.
- `octowright skill install`, `octowright skill status`, and `octowright skill doctor` CLI commands.
- Session cache reporting in session detail/close responses.
- ARIA-first macro playback with semantic-first fallback behavior.
- Browser handoff workflow for stateful headless/headed transitions.
- HAR capture plumbing and related browser/session tool support.

### Improved
- Websocket binary payload handling for timeline rendering and cache safety.
- Markdown cache capture and retrieval flow for session debugging.
- CI checks for wheel/sdist artifact integrity and skill CLI smoke coverage.

### Internal
- Version sync guardrail tests and release-readiness docs cleanup.
- File-size enforcement refactors: split runtime/support modules (`pool_support`, `pool_roster`, `macros_runtime`, session mixins/protocols) to keep modules idiomatic and below LOC caps.
