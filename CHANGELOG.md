# Changelog

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
